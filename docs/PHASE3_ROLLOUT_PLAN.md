# Phase 3 idempotency retry — approval candidate

Local corrections only. **Do not execute this procedure until the corrected commit
and the current-state backup gate are approved.** No push, deployment, AWS access,
or existing-container restart was performed to prepare these corrections.

## Recorded deployment outcome and retry baseline

The first attempt published `60ba7141b1294679c1b0ee2eecb08558d2a1ee3f` and committed
migration 002. Duplicate submissions returned record ID **3**, conflicting content
returned 409, and invalid severity returned 422. The Prometheus verifier then
failed because Amazon Linux 2023's Python **3.9.25** rejects nanosecond fractions
passed directly to `datetime.fromisoformat`. The retained-image rollback restored
the prior application; its copy of the same verifier also failed. Subsequent
compatible read-only verification confirmed the rollback, healthy containers,
and fresh successful scrapes from both jobs.

Last recorded remote state (2026-10-30, not freshly queried in this correction):

| Item | Expected retry state |
| --- | --- |
| GitHub main | `60ba7141b1294679c1b0ee2eecb08558d2a1ee3f` |
| EC2 checkout / running API | `e75a6a307ba1e9c91eef03b4b027963a5647fb7c`, old API, healthy |
| Retained/running API image | `sha256:db1294ca23a6e8fa5e4cc7d616682bdc54dece97b331d2ea3d67848b5edbc5ee` |
| PostgreSQL schema | Migration 002 already committed; nullable TEXT source_event_id and valid immediate unique constraint |
| Database | **Four records**, including the original two and two synthetic acceptance records |
| Existing keyed synthetic event | `source_event_id=phase3-rollout:60ba7141b1294679c1b0ee2eecb08558d2a1ee3f`, database ID **3** |
| Existing legacy synthetic event | One row for service phase3-synthetic-rollout with null source_event_id |
| Unaffected services | PostgreSQL, EventFlow, Prometheus, Grafana healthy; IDs/images/start times/restarts/mounts unchanged |

Do not repeat the old two-record gate, require zero keyed records, drop migration
002, reset identity values, remove synthetic history, or restore the old database.
A different current state is a stop-and-review condition, not an automatic repair.
The original rollout evidence remains in
`/opt/platform-engineering-lab/backups/phase3-rollout-60ba7141b1294679c1b0ee2eecb08558d2a1ee3f/`.

## Backup gate — current four-record state required

The existing encrypted archive is still a valid **historical** recovery point:

```text
/home/bigshadow1008/.local/share/platform-lab-backups/20261030T133949Z/postgres-backup-20261030T133949Z.tar.gz.cms
SHA-256: 491c1d847b437243be214b009af17c60faaccda3469626943ce5a15e14c66cd5
```

Its isolated restore had two records and no migration 002. It does **not** cover
the current schema, two synthetic additions, or keyed-event history. Before retry
cutover, secure a fresh encrypted off-instance backup of the current database
through the previously reviewed SSM method, compare source/transferred SHA-256,
verify decryption/internal checksums and isolated restoration, and record its
archive path/hash and schema/record fingerprints. Keep credentials/key material
outside Git and logs. No new backup is created during this local correction task.

Pause producers for the backup/cutover review. Bind the new backup's restored
four-record/schema fingerprint to the captured baseline below; counts alone do
not establish freshness. If any database records/objects change afterward, stop,
refresh the backup and obtain review of the new recovery point. Do not use the
old two-record archive to justify proceeding or restore it as application rollback.
The existing backup/recovery method needs no PostgreSQL restart or inbound port.

## Shared verification and compatibility review

`scripts/verify_phase3.py` is a self-contained Python **3.9+** standard-library
helper, used by rollout **and rollback**. Its RFC3339 parser accepts Z or explicit
offsets and 0–9 fractional digits, normalizes to UTC, and truncates only digits
beyond microseconds before calling fromisoformat. This is conservative for freshness
by less than one microsecond. Malformed/naive timestamps fail explicitly; missing,
DOWN, errored, or stale jobs cannot pass. Both jobs must have a successful scrape
newer than the start of the check. No timestamp parsing is duplicated in shell
heredocs, and parsing errors must not be mistaken for proof of an application fault.

The helper captures IDs and digests of **every column of all four records**, plus
schema/constraint flags, prior synthetic identity/history, and the identity
sequence. Snapshot queries use UTC. Before/after comparisons reject changed data,
missing constraints, unexpected counts/history, or a sequence moving backwards.
Retry attempts may advance the sequence without new rows: ON CONFLICT consumes
identity values. Sequence gaps are normal and must not trigger rollback.

Acceptance reuses the exact previously approved event/payload and expects ID **3**
on every successful response. It checks 409 and 422, then verifies that all four
row fingerprints remain unchanged. It does not POST another unkeyed legacy event
or generate a new source ID from the corrected release SHA. Acceptance is therefore
repeatable after a monitoring failure without increasing the row count.

Host-side scripts reviewed: this helper, infra/deploy/check-config.py, the existing
smoke/observability/backup/deployment scripts, and every Bash/Python block in this
runbook. No datetime.UTC, hashlib.file_digest, union-type runtime evaluation,
structural matching, or other Python 3.10+ host dependency is needed. The FastAPI
image remains Python 3.12; the Windows collector remains Python 3.10+ and is not
run on the EC2 host. Python 3.9 tests exercise the actual helper, not a copy of its
parser. Raw metadata comparison must sort Docker Mounts by Destination.

## Risks and scope

Single-ingestor replacement causes a short outage; build before stopping it and
keep producers paused through acceptance/rollback. The retry does **not** execute
DDL, restart PostgreSQL, or invoke db-init. Missing/invalid migration 002 stops
preflight instead of silently running a migration. Capture/preserve the exact
running image for rollback. Keep the other four services running and all volumes.
Require at least 256 MiB available memory and 6 GiB free disk, with additional build
headroom; stop on OOM, restart, or low resources. Do not resize Terraform resources.

Legacy clients without source IDs still have insert-per-request behavior. Old
unkeyed history cannot be retroactively deduplicated. Do not relabel collector
outboxes or enable real Windows collection. Database restoration/deletion would
remove deduplication history and later records; application rollback retains both.

## 1. Proposed publication and access — after approval only

Substitute the full corrected commit SHA reported with this plan in both shells.
The pre-push remote branch is expected to be the first attempt's reviewed release.

```bash
release='FULL_APPROVED_CORRECTED_COMMIT_SHA'
test "$(git rev-parse HEAD)" = "$release"
test -z "$(git status --porcelain)"
test "$(git ls-remote origin refs/heads/main | cut -f1)" = 60ba7141b1294679c1b0ee2eecb08558d2a1ee3f
git push origin "$release:refs/heads/main"
test "$(git ls-remote origin refs/heads/main | cut -f1)" = "$release"
test "$(aws sts get-caller-identity --profile platform-lab --region us-east-2 --query Account --output text)" = 598387452073
aws ssm start-session --profile platform-lab --region us-east-2 --target i-0ae38585502b57a05
```

No force push, IAM/security-group changes, public access, or general deploy.sh run.

## 2. EC2 preflight, baseline and build — old API stays up

Complete the new encrypted-backup gate and pause producers before cutover. In the
approved SSM shell:

```bash
sudo -i
set -euo pipefail
umask 077
test -f /etc/platform-engineering-lab-aws
cd /opt/platform-engineering-lab
release='FULL_APPROVED_CORRECTED_COMMIT_SHA'
previous=e75a6a307ba1e9c91eef03b4b027963a5647fb7c
test "$(git rev-parse HEAD)" = "$previous"
test -z "$(git status --porcelain)"
git fetch origin "$release"
test "$(git rev-parse FETCH_HEAD)" = "$release"
git merge-base --is-ancestor "$previous" "$release"
git checkout --detach "$release"
python3 infra/deploy/check-config.py
dc=(docker compose --project-directory /opt/platform-engineering-lab --env-file /opt/platform-engineering-lab/.env -f compose.yaml -f infra/deploy/compose.aws.yaml)
"${dc[@]}" config --quiet
"${dc[@]}" ps --all
python3 - <<'PY'
from pathlib import Path
import os
available_kib = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))
fs = os.statvfs('/opt/platform-engineering-lab')
assert available_kib >= 256 * 1024 and fs.f_bavail * fs.f_frsize >= 6 * 1024**3
print('PASS: minimum memory/disk gates; monitor additional build headroom')
PY
"${dc[@]}" exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
record="/opt/platform-engineering-lab/backups/phase3-rollout-$release"
test ! -e "$record"
mkdir -m 700 "$record"
# Retain this standalone helper before any checkout of the old revision.
install -m 600 scripts/verify_phase3.py "$record/verify_phase3.py"
python3 "$record/verify_phase3.py" capture --out "$record/baseline.json"
python3 "$record/verify_phase3.py" prometheus
printf '%s\n' "$previous" > "$record/previous-revision.txt"
container=$("${dc[@]}" ps -q log-ingestor)
test -n "$container"
old_image=$(docker inspect --format '{{.Image}}' "$container")
test "$old_image" = sha256:db1294ca23a6e8fa5e4cc7d616682bdc54dece97b331d2ea3d67848b5edbc5ee
rollback_image="platform-lab-log-ingestor-rollback:phase3-$release"
docker image tag "$old_image" "$rollback_image"
printf '%s\n' "$old_image" > "$record/previous-image-id.txt"
printf 'services:\n  log-ingestor:\n    image: %s\n' "$rollback_image" > "$record/rollback-image.yaml"
for service in postgres eventflow prometheus grafana log-ingestor; do
  cid=$("${dc[@]}" ps -q "$service")
  test -n "$cid"
  test "$(docker inspect --format '{{.State.Health.Status}}' "$cid")" = healthy
  docker inspect --format '{{.Id}} {{.Image}} {{.State.StartedAt}} {{.RestartCount}} {{json .Mounts}}' "$cid" > "$record/$service-before.txt"
done
"${dc[@]}" build log-ingestor
python3 "$record/verify_phase3.py" compare --baseline "$record/baseline.json"
```

Monitor memory/disk/OOM/container state during the build in a second SSM shell.
If build/preflight fails, stop before stopping the old API. Confirm the backup's
restored current-state fingerprint matches baseline.json; keep that backup proof
with the protected rollout record. Do not print environment files or event data.

## 3. Cutover — migration already applied, no SQL migration execution

```bash
"${dc[@]}" stop -t 30 log-ingestor
"${dc[@]}" exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
python3 "$record/verify_phase3.py" compare --baseline "$record/baseline.json"
"${dc[@]}" up -d --no-deps --no-build --pull never --force-recreate --wait --wait-timeout 120 log-ingestor
python3 "$record/verify_phase3.py" accept --baseline "$record/baseline.json"
python3 "$record/verify_phase3.py" prometheus
"${dc[@]}" exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
"${dc[@]}" ps --all
for service in postgres eventflow prometheus grafana; do
  cid=$("${dc[@]}" ps -q "$service")
  docker inspect --format '{{.Id}} {{.Image}} {{.State.StartedAt}} {{.RestartCount}} {{json .Mounts}}' "$cid" > "$record/$service-after.txt"
done
```

Compare the four unaffected services against before records, sorting mounts by
Destination. IDs, images, start times, restarts, and mounts must match. Monitor for
at least 15 minutes: available memory/disk, OOM/restarts, ingestor readiness, and
fresh successful Prometheus scrapes. Reuse the same helper for every check. Re-run
`compare` at the end; re-running `accept` is safe and retains exactly four rows.
Record the ingestor outage and planned scrape gap. No real collector is enabled.

Any failed check stops further rollout actions. After cutover has started, restore
the old image using section 4. Record the failed operation and sanitized exception
message/traceback location; do not suppress all diagnostic context as in the first
attempt. Never include credentials, raw event bodies, or full environments in logs.

## 4. Rollback — restore first, verify separately

Keep producers paused. The corrected helper was copied into the protected record,
so it remains usable after checking out e75a6a3, which does not contain that script.
This avoids both duplicated timestamp code and a missing-file failure on rollback.

```bash
"${dc[@]}" stop -t 30 log-ingestor
test "$(docker image inspect --format '{{.Id}}' "$rollback_image")" = "$(cat "$record/previous-image-id.txt")"
git checkout --detach "$previous"
docker compose --project-directory /opt/platform-engineering-lab --env-file /opt/platform-engineering-lab/.env -f compose.yaml -f infra/deploy/compose.aws.yaml -f "$record/rollback-image.yaml" up -d --no-deps --no-build --pull never --force-recreate --wait --wait-timeout 120 log-ingestor
printf 'previous-image-restored\n' > "$record/rollback-restoration.status"
"${dc[@]}" exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
curl --noproxy '*' --fail --silent http://127.0.0.1:8002/ready
python3 "$record/verify_phase3.py" compare --baseline "$record/baseline.json"
python3 "$record/verify_phase3.py" prometheus
"${dc[@]}" ps --all
```

Also compare the unaffected services' recorded states. Report **restoration status**
and **verification status** separately: a failed read-only check is not evidence
that the old image failed to start. Keep the restored old service running while
investigating verification problems; do not recursively recreate it. No database
restore, migration removal, identity reset, data deletion, volume operation,
PostgreSQL restart, or Terraform change belongs in this rollback. The old API is
not idempotent; collectors remain disabled after rollback.

## Local validation and scope

Run `bash scripts/test-phase3-local.sh` for all collector, ingestion, and verifier
tests on a new disposable local PostgreSQL instance. Verify the host helper and
its standard-library tests under a real Python 3.9 runtime as well as the local
Python version. Configuration, host-script/runbook syntax, and private-artifact
exclusions are checked before the local commit. These tests create/remove only
disposable local test containers; they do not restart existing containers.

The next deployment requires approval of the corrected commit and verified current
four-record backup. These corrections do not push, deploy, modify AWS, restart
existing containers, or collect real Windows logs. Historical two-record backup
and first-attempt records remain preserved and explicitly historical.

Local validation result: **41 tests passed** (16 collector, 12 PostgreSQL/API,
13 host-verifier regressions). All 13 host regressions also passed on actual
Python **3.9.25**, including nanosecond fractions and the standalone retained
rollback helper. Both Compose configurations, host/runbook syntax, source
whitespace, and targeted credential/private-artifact exclusion checks passed.
The fresh four-record off-instance backup remains a prerequisite for the next
approved deployment, not an action completed by these local corrections.

To reproduce the Python 3.9 regression run without accessing AWS or existing
containers (new disposable runtime, read-only code mounts, no runtime network):

```bash
docker run --rm --network none --read-only --tmpfs /tmp \
  --mount "type=bind,source=$PWD/scripts,target=/workspace/scripts,readonly" \
  --mount "type=bind,source=$PWD/infra/deploy,target=/workspace/infra/deploy,readonly" \
  --mount "type=bind,source=$PWD/tests,target=/workspace/tests,readonly" \
  --workdir /workspace python:3.9-slim \
  python -B -m unittest discover -s tests -p test_rollout_verification.py -v
```
