"""Self-contained report. No CDN, external script, or uploaded evidence."""
import json
from pathlib import Path


def write_html(reports, destination):
    template = Path(__file__).with_name("report.html").read_text()
    # JSON lives in a non-executable script element. Escape HTML delimiters so
    # even adversarial SQL/assertion strings cannot terminate that element.
    payload = json.dumps({"reports": reports}, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    Path(destination).write_text(template.replace("__COMMITCUT_DATA__", payload))
