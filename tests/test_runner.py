import unittest
import urllib.parse

from commitcut.runner import InvalidRun, app_environment, validate_prefix, database_lease


class RunnerTests(unittest.TestCase):
    def test_cut_replay_rejects_query_drift(self):
        baseline = [{"index": 1, "protocol": "extended", "query_sha256": "a", "state_before": "I"}]
        events = [{"event": "begin", "boundary": dict(baseline[0], query_sha256="b")}]
        with self.assertRaisesRegex(InvalidRun, "drifted"):
            validate_prefix(events, baseline, 1)
        with self.assertRaisesRegex(InvalidRun, "count"):
            validate_prefix([], baseline, 1)
        validate_prefix([{"event": "begin", "boundary": baseline[0]}], baseline, 1)

    def test_two_runners_cannot_reset_same_database(self):
        with database_lease("postgresql://alice@localhost:54399/test_lease"):
            with self.assertRaisesRegex(InvalidRun, "Another"):
                with database_lease("postgresql://bob@127.0.0.1:54399/test_lease"):
                    self.fail("concurrent lease acquired")
        with database_lease("postgresql://alice@localhost:54399/test_lease"):
            pass

    def test_database_endpoint_preserves_encoded_credentials(self):
        class C:
            database = "postgresql://u:p%40ss%3Aword@127.0.0.1:5432/test?application_name=fixture&sslmode=require"
            data = {"app": {}}
        class P:
            address = "127.0.0.1:12345"
        result = app_environment(C, P, 34567)
        db = urllib.parse.urlsplit(result["DATABASE_URL"])
        self.assertEqual(db.netloc, "u:p%40ss%3Aword@127.0.0.1:12345")
        self.assertEqual(dict(urllib.parse.parse_qsl(db.query)), {"application_name": "fixture", "sslmode": "disable"})
        self.assertEqual(result["PORT"], "34567")


if __name__ == "__main__":
    unittest.main()
