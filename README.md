# CommitCut

[![CommitCut: the duplicate test passes, but a crash leaves zero credits after two successful retries.](docs/assets/commitcut-hero.svg)](https://balkashynov.github.io/commitcut/#case=0&cut=2-after)

<p align="center">
  <a href="https://balkashynov.github.io/commitcut/#case=0&amp;cut=2-after"><strong>Explore the recorded failure →</strong></a>
  &nbsp; · &nbsp; <a href="#try-it">Run it locally</a>
  &nbsp; · &nbsp; <a href="docs/DESIGN.md">Read the protocol design</a>
</p>

<p align="center">
  <a href="https://github.com/balkashynov/commitcut/actions/workflows/ci.yml"><img src="https://github.com/balkashynov/commitcut/actions/workflows/ci.yml/badge.svg" alt="Linux and macOS verification" /></a>
</p>

**Turn a webhook's crash window into a repeatable CI failure.** CommitCut runs your real application through a Rust PostgreSQL proxy, kills the worker at a selected database acknowledgement boundary, restarts it, redelivers a signed fixture, and checks business state with SQL. No sleeps or crash hooks in your handler.

For developers whose Stripe handlers grant credits, provision access, or record fulfillment in PostgreSQL. A normal duplicate-delivery test can pass while a worker crash still loses or duplicates the effect. CommitCut makes that gap visible before you ship.

## See the bug in 60 seconds

1. **[Open the failing cut](https://balkashynov.github.io/commitcut/#case=0&cut=2-after).** The event receipt committed, but the worker died before the database response reached it.
2. **Inspect recovery.** Both redeliveries return `204`. The handler sees an existing receipt and skips fulfillment. The customer has **0 credits**, expected **10**.
3. **[Switch to one transaction](https://balkashynov.github.io/commitcut/#case=2).** Receipt and credits commit together. All ten tested cuts recover to **10 credits**.

The demo is an interactive viewer of **recorded executions**, with query timelines, transaction state, recovery responses and SQL assertions. It does not run a database in your browser.

## Real crashes. Checkable results.

Every scenario passes its unfaulted restart-and-duplicate baseline. Injecting crashes separates the unsafe handlers from their transactional controls:

| Handler | Failure after recovery | Violating cuts / tested cuts |
| :--- | :--- | ---: |
| Record receipt, then grant credits (`pg`) | **0 credits** instead of 10 | **2 / 6** |
| Grant credits, then record receipt (`pg`) | **20 credits** instead of 10 | **2 / 6** |
| Receipt + credit grant in one transaction (`pg`) | None observed; **10 credits** | **0 / 10** |
| Unchanged pinned OpenSaaS helper (Prisma) | **0 credits** instead of 10 | **4 / 8** |
| Our transactional replacement (Prisma) | None observed; **10 credits** | **0 / 12** |

**42 cuts per full matrix. Two local release runs, 84 cuts, zero inconclusive outcomes.** Counts describe this bounded fixture, not production failure rates. The examples use real PostgreSQL, actual `SIGKILL`s, `pg` / Prisma, and the official Stripe SDK for signature verification.

The OpenSaaS case uses an unmodified, attributed helper pinned to a specific revision in a small dependency harness. It is not a full deployed Wasp application or a live Stripe payment test.

[Complete reports and validation](docs/VALIDATION.md) · [Upstream source and harness scope](examples/opensaas/README.md) · [Why this project / prior art](docs/RESEARCH.md)

## The boundary that matters

```text
Application            CommitCut proxy                 PostgreSQL
    │                        │                              │
    ├──── query ────────────►├──────── query ──────────────►│
    │                        │                       write commits
    │                        │◄──── complete response ─────┤
    │                  buffer response                      │
    ✕  SIGKILL         withhold every byte                 │
    │                                                       │
 restart + replay ───────────────► inspect surviving business state
```

PostgreSQL finishing a write and the application observing its acknowledgement are different events. CommitCut buffers the full server response through `ReadyForQuery`, then kills the worker before forwarding any of it. It also tests the boundary before each observed query cycle executes.

The proxy handles simple and extended query protocols, prepared statements, portals and binary parameters/results. It tracks transaction status, including a failed `COMMIT` that returns `ROLLBACK`. Unsupported protocol behavior and uncertain recovery produce an **inconclusive** result, never a green test. [Read the protocol contract and tests](docs/DESIGN.md).

## Try it

Requires Python 3.11+, Rust 1.93+, Node 22+, PostgreSQL with `psql`. Linux and macOS are the intended platforms.

```sh
git clone https://github.com/balkashynov/commitcut.git
cd commitcut
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
