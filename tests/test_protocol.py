"""Real PostgreSQL differential tests. Explicitly enabled; never silently skipped in CI."""
import os
from pathlib import Path
import socket
import struct
import tempfile
import unittest
import urllib.parse

from commitcut.runner import Proxy, InvalidRun


def frame(tag, body=b""):
    return tag + struct.pack("!I", len(body)+4) + body


def read_exact(sock, n):
    parts = bytearray()
    while len(parts) < n:
        b = sock.recv(n-len(parts))
        if not b:
            raise EOFError("truncated frame")
        parts.extend(b)
    return bytes(parts)


class PG:
    def __init__(self, address, user, database):
        self.sock = socket.create_connection(address, timeout=3)
        body = struct.pack("!I", 196608) + b"user\0" + user.encode() + b"\0database\0" + database.encode() + b"\0\0"
        self.sock.sendall(struct.pack("!I", len(body)+4)+body)
        auth = self.until_ready()
        if any(tag == b'R' and body != b'\0\0\0\0' for tag, body in auth):
            raise RuntimeError("Raw protocol tests require local trust authentication")

    def until_ready(self):
        out = []
        while True:
            tag = read_exact(self.sock, 1)
            length = struct.unpack("!I", read_exact(self.sock, 4))[0]
            body = read_exact(self.sock, length-4)
            out.append((tag, body))
            if tag == b'Z':
                return out

    def query(self, sql):
        self.sock.sendall(frame(b'Q', sql.encode()+b'\0'))
        return self.until_ready()

    def send(self, data):
        self.sock.sendall(data)
        return self.until_ready()

    def close(self):
        try:
            self.sock.sendall(frame(b'X'))
        except OSError:
            pass
        self.sock.close()


@unittest.skipUnless(os.environ.get("COMMITCUT_PROTOCOL_TESTS") == "1", "set COMMITCUT_PROTOCOL_TESTS=1 with a dedicated trust-auth test database")
class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        database = os.environ["COMMITCUT_DATABASE_URL"]
        self.url = urllib.parse.urlsplit(database)
        class Config:
            include_sql = False
            timeout = 3
        self.config = Config()
        self.config.database = database
        self.config.proxy = os.environ.get("COMMITCUT_PROXY", str(Path(__file__).resolve().parents[1] / "target/debug/commitcut-proxy"))
        self.proxies = []
        self.clients = []

    def proxy(self, **kwargs):
        p = Proxy(self.config, log_path=self.directory / f"proxy{len(self.proxies)}.log", **kwargs)
        self.proxies.append(p)
        return p

    def client(self, proxy=None):
        address = (self.url.hostname, self.url.port or 5432)
        if proxy:
            host, port = proxy.address.rsplit(":", 1)
            address = (host, int(port))
        client = PG(address, urllib.parse.unquote(self.url.username), self.url.path[1:])
        self.clients.append(client)
        return client

    def tearDown(self):
        for client in self.clients:
            client.close()
        for proxy in self.proxies:
            proxy.kill()
        self.temp.cleanup()

    def test_simple_multistatement_error_and_aborted_commit_match_database(self):
        proxy = self.proxy()
        direct, via = self.client(), self.client(proxy)
        proxy.arm(3)
        for sql in ["SELECT 1; SELECT 'two'", "", "BEGIN", "SELECT 1/0", "COMMIT", "SELECT 42"]:
            # Error messages include only server values here and match byte-for-byte.
            self.assertEqual(via.query(sql), direct.query(sql), sql)
        proxy.verify()
        boundaries = [e for e in proxy.events if e["event"] == "boundary"]
        self.assertEqual(boundaries[3]["sqlstates"], ["22012"])
        self.assertEqual(boundaries[3]["state_after"], "E")
        self.assertEqual(boundaries[4]["commands"], ["ROLLBACK"])
        self.assertEqual(boundaries[4]["state_after"], "I")
        self.assertTrue(all("sql" not in b for b in boundaries))

    def test_prepared_statement_describe_binary_bind_and_reuse(self):
        proxy = self.proxy()
        direct, via = self.client(), self.client(proxy)
        proxy.arm(3)
        prepare = frame(b'P', b'named\0SELECT $1::int4\0'+struct.pack('!H',0)) + frame(b'D', b'Snamed\0') + frame(b'S')
        self.assertEqual(via.send(prepare), direct.send(prepare))
        for value in [7, -1234]:
            bind = b'\0named\0' + struct.pack('!HHH',1,1,1) + struct.pack('!Ii',4,value) + struct.pack('!HH',1,1)
            execute = frame(b'B', bind) + frame(b'E', b'\0'+struct.pack('!I',0)) + frame(b'S')
            self.assertEqual(via.send(execute), direct.send(execute))
        proxy.verify()
        boundaries = [e for e in proxy.events if e["event"] == "boundary"]
        self.assertEqual(len(boundaries), 3)
        self.assertEqual(boundaries[0]["query_sha256"], boundaries[1]["query_sha256"])

    def test_failed_parse_does_not_replace_existing_statement_metadata(self):
        proxy = self.proxy()
        direct, via = self.client(), self.client(proxy)
        proxy.arm(3)
        def prepare(sql):
            return frame(b'P', b'keep\0'+sql.encode()+b'\0'+struct.pack('!H',0))+frame(b'S')
        self.assertEqual(via.send(prepare("SELECT 7")), direct.send(prepare("SELECT 7")))
        self.assertEqual(via.send(prepare("SELECT 99")), direct.send(prepare("SELECT 99")))
        bind = b'\0keep\0'+struct.pack('!HHH',0,0,0)
        execute = frame(b'B',bind)+frame(b'E',b'\0'+struct.pack('!I',0))+frame(b'S')
        self.assertEqual(via.send(execute), direct.send(execute))
        proxy.verify()
        boundaries = [e for e in proxy.events if e["event"] == "boundary"]
        self.assertEqual(boundaries[1]["sqlstates"], ["42P05"])
        self.assertEqual(boundaries[0]["query_sha256"], boundaries[2]["query_sha256"])

    def test_cut_withholds_every_response_byte_after_commit(self):
        direct = self.client()
        direct.query("CREATE TABLE IF NOT EXISTS commitcut_protocol_probe (value int)")
        direct.query("TRUNCATE commitcut_protocol_probe")
        proxy = self.proxy(cut=1, phase="after")
        client = self.client(proxy)
        proxy.arm(3)
        client.sock.sendall(frame(b'Q', b'INSERT INTO commitcut_protocol_probe VALUES (7) RETURNING value\0'))
        event = proxy.wait_event("cut", 3)
        self.assertEqual(event["boundary"]["state_after"], "I")
        client.sock.settimeout(.1)
        with self.assertRaises(socket.timeout):
            client.sock.recv(1)
        response = direct.query("SELECT value FROM commitcut_protocol_probe")
        self.assertIn((b'D', struct.pack('!HI',1,1)+b'7'), response)
        direct.query("DROP TABLE commitcut_protocol_probe")

    def test_cut_inside_transaction_does_not_commit(self):
        direct = self.client()
        direct.query("CREATE TABLE IF NOT EXISTS commitcut_protocol_probe (value int)")
        direct.query("TRUNCATE commitcut_protocol_probe")
        proxy = self.proxy(cut=2, phase="after")
        client = self.client(proxy)
        proxy.arm(3)
        client.query("BEGIN")
        client.sock.sendall(frame(b'Q', b'INSERT INTO commitcut_protocol_probe VALUES (9)\0'))
        event = proxy.wait_event("cut", 3)
        self.assertEqual(event["boundary"]["state_after"], "T")
        self.assertNotIn(b'D', [tag for tag, _ in direct.query("SELECT value FROM commitcut_protocol_probe")])
        proxy.kill()
        direct.query("DROP TABLE commitcut_protocol_probe")

    def test_flush_fails_closed(self):
        proxy = self.proxy()
        client = self.client(proxy)
        proxy.arm(3)
        client.sock.sendall(frame(b'H'))
        with self.assertRaisesRegex(InvalidRun, "Flush"):
            proxy.wait_event("cut", 3)


if __name__ == "__main__":
    unittest.main()
