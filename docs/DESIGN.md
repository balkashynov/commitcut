# How CommitCut runs a crash test

CommitCut checks what remains in PostgreSQL after an app crashes and receives the same webhook again. You supply SQL queries and expected results. For example, a query can check that a customer has 10 credits, rather than 0 or 20.

## The test, step by step

First, CommitCut runs the app without a crash. It sends the webhook, checks the database, restarts the app, sends the webhook again, and checks the database again. This first run must pass before crash tests start. It also records the app's database requests.

For each recorded request, CommitCut resets the database and runs two separate tests:

- **Before:** stop the app before sending that request to PostgreSQL.
- **After:** let PostgreSQL finish the request, hold back its whole reply, then stop the app.

The Python test runner stops the app with `SIGKILL`, waits for its process-group leader to exit, and closes the database connections by stopping the proxy. PostgreSQL stays running. The runner then starts a new app process, sends the same webhook body with a new Stripe timestamp and signature, and runs the SQL checks through a separate connection.

A timeout, failed retry, unsupported request, missing crash point, or changed query order produces an **inconclusive** result. This means the tool could not check the result reliably. It never counts as a pass.

## What counts as a database request

The proxy is a Rust program between the app and PostgreSQL. It reads PostgreSQL v3 messages and groups them into **query cycles**:

- A simple `Query` message is one cycle. It may contain several SQL statements.
- Extended-protocol messages are grouped up to `Sync`. A group may prepare a statement, describe it, or execute it.

The report calls these groups “database requests.” The tool cannot stop between statements in the same group. In Prisma examples, preparing a statement and executing it can appear as separate rows. Preparing a statement does not save the change it describes.

The proxy tracks prepared statement and portal names so it can associate `Parse`, `Bind`, and `Execute` messages with SQL. It forwards parameter values and result rows without putting them in the trace. It compares SQL hashes when checking that a later run has reached the same request.

## Why the whole reply is held back

For an “after” test, the proxy collects the response through PostgreSQL's `ReadyForQuery` message before stopping the app. It does not forward any part of that response to the app.

Holding back only the last message would be too late. Some clients consider a query complete after `CommandComplete` and could start the next operation. At the same time, `CommandComplete` alone does not show that an implicit extended-protocol transaction has committed; `Sync` finishes that transaction.

The report records PostgreSQL's transaction status:

| Value | Meaning |
| --- | --- |
| `I` | Outside a transaction |
| `T` | Inside a transaction |
| `E` | Inside a transaction that has failed |

`I` does not prove that a write was saved. For example, `COMMIT` in an already failed transaction can return `ROLLBACK`. The trace keeps command tags and SQLSTATE error codes. The final SQL checks read the actual database rather than guessing from a status flag.

## Message sizes and connections

`native/src/wire.rs` checks message lengths before allocating memory. One message is limited to 16 MiB and a whole query cycle to 32 MiB. Separate tasks read from the app and database so cancellation during authentication does not discard half of a message.

Startup and authentication messages are forwarded. TLS and GSS encryption requests are declined. Use a local test connection without encryption; this is not a proxy for production traffic.

During a test, a shared lock lets one query cycle through at a time. At the selected crash point, the proxy holds that lock until the runner stops the app and proxy. This supports the supplied examples, which send one webhook at a time. An app doing several things at once may change its query order or time out; these tests do not cover every possible ordering.

## Starting and stopping processes

The runner starts the app in a new process group. It gets the group ID from the process it started, not from the configuration file. It kills that group before closing the paused database connections. Otherwise, a connection error might let the app run cleanup code instead of crashing as intended.

The proxy has no child processes and is stopped directly. It also exits if the runner closes its standard input.

App commands are lists of arguments, not shell commands. Database reset and result checks use `psql` with `ON_ERROR_STOP`. The runner does not use Python `eval`, shell interpolation, or a user-supplied process ID to kill.

A local lock file prevents two CommitCut runs from resetting the same database at once. It does not stop unrelated programs from using that database. Use a database dedicated to these tests.

## Repeating the same crash point

Each crash point has a request number, protocol type, SQL SHA-256 hash, and transaction status before the request. The runner compares the requests up to that point with the first run. If they differ, it refuses to count the result as a pass.

Parameter values are not compared. Generated IDs and timestamps can differ between runs. The tool repeats the selected database request position, not every instruction or network message in the app. Run the full test again to check whether its results repeat.

## Supported requests

The local tests cover PostgreSQL 15, `pg` 8 simple and extended queries, Prisma 5.19.1, named statement reuse, binary parameters, several statements in a simple query, SQL errors, and transaction rollback. Linux CI also runs against PostgreSQL 16.

The tool rejects `Flush`-driven exchanges, COPY, asynchronous notifications, suspended portals, cancel connections, startup protocols other than v3, and messages above the size limits. It reports an error instead of continuing with an unreliable test.

## Limits to keep in mind

- It tests only the requests made by the supplied webhook. It cannot check code paths that were not run.
- It stops only the app. It does not test database crashes, power loss, disk corruption, replication failover, or durability settings.
- It does not try every possible ordering of threads, processes, or network messages.
- It does not check emails, other APIs, queues, or other databases. They need separate test code and result checks.
- The health endpoint must say when the app is ready. The webhook response must mean the work being checked is finished. Queued jobs need an explicit way to wait for completion; this release does not provide one.
- The database is reset for each test. Use made-up data in a database you can erase. Reports omit raw SQL by default and do not include request bodies, credentials, or returned rows. Names and values in SQL checks, and the app's log files, may still contain data supplied by the test author.
- A pass means only that the supplied checks passed at the tested crash points. If a check misses a problem, CommitCut will miss it too.
