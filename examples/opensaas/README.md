# Testing OpenSaaS's event helper

This example uses [`processedWebhookEvent.ts`](vendor/processedWebhookEvent.ts) **without changes** from [OpenSaaS commit 925602ce](https://github.com/wasp-lang/open-saas/blob/925602ceafb5a0988a3b2ba72d754f022f121699/template/app/src/payment/processedWebhookEvent.ts), the head of merged [PR #732](https://github.com/wasp-lang/open-saas/pull/732). Its MIT license is kept in [vendor/LICENSE](vendor/LICENSE).

The helper saves a row for the event, then calls a function to do the work. If that function throws an error, the helper deletes the row. But a crashed process cannot run that cleanup code. If the row was saved before the crash, the next process sees it and skips the retry, even if the work never finished.

In our example, that work is adding 10 credits to an account. After some crashes, the customer still has 0 credits even though both retries return HTTP 204.

## What we ran

- The unchanged OpenSaaS helper, run with tsx. Its `wasp/server` import is used only as a TypeScript type and removed during compilation. We did not replace it with a fake Wasp server.
- A real Prisma 5.19.1 client and PostgreSQL database. The event table has the relevant fields and unique constraint from OpenSaaS.
- Stripe SDK 18.1.0 to check webhook signatures, matching the version listed by the inspected OpenSaaS app.
- A small HTTP server, a made-up `invoice.paid` event, and our own function to add credits to an `Account` row.
- A real `SIGKILL` of the app process group, a fresh app process, two retries of the same signed webhook, and SQL queries to check the saved data.

We did not run the whole Wasp application, its invoice lookup code, or a live Stripe payment. The small server, account table, and credit-adding function are ours. This shows how the helper behaves in this test; it does not establish what happens in every app that uses it.

## The version that saves both changes together

`transactional-control.toml` tests our replacement. It records the event and adds credits in one database transaction. It is separate from the unchanged helper. It is not a proposed OpenSaaS patch.

A database transaction can save these two database changes together. It cannot undo an email or a call to another service. Those need separate handling and tests.

## Run the examples

From the repository root, after setting up the local test database:

```sh
npm ci --prefix examples/opensaas
(cd examples/opensaas && DATABASE_URL="$COMMITCUT_DATABASE_URL" npx prisma generate)
PYTHONPATH=python python3 -m commitcut.cli examples/opensaas/upstream.toml --output .runs/upstream
PYTHONPATH=python python3 -m commitcut.cli examples/opensaas/transactional-control.toml --output .runs/control
```

The unchanged helper example should exit `1` because some crashes leave credits missing. Our version should exit `0`. `scripts/verify-examples.py --upstream` checks both results and succeeds only if the expected failures and passes occur.

PR #732 already discusses a separate problem with overlapping deliveries and what its change covers. We have not contacted the maintainers or shown a failure in a live deployment.
