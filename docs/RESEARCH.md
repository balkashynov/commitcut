# Why build CommitCut?

Research checked 2026-10-15. This is a selection argument, not evidence of Stripe endorsement or external adoption.

## The user and the job

A developer operating a Stripe-backed application needs to preserve paid fulfillment across worker restarts. A sequential duplicate-webhook test can pass while a crash between a committed receipt and its business mutation permanently loses the mutation. Reversing those writes can duplicate fulfillment instead.

The useful output is a reproducible failing CI test against the developer's real process and PostgreSQL database, with the boundary, transaction state and post-retry business result. The first adoption path is an application's existing integration suite. Stripe Developer Experience or integration-reliability engineers are a plausible audience; this is an inference, not an internal Stripe requirement.

## Ideas considered and rejected

| Direction | Evidence of usefulness | Why it was not selected |
| --- | --- | --- |
| Webhook replay, duplication and ordering fuzzer | Stripe documents retries and unordered delivery; developers report duplicate fulfillment. | [hookfuzz](https://github.com/shaurmittal/hookfuzz) already generates events, checks invariants and shrinks deliveries. [webhook-chaos](https://github.com/24tiy/webhook-chaos) already signs and schedules recorded events. Another ordinary replay CLI adds little. |
| A complete stateful Stripe emulator | Fast offline integration tests are useful. | [stripe-mock](https://github.com/stripe/stripe-mock) explicitly limits its behavior and warns about fidelity. Maintaining payments, billing and API-version semantics is too broad to validate credibly here. |
| Stripe reconciliation engine | [Stripe's Ledger article](https://stripe.com/blog/ledger-stripe-system-for-tracking-and-validating-money-movement) illustrates the value of checking money movement. | Merchant mappings and representative real records are necessary to establish incremental usefulness. No such dataset or user commitment is available. Not a reason to invent one. |
| General SQL scheduling / race debugger | PostgreSQL has [isolation tests](https://github.com/postgres/postgres/blob/master/src/test/isolation/README); [Mikael Lirbank's barrier-testing article](https://www.lirbank.com/harnessing-postgres-race-conditions) describes the application-test gap. | [misorder](https://github.com/anthid-labs/misorder) already proxies real dependencies; [ChaosSQL](https://github.com/bregaldahq/chaossql) already explores SQL concurrency. A general replacement would overlap substantially. |
| **Systematic application crash/restart/redelivery at wire boundaries** | A real processed-event helper separates its event claim and fulfillment. That gap can survive ordinary replay tests. | **Selected.** A small, inspectable protocol engine can enumerate observed boundaries, kill the actual owned process before it sees a response, restart, redeliver and compare durable state. |

## Concrete evidence, including inconvenient details

[OpenSaaS #688](https://github.com/wasp-lang/open-saas/issues/688) reported duplicate credit grants and other webhook concerns. It was **closed by splitting the scope**, not because every concern vanished. [PR #732](https://github.com/wasp-lang/open-saas/pull/732) merged an event-claim helper and explicitly discussed a different overlapping-delivery failure window. We do not present that project as ignoring idempotency or claim to have discovered the general pattern.

CommitCut tests the unmodified helper from commit `925602ceafb5a0988a3b2ba72d754f022f121699`, using real Prisma and a small PostgreSQL credit-grant callback. The unfaulted restart/duplicate baseline succeeds. Killing the worker after its event claim commits yields acknowledged retries and zero credits. A transactional control passes the tested boundaries. See [the exact scope and provenance](../examples/opensaas/README.md).

This is stronger than a planted example, but it is **not** a full deployed OpenSaaS/Wasp application run. We did not use a live Stripe account, charge a card, contact maintainers, or establish that a particular production deployment exhibits the failure.

## What is distinct, and what is not

Crash testing, protocol proxies, and idempotency are established techniques. The inspected competitors overlap heavily with the ingredients. CommitCut's specific workflow is an enumerated *application SIGKILL → fresh process → same signed fixture → SQL business assertion* matrix at real PostgreSQL response boundaries, with an unchanged upstream helper as a case study.

The inspected misorder README supports seeded faults and shrinking, and lists cross-connection holds and a Postgres worked example as incomplete. The inspected hookfuzz README excludes concurrent handler races. Neither inspected README documents this systematic application-crash matrix. That observation is **not** proof that no other tool implements it.

We deliberately narrowed the product instead of claiming a new distributed-systems invention. Its usefulness should be judged by whether a developer can add the scenario and catch a regression in their own code.

## Why this is technically substantive

- PostgreSQL startup/authentication and simple/extended query framing.
- Prepared statement and portal tracking, including named statement reuse and binary parameters.
- Full-response withholding: resolving on `CommandComplete` must not let the application advance before the cut.
- Correct `Sync` and `ReadyForQuery` transaction boundaries. `I` means idle; `COMMIT` can return a `ROLLBACK` tag.
- A process supervisor that kills only its own worker group and waits before releasing database connections.
- Fail-closed handling of protocol modes, deadlines, sequence drift and baseline failures.
- Negative controls with real database commits, uncommitted writes, driver byte transparency, omissions, duplications and atomic recovery.

A shareable story follows directly from the measured result: **“The duplicate test passed. A crash between two database writes still lost the customer's credits.”** Attention, adoption, interviews and offers remain outcomes to pursue, not claims made by this artifact.
