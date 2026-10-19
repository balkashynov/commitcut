# CommitCut

**Crash-test your Stripe webhook handler.**

CommitCut sends a test Stripe webhook to your app, stops the app during a database request, restarts it, and sends the same webhook again. It then checks the data in PostgreSQL. You can use it to test code that adds credits, gives access to a subscription, or records an order after a payment.

An independent project, not made or endorsed by Stripe.

## The problem, with an example

A customer buys 10 credits. Stripe sends your app a webhook: an HTTP request saying the payment happened. Your app needs to add those credits once, even if it receives the same webhook again.

This code looks reasonable:

```text
1. If this event is already marked as handled, return success.
2. Save a row that marks the event as handled.
3. Add 10 credits to the customer's account.
```

But the app can crash between steps 2 and 3. When it restarts and receives the webhook again, it finds the saved row and skips the work. It returns success, but the customer still has **0 credits**.

Sending the webhook twice without a crash does not find this bug.

**[See this test result in your browser](https://balkashynov.github.io/commitcut/#case=0&cut=2-after)** · [See the version that saves both changes together](https://balkashynov.github.io/commitcut/#case=2)

The page shows saved results from real test runs. You can choose when the app crashes and see the credits left after two retries. No installation is needed to view it.

## What CommitCut does

1. Runs your app against a local test PostgreSQL database.
2. Sends a test webhook and records the app's database requests.
3. Starts a separate test for each request, stopping the app just before or just after PostgreSQL handles it.
4. Restarts the app and sends the same webhook again.
5. Runs SQL checks that you provide, such as “the customer has exactly 10 credits.”

Each test starts with a reset database. Only the app is stopped; PostgreSQL keeps running. CommitCut runs your app's code without adding crash instructions to it.

## Results from the included examples

All five examples pass when the webhook is repeated without a crash. Stopping the app finds these problems:

| How the app saves the changes | Result after restarting and retrying | Failed tests / crash tests |
| :--- | :--- | ---: |
| Mark handled, then add credits | Some crashes leave **0 credits** | **2 / 6** |
| Add credits, then mark handled | Some crashes leave **20 credits** | **2 / 6** |
| Save both changes in one database transaction | **10 credits** in every tested case | **0 / 10** |
| OpenSaaS's unchanged event helper, using Prisma | Some crashes leave **0 credits** | **4 / 8** |
| Our version that saves both changes together, using Prisma | **10 credits** in every tested case | **0 / 12** |

A database transaction saves both changes together or neither of them. That prevents the half-finished database update in these examples.

There are 42 crash tests in one run of all five examples. We ran them twice locally: **84 tests, with a result for every test**. These counts describe the examples, not how often this happens in a real service.

The tests use real PostgreSQL, real app processes, `pg` and Prisma database clients, and Stripe's SDK to check webhook signatures. They use made-up events and accounts. No payment is made and no request is sent to Stripe.

The OpenSaaS example uses one unchanged helper from a specific version of that project, inside a small test app. We did not run the whole OpenSaaS/Wasp application. [See exactly what was tested](examples/opensaas/README.md).

[Saved test results](docs/VALIDATION.md) · [How it works](docs/DESIGN.md) · [Why we built it and other tools](docs/RESEARCH.md) · [Linux and macOS checks](https://github.com/balkashynov/commitcut/actions/workflows/ci.yml)

## How it stops the app at the right time

A small Rust program sits between your app and PostgreSQL. It forwards database requests and can hold back PostgreSQL's replies.

For an “after” test, it waits until PostgreSQL has finished a request, holds back the entire reply, and tells the test runner to stop the app. This lets us test a write that the database saved but the app never got to confirm. A Python program stops and restarts the app, sends the webhook again, and runs the SQL checks.

The tool tests PostgreSQL request groups, called **query cycles** in the protocol. A group can contain several SQL statements, or just prepare a statement for later use. It cannot stop between statements sent in the same group. [Protocol details and supported requests](docs/DESIGN.md).

## Try it

Requires Python 3.11+, Rust 1.93+, Node 22+, and PostgreSQL with `psql`. Linux and macOS are supported.

```sh
git clone https://github.com/balkashynov/commitcut.git
cd commitcut
cargo build --release
python3 -m venv .venv
.venv/bin/pip install .
npm ci --prefix examples/stripe-credits
```

Start a database that can be erased between tests, for example with Docker:

```sh
docker compose up -d --wait
export COMMITCUT_DATABASE_URL='postgresql://commitcut:commitcut_local_test@127.0.0.1:55439/commitcut_test'
```

You can also use [the local PostgreSQL script](scripts/local-postgres.sh). Put `psql` on PATH, or set `COMMITCUT_PSQL` to its full path. CommitCut finds the compiled Rust program in this checkout; set `COMMITCUT_PROXY` if it is elsewhere.

```sh
# Expected to exit 1: some crashes leave the customer with no credits.
.venv/bin/commitcut examples/stripe-credits/claim-first.toml --output .runs/claim-first

# Expected to exit 0: all tested crashes leave the customer with 10 credits.
.venv/bin/commitcut examples/stripe-credits/atomic.toml --output .runs/atomic
```

Open `.runs/claim-first/report.html`. It works offline. The same folder contains the JSON results and app logs. Add `--include-sql` to include SQL text in the report; it is left out by default.

## Test your own app

Create a configuration file. It specifies how to start the app, reset the database, send the webhook, and check the result.

```toml
name = "Add credits after an invoice is paid"
reset_sql = "reset-test-database.sql"
timeout_seconds = 10
redeliveries = 2

[app]
command = ["node", "server.mjs"]
health_path = "/health"
# The app reads DATABASE_URL and PORT, both supplied by CommitCut.

[request]
path = "/webhook"
body_file = "invoice-paid.json"
stripe_secret_env = "STRIPE_WEBHOOK_SECRET"

[[assertions]]
name = "Credits added once for this invoice"
sql = "SELECT count(*) FROM credit_grants WHERE invoice_id = 'in_test'"
expected = "1"
```

File paths are relative to this configuration file. Set `COMMITCUT_DATABASE_URL` to a test database on your own machine and `STRIPE_WEBHOOK_SECRET` to the test signing secret your app uses.

The app connects through the Rust program. Database reset commands and result checks connect directly to PostgreSQL. The app must stay in the foreground, and its health endpoint must say when it is ready to receive requests. Include all starting data in the reset SQL.

```sh
commitcut scenario.toml --output evidence/run-1

# Repeat just the crash after request group 2.
commitcut scenario.toml --cut 2 --phase after --output evidence/repro-2
```

Exit codes:

- **0:** All SQL checks passed in the tested cases.
- **1:** At least one SQL check found the wrong result.
- **2:** The test could not finish or could not reliably check the result.

A timeout, failed retry, unsupported database request, or changed query order does not count as a pass.

## What this does not test

CommitCut tests one webhook at a time and stops only the app process. It does not stop PostgreSQL, test disk failures, try every possible ordering of concurrent requests, or check live Stripe payments.

A PostgreSQL transaction cannot undo an email or a request to another service. Those need their own checks. Apps that put work on a queue also need a way to wait until that work finishes; this version does not provide one.

Passing means the SQL checks passed for the crash points that were tested. It does not prove the app is correct in every situation.

## Run the checks

```sh
cargo test
cargo clippy --all-targets -- -D warnings
PYTHONPATH=python python3 -m unittest discover -s tests -p test_runner.py
PYTHONPATH=python python3 scripts/verify-examples.py --output .runs/verification
```

To include the OpenSaaS examples, install their dependencies and generate the Prisma client first:

```sh
npm ci --prefix examples/opensaas
(cd examples/opensaas && DATABASE_URL="$COMMITCUT_DATABASE_URL" npx prisma generate)
PYTHONPATH=python python3 scripts/verify-examples.py --upstream --output .runs/full-verification
```

The script checks that the known broken examples fail and the versions using one transaction pass. Set `COMMITCUT_PROTOCOL_TESTS=1` to run the separate PostgreSQL protocol tests. They need a local test database with trust authentication and run in the Linux CI job.

MIT licensed. This is an independent project, not made or endorsed by Stripe or OpenSaaS. It does not send usage data anywhere.
