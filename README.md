# CommitCut

**Your duplicate-webhook test passes. What if the worker dies between two database writes?**

CommitCut kills your real application at PostgreSQL acknowledgement boundaries, restarts it, redelivers a signed webhook, and checks durable business state. No sleeps or crash hooks in your handler.

[Interactive recorded demo](https://balkashynov.github.io/commitcut/) · [Protocol design](docs/DESIGN.md) · [Why this project](docs/RESEARCH.md) · [Pinned OpenSaaS case](examples/opensaas/README.md)

```text
Claim first:   record event → CRASH → grant credits
Retry:         event already handled → HTTP 204
Customer:      0 credits. Expected 10.

Effect first:  grant credits → CRASH → record event
Retry:         event not recorded → grant again
Customer:      20 credits. Expected 10.

One transaction: receipt + credits commit together
Every tested cut: recovery leaves 10 credits.
```

The examples use **real PostgreSQL, actual SIGKILLs, pg/Prisma, and the official Stripe SDK**. An unmodified pinned OpenSaaS helper demonstrates the omission in a small dependency harness. See [scope and provenance](examples/opensaas/README.md); this is not a complete deployed-application or live Stripe test.

## Try it

Requires Python 3.11+, Rust 1.93+, Node 22+, PostgreSQL with `psql`. Linux and macOS are the intended platforms.

```sh
cargo build --release
python3 -m venv .venv
.venv/bin/pip install .
npm ci --prefix examples/stripe-credits
```

Start a disposable database, for example with Docker:

```sh
docker compose up -d --wait
export COMMITCUT_DATABASE_URL='postgresql://commitcut:commitcut_local_test@127.0.0.1:55439/commitcut_test'
```

Or use [the local PostgreSQL helper](scripts/local-postgres.sh). Put `psql` on PATH, or set `COMMITCUT_PSQL` to its absolute path. The native binary is discovered from this checkout or `COMMITCUT_PROXY`.

```sh
# Expected to exit 1: finds missing fulfillment.
.venv/bin/commitcut examples/stripe-credits/claim-first.toml --output .runs/claim-first

# Expected to exit 0: verifies the transactional control at all observed cuts.
.venv/bin/commitcut examples/stripe-credits/atomic.toml --output .runs/atomic
```

Open `.runs/claim-first/report.html`. It is self-contained and works offline. JSON evidence and worker logs are saved beside it. `--include-sql` adds raw SQL for synthetic fixtures; it is off by default.

## Test your handler

Write a small scenario:

```toml
name = "Invoice credit fulfillment"
reset_sql = "reset-test-database.sql"
timeout_seconds = 10
redeliveries = 2

[app]
command = ["node", "server.mjs"]
health_path = "/health"
# Worker reads DATABASE_URL and PORT; both are supplied by CommitCut.

[request]
path = "/webhook"
body_file = "invoice-paid.json"
stripe_secret_env = "STRIPE_WEBHOOK_SECRET"

[[assertions]]
name = "Exactly one credit grant"
sql = "SELECT count(*) FROM credit_grants WHERE invoice_id = 'in_test'"
expected = "1"
```

Paths resolve relative to the scenario. Set `COMMITCUT_DATABASE_URL` to a dedicated **loopback** test database and `STRIPE_WEBHOOK_SECRET` to the worker's test signing secret. The worker receives a proxied `DATABASE_URL`; reset and assertions connect directly. The application command must run in the foreground. Put any setup in the supplied reset SQL, and ensure your health endpoint is ready before requests start.

```sh
commitcut scenario.toml --output evidence/run-1
commitcut scenario.toml --cut 2 --phase after --output evidence/repro-2
```

Exit codes: **0** assertions held at the tested cuts, **1** a business invariant failed, **2** inconclusive or setup error. A baseline failure, protocol error, query-sequence drift, timeout or failed recovery response never becomes a green test.

## What the matrix covers

Each observed query cycle is tested before it executes and after PostgreSQL completes it but before the application sees **any** response bytes. The entire worker process group is killed, then restarted against the surviving database. A crash within an open transaction leaves PostgreSQL to roll it back; a completed transaction survives.

This release covers the supplied sequential fixture and its observed wire boundaries. It does not simulate all Stripe behaviors, prove exactly-once delivery, explore every concurrent schedule, or crash the database. External side effects and asynchronous jobs need additional completion contracts and assertions. [Full contract and protocol limits](docs/DESIGN.md).

## Verify the evidence

```sh
cargo test
cargo clippy --all-targets -- -D warnings
PYTHONPATH=python python3 -m unittest discover -s tests -p test_runner.py
PYTHONPATH=python python3 scripts/verify-examples.py --output .runs/verification
```

For the upstream case, install its pinned dependencies and generate Prisma first:

```sh
npm ci --prefix examples/opensaas
(cd examples/opensaas && DATABASE_URL="$COMMITCUT_DATABASE_URL" npx prisma generate)
PYTHONPATH=python python3 scripts/verify-examples.py --upstream --output .runs/full-verification
```

The verification script requires both failure controls to fail and transactional controls to pass. Real protocol differential tests are enabled with `COMMITCUT_PROTOCOL_TESTS=1`; they require a dedicated trust-auth local database and are run explicitly in CI.

MIT licensed. Unofficial project; no affiliation with or endorsement by Stripe or OpenSaaS. No telemetry.
