# Validation evidence

Verified locally on 2026-10-15: macOS ARM64, PostgreSQL 15.14, Rust 1.93.0, Python 3.13, pg 8.16.3, Prisma 5.19.1 and official Stripe SDKs 18.5.0 / 18.1.0. [Source and binary hashes](evidence/manifest.json).

## Actual worker crashes

Every row starts with a passing unfaulted baseline, including a fresh worker and duplicate redeliveries. All tests use synthetic fixtures; no live Stripe account or payment was used.

| Scenario | Observed cycles | Cuts tested | Violations | Recovered credit balance |
| --- | ---: | ---: | ---: | --- |
| Receipt before credit | 3 | 6 | 2 | 0 at failing cuts, expected 10 |
| Credit before receipt | 3 | 6 | 2 | 20 at failing cuts, expected 10 |
| Receipt + credit in one pg transaction | 5 | 10 | 0 | 10 |
| Pinned OpenSaaS helper through Prisma | 4 | 8 | 4 | 0 at failing cuts, expected 10 |
| Transactional Prisma control | 6 | 12 | 0 | 10 |

Two identical release runs executed **84 cuts total**, with zero inconclusive outcomes. Complete reports matched after excluding elapsed time and ephemeral listener addresses; [repeatability result](evidence/repeatability.json). One five-scenario run took about 16 seconds on this machine. That is an example-run duration, not a general application performance benchmark.

Full reports: [claim first](evidence/claim-first.json), [effect first](evidence/effect-first.json), [pg atomic](evidence/atomic.json), [upstream helper](evidence/upstream.json), [Prisma control](evidence/transactional-control.json).

## Protocol and lifecycle checks

Six real PostgreSQL protocol tests cover:

1. Byte-for-byte simple-query response equivalence, including multi-statement queries, empty queries, SQL errors and an aborted COMMIT returning ROLLBACK.
2. Named prepared statements, separate Describe/Sync, binary Bind parameters/results and statement reuse.
3. Rejected duplicate Parse preserves the previously acknowledged statement's metadata.
4. A committed INSERT is visible to an independent connection while the cut client receives zero response bytes.
5. A cut inside an open transaction has not made the uncommitted INSERT visible.
6. Unsupported Flush exchanges fail closed.

Three Rust framing tests cover bounded allocation, roundtrip framing, clean EOF versus truncation, and protocol strings. Python tests cover sequence-drift rejection, encoded connection credentials, exclusive local database ownership, and embedded-report script-termination injection.

Separate smoke tests passed through **real PostgreSQL SCRAM-SHA-256 authentication** with both pg and Prisma. A fresh-venv installed CLI passed a selected atomic crash/recovery test and generated its offline HTML report. Browser checks exercised scenario switching and displayed the saved omission and recovery evidence.

## Limits

The native engine is a testing proxy, not production infrastructure. These tests do not establish complete protocol coverage, live Stripe behavior, database crash durability, remote side-effect safety, every concurrent schedule, or a full OpenSaaS deployment's behavior. The exact upstream dependency-harness scope is documented [here](../examples/opensaas/README.md).

Hosted [Linux/PostgreSQL 16 and macOS CI](https://github.com/balkashynov/commitcut/actions/runs/38760158951) passed on initial release `e522ea8`: formatting, clippy, Rust and Python tests, real protocol checks, fresh package installation and all 42 integration cuts. See the repository's latest workflow status for subsequent commits.
