from pathlib import Path
import tempfile
import unittest
from commitcut.report import write_html


class ReportTests(unittest.TestCase):
    def test_evidence_cannot_terminate_script_element(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.html"
            attack = '</script><script>alert("oops")</script>'
            write_html([{"name": attack}], path)
            text = path.read_text()
            self.assertNotIn(attack, text)
            self.assertIn(r'\u003c/script\u003e', text)
            self.assertNotIn('__COMMITCUT_DATA__', text)


if __name__ == '__main__':
    unittest.main()
