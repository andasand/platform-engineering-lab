# Phase 3 idempotency — proposed deployment review

Local implementation and synthetic validation only. No AWS connection, deployment,
push, Terraform change, or real Windows collection was performed for this work.
The approval candidate now has an [exact rollout and rollback runbook](PHASE3_ROLLOUT_PLAN.md),
including the locally reverified encrypted recovery point. Its commands remain unexecuted.

## Proposed database migration

[`002_source_event_id.sql`](../database/migrations/002_source_event_id.sql) runs
after the unchanged `001_security_events.sql`. In a transaction it adds nullable
`source_event_id TEXT` and the named unique constraint
`security_events_source_event_id_key`. It validates a preexisting same-named
constraint instead of assuming it is correct. Migration reruns are supported.

Existing records keep their IDs, timestamps, raw JSON, and all other values;
their new column is null. There is no UPDATE, DELETE, backfill, schema replacement,
or historical deduplication. PostgreSQL's default unique constraint allows multiple
nulls, preserving existing producers that omit source identifiers. IDs are globally
unique and case-sensitive; each producer must own a namespace. The collector uses
`winsec:v1:<sha256(host-alias, generation, channel, record-id)>` with stable inputs.

The API attempts `INSERT ... ON CONFLICT (source_event_id) DO NOTHING RETURNING id`.
On conflict, a separate statement under explicit READ COMMITTED isolation reads
the committed winner and compares its canonical validated JSONB payload. Equal
content returns the original ID; different content returns 409. Replays do not
update the existing row or its creation timestamp. This separate lookup accounts
for concurrent inserts that were not visible at the initial INSERT snapshot; see
[PostgreSQL transaction isolation](https://www.postgresql.org/docs/16/transaction-iso.html).
Identity sequence gaps from conflict attempts are normal. The key is stored
directly; payload comparison uses the full validated JSONB, not a lossy fingerprint.

API validation limits identifiers to 1–128 ASCII letters/digits and `._:-`, with
an alphanumeric first character. The constraint enforces uniqueness even for
direct database writes; input-format validation belongs to the API.

## Locking, deployment order, and rollback

This small-lab migration uses ordinary transactional ALTER TABLE/unique-index
creation. It can briefly block writes and needs index disk space; it is **not an
online migration guarantee**. Lock acquisition is bounded to five seconds and
statement execution to sixty seconds. Failure rolls back the whole migration;
investigate blockers/size and retry in an approved window. A large table needs a
separately reviewed concurrent-index migration rather than raising limits blindly.

Before an approved deployment: take and verify the existing PostgreSQL backup,
review table/index size and available disk, pause collector sending, and confirm
the reviewed source revision. Apply 001 then 002 with `psql -X -v ON_ERROR_STOP=1`;
check the named unique constraint and preservation of old record counts/sample
IDs. The prepared Compose initializer mounts and applies both SQL files in order.
No deployment command has been executed here.

Deploy the updated ingestor only after the migration commits. `/ready` checks the
column and nondeferrable unique constraint and then advertises
`capabilities.source_event_id = "v1"`. Run an approved synthetic duplicate/conflict
check before enabling any collector delivery. Existing public-ingress, loopback
ports, PostgreSQL storage, and Docker volumes need no changes.

Application rollback can retain the additive column/index: the old ingestor
ignores them and still accepts legacy requests. **Pause idempotent collectors
before rollback**: the old API silently ignores extra JSON fields and does not
deduplicate. New collectors refuse an old receiver's readiness response, but a
capability check is not protection against a server being swapped mid-batch.
Do not drop the column/index or restore an older database to roll back application
code; either would discard deduplication history or newer data. Re-enable delivery
only after the idempotent API and readiness are restored.

## Compatibility and retry contract

- Omitted/null `source_event_id`: legacy insert-per-request behavior and unchanged
  success-response fields; duplicate prevention is unavailable for these requests.
- Keyed request: HTTP 200 includes the echoed source ID; retries return the same
  database ID. The full validated payload, including its original event timestamp,
  must remain identical. Severity is uppercase, IP values canonicalized by Pydantic,
  and timestamps normalized to UTC. Previously accepted naive timestamps remain
  accepted and now explicitly mean UTC; historical raw JSON is not rewritten.
- Same ID/different content: HTTP 409 with no overwrite; collector blocks/retains
  that event. Invalid identifiers or payloads return 422. Both need review rather
  than a new randomly generated identifier.
- Database errors/HTTP 5xx/timeouts: retry the **same persisted ID and payload**
  with bounded backoff only against a receiver advertising the new contract.
  Malformed or mismatched acknowledgements do not advance the ACK checkpoint.
- An interrupted send becomes unknown on restart and is requeued only after the
  capability check. Retry counts/deadlines survive restart. Exhausted budgets
  retain events in blocked state; no automatic queue or checkpoint deletion.
- Duplicate protection lasts while the original keyed database record exists.
  Deleting records or restoring a database predating their insertion removes that
  history. The design does not claim exactly-once delivery under arbitrary database
  restoration, deletion, or malicious source-key reuse.

Old `synthetic-security-v1` outboxes lack the keyed protocol and are refused by
the new `synthetic-security-v2` collector. Preserve and reconcile any old pending,
unknown, or acknowledged state; do not relabel it or invent keys for past sends.
Historical rows with null identifiers cannot be matched reliably without a separate
reviewed reconciliation/backfill. New synthetic tests use fresh disposable state.

The CLI remains synthetic/offline. The test-facing loopback HTTP adapter disables
proxies and redirects, checks readiness before each batch, and handles timeouts
with stable-key retries. It does not open an SSM session or select an AWS target.
SSM delivery and real-log collection still require separate approval.

## Local validation

Install `tests/requirements.txt` into the test virtual environment, then run:

```bash
bash scripts/test-phase3-local.sh
```

The runner starts a separate `postgres:16` container with temporary RAM-backed
storage and a random loopback port, then removes only that container. It never
reads `.env`, starts the normal Compose project, or touches existing volumes.
Trust authentication is confined to this disposable local test instance; it is
not a proposed deployment configuration.

Tests cover legacy-row preservation and repeatable migration, direct SQL uniqueness,
sequential/concurrent duplicate submissions, original-ID replay, changed-content
conflicts, legacy clients, validation, canonical timestamps, readiness checks,
bounded queues/batches, retry budgets, restarts, and a real loopback HTTP timeout
after PostgreSQL commits followed by collector restart/retry. Offline unit tests
also verify filtering/redaction and old-receiver rejection. No real logs are used.

Local result on 2026-10-30: **16 collector tests and 11 PostgreSQL/API integration
tests passed** using the disposable runner. The timeout test delayed a real HTTP
response after commit, reopened the outbox, and verified that retry returned the
original PostgreSQL ID with one stored row. Compose merge assertions, shell syntax,
and source whitespace checks passed. Test containers were removed; existing local
containers remained running. These results do not constitute an AWS deployment
or live Windows validation.
