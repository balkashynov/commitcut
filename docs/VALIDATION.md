# Test results

The runs below were recorded on 2026-10-15 with macOS ARM64, PostgreSQL 15.14, Rust 1.93.0, Python 3.13, pg 8.16.3, Prisma 5.19.1, and Stripe SDKs 18.5.0 / 18.1.0. [File hashes from those runs](evidence/manifest.json).

The README and report display were later rewritten for clarity. The saved JSON results and original run manifest were kept unchanged. The manifest describes the files used for the recorded runs, including the older report template.

## App crashes

Each example first passes without a crash, including restarting the app and sending the webhook again. All accounts and events are made up. No live Stripe account or payment was used.

| Example | Database request groups | Crash tests | Failed checks after retrying | Customer's credits |
| --- | ---: | ---: | ---: | --- |
| Mark handled, then add credits | 3 | 6 | 2 | 0 in the failing cases; expected 10 |
| Add credits, then mark handled | 3 | 6 | 2 | 20 in the failing cases; expected 10 |
| Save both changes in one pg transaction | 5 | 10 | 0 | 10 |
| Unchanged OpenSaaS helper, using Prisma | 4 | 8 | 4 | 0 in the failing cases; expected 10 |
| Our version using one Prisma transaction | 6 | 12 | 0 | 10 |

Two complete runs performed **84 crash tests**. Every test finished with a result. After removing elapsed times and temporary port numbers, the full reports matched between runs. [Comparison result](evidence/repeatability.json).

One run of all five examples took about 16 seconds on this machine. That is a measurement of these small examples, not a prediction for other apps.

Full reports: [mark handled first](evidence/claim-first.json), [add credits first](evidence/effect-first.json), [one pg transaction](evidence/atomic.json), [OpenSaaS helper](evidence/upstream.json), [one Prisma transaction](evidence/transactional-control.json).

## PostgreSQL message tests

Six tests against real PostgreSQL check that:

1. Simple-query replies match the bytes received without the proxy, including empty queries, several statements, SQL errors, and a failed `COMMIT` that returns `ROLLBACK`.
2. Named prepared statements, separate Describe/Sync messages, binary parameters and results, and statement reuse still work.
3. If PostgreSQL rejects a duplicate `Parse`, the proxy keeps the previously accepted statement information.
4. A separate connection can see a saved INSERT while the paused app receives zero reply bytes.
5. A separate connection cannot see an INSERT inside a transaction that has not committed.
6. An unsupported `Flush` exchange stops the test with an error.

Three Rust tests check message length limits, message encoding and decoding, strings, and the difference between a clean connection close and a message cut off halfway through.

Python tests check changed query order, encoded connection credentials, locking the local test database, and preventing text in a report from becoming executable HTML or JavaScript.

Separate checks passed with PostgreSQL's **SCRAM-SHA-256 password authentication** using both pg and Prisma. A fresh Python installation of the package ran a crash test against the one-transaction example and produced an offline HTML report.

## Automated checks

[Linux/PostgreSQL 16 and macOS CI](https://github.com/balkashynov/commitcut/actions/workflows/ci.yml) checks each pushed commit. Linux runs formatting, clippy, Rust and Python tests, the six real PostgreSQL message tests, package installation, and all 42 example crash tests. macOS runs formatting, clippy, Rust and Python tests.

The [first release run](https://github.com/balkashynov/commitcut/actions/runs/38760158951) passed at `e522ea8`. The workflow page shows results for later commits.

## What these results do not show

These tests do not cover every PostgreSQL message, live Stripe behavior, database crashes, emails or calls to other services, every ordering of concurrent requests, or the whole OpenSaaS application. [OpenSaaS test setup](../examples/opensaas/README.md).
