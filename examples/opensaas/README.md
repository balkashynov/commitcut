# A pinned upstream helper, through real Prisma

This harness imports [`processedWebhookEvent.ts`](vendor/processedWebhookEvent.ts) **without source changes** from [OpenSaaS commit 925602ce](https://github.com/wasp-lang/open-saas/blob/925602ceafb5a0988a3b2ba72d754f022f121699/template/app/src/payment/processedWebhookEvent.ts), the head of merged [PR #732](https://github.com/wasp-lang/open-saas/pull/732). Its MIT license is retained in [vendor/LICENSE](vendor/LICENSE).

The helper claims an event, invokes a callback, and removes the claim if the callback throws. A process crash cannot execute that catch block. If the claim already committed, a fresh process recognizes the retry as already handled even though its fulfillment callback never completed.

## What runs

- Exact upstream helper, transpiled by tsx. Its `wasp/server` import is only used as a TypeScript type and is erased; no fake runtime implementation is supplied.
- Real generated Prisma 5.19.1 client and PostgreSQL protocol. The processed-event model matches the relevant upstream fields and uniqueness constraint.
- Official Stripe 18.1.0 SDK signature verification, the version pinned by the inspected OpenSaaS application manifest.
- A small HTTP dependency harness, a synthetic `invoice.paid` fixture and an `Account` credit callback.
- Actual worker process-group SIGKILL, fresh process startup, two signed redeliveries, and independent SQL assertions.

The harness is **not the complete Wasp application**, does not exercise its full invoice lookup/mapping pipeline, and does not contact Stripe. The simplified credit callback and account schema are ours. The finding establishes the helper's behavior under this concrete callback and process-failure model, not the status of every downstream deployment.

`transactional-control.toml` uses our transactional claim-and-credit implementation as a negative control. It does not silently alter the upstream helper or present the control as a ready upstream patch. External effects such as email need a separate reliable handoff, and a database transaction alone does not make them exactly once.

## Run

From the repository root, with the dedicated database environment configured:

```sh
npm ci --prefix examples/opensaas
(cd examples/opensaas && DATABASE_URL="$COMMITCUT_DATABASE_URL" npx prisma generate)
PYTHONPATH=python python3 -m commitcut.cli examples/opensaas/upstream.toml --output .runs/upstream
PYTHONPATH=python python3 -m commitcut.cli examples/opensaas/transactional-control.toml --output .runs/control
```

The upstream scenario is expected to exit `1` with missing credits. The control should exit `0`. `scripts/verify-examples.py --upstream` asserts both directions and exits successfully only when the expected failure and recovery results hold.

PR #732 already describes a separate claim/processing race and its accepted scope. No maintainer endorsement, new issue, production exploit, or live-service reproduction is claimed.
