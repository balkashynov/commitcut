# Why build CommitCut?

Sources were checked on 2026-10-15. No Stripe team has asked for this tool or agreed to use it.

## The problem

An app receives a payment webhook and needs to add credits once. It saves “event handled,” then adds the credits. If it crashes between those writes, the next attempt sees “handled” and skips the credits. Reversing the writes can add the credits twice instead.

A test that sends the webhook twice without stopping the app can miss both problems. CommitCut stops the actual app at selected database requests, restarts it, sends the webhook again, and checks the saved data. A developer can add this to their app's existing tests.

## Other tools and ideas we considered

| Idea | Why we chose something else |
| --- | --- |
| Send webhooks again, out of order, or with delays | [hookfuzz](https://github.com/shaurmittal/hookfuzz) and [webhook-chaos](https://github.com/24tiy/webhook-chaos) already test event delivery. We wanted to test what happens when the app process stops between database writes. |
| Recreate the whole Stripe API locally | [stripe-mock](https://github.com/stripe/stripe-mock) already helps test requests and responses. Correctly recreating payments, billing, and API versions would be much larger than this project. |
| Compare payment records across systems | [Stripe's Ledger article](https://stripe.com/blog/ledger-stripe-system-for-tracking-and-validating-money-movement) explains why checking money movement matters. We do not have a merchant's records or agreed requirements for a useful comparison tool. |
| Control the ordering of concurrent SQL requests | PostgreSQL has [isolation tests](https://github.com/postgres/postgres/blob/master/src/test/isolation/README). [misorder](https://github.com/anthid-labs/misorder) and [ChaosSQL](https://github.com/bregaldahq/chaossql) already work on related testing problems. [Mikael Lirbank's article](https://www.lirbank.com/harnessing-postgres-race-conditions) explains testing races in apps. We chose the smaller question of what survives an app crash and retry. |

## A real helper we could test

[OpenSaaS #688](https://github.com/wasp-lang/open-saas/issues/688) reported duplicate credits and other webhook concerns. It was closed by splitting up the work. [PR #732](https://github.com/wasp-lang/open-saas/pull/732) added a helper that records processed events and discussed a separate problem with overlapping deliveries.

We tested the unchanged helper from commit `925602ceafb5a0988a3b2ba72d754f022f121699` with real Prisma and a small function that adds credits in PostgreSQL. The test passes without a crash. Stopping the app after it saves the event row can leave the credits missing. Our version using one transaction passes every tested crash point. [Source and exact setup](../examples/opensaas/README.md).

This is a test of one helper in a small app, not the whole deployed OpenSaaS/Wasp application. We did not use a live Stripe account, contact maintainers, or show that a specific production app has this problem.

## What this tool adds

Crash testing, database proxies, and checks for duplicate work are existing techniques. CommitCut combines them into one command that:

1. Records the database requests made while handling a test webhook.
2. Stops the app before or after each request.
3. Restarts it and sends the webhook again.
4. Checks the database and saves a report for each crash point.

The inspected misorder README describes seeded faults and reducing failing examples; it lists some PostgreSQL and cross-connection work as incomplete. The inspected hookfuzz README excludes races between concurrent handlers. Neither README described this exact sequence of app crashes and retries. That does not prove no other tool can do it.

The useful next test is someone else's app: can they configure this tool and catch a problem their current tests miss?

## Work involved in the implementation

- Reading PostgreSQL messages without changing parameter values or returned rows.
- Tracking prepared statements so a repeated test can identify the same database request.
- Holding back every byte of a reply so the app cannot move on before it is stopped.
- Handling transaction status correctly, including a failed `COMMIT` that returns `ROLLBACK`.
- Stopping only the app process group started by the test runner, before closing its database connections.
- Reporting an error when a request is unsupported, a deadline expires, or the query order changes.
- Testing known broken examples alongside versions that save both database changes together.

[Implementation details](DESIGN.md) · [Recorded results](VALIDATION.md)
