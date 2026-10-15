#!/usr/bin/env python3
"""Publish only synthetic reports explicitly selected from a completed matrix."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "python"))
from commitcut.report import write_html

parser = argparse.ArgumentParser()
parser.add_argument("run", help="Output from verify-examples.py --upstream")
args = parser.parse_args()
run = Path(args.run)
reports=[]
output=root/'docs/evidence'
output.mkdir(exist_ok=True)
for name in ['claim-first','effect-first','atomic','upstream','transactional-control']:
    report=json.loads((run/name/'report.json').read_text())
    if report['status'] not in ('pass','violation') or report['coverage']['inconclusive']:
        raise SystemExit('Refusing to publish incomplete evidence')
    reports.append(report)
    (output/(name+'.json')).write_text(json.dumps(report,indent=2)+'\n')
write_html(reports,root/'site/index.html')
source_paths=['native/src/main.rs','native/src/wire.rs','Cargo.lock','python/commitcut/runner.py','python/commitcut/cli.py',
              'python/commitcut/report.py','python/commitcut/report.html','examples/stripe-credits/server.mjs',
              'examples/stripe-credits/invoice.json','examples/opensaas/server.ts','examples/opensaas/vendor/processedWebhookEvent.ts']
manifest={'recorded_at_utc':datetime.now(timezone.utc).isoformat(),'platform':platform.system()+' '+platform.machine(),
          'python':platform.python_version(),'rust':subprocess.check_output(['rustc','--version'],text=True).strip(),
          'postgres':subprocess.check_output([os.environ.get('COMMITCUT_PSQL') or shutil.which('psql'),'-XAt','-d',os.environ['COMMITCUT_DATABASE_URL'],'-c','SHOW server_version'],text=True).strip(),'node':subprocess.check_output(['node','--version'],text=True).strip(),
          'source_sha256':{p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in source_paths},
          'native_binary_sha256':hashlib.sha256((root/'target/release/commitcut-proxy').read_bytes()).hexdigest(),
          'reports_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.glob('*.json')) if p.name!='manifest.json'},
          'scope':'Synthetic fixtures, real PostgreSQL, real application SIGKILL. No live Stripe account, payments, or full Wasp deployment.'}
(output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print('Exported five synthetic reports, source hashes, and standalone demo.')
