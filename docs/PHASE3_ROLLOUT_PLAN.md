# Phase 3 idempotency rollout — approval candidate

Prepared locally on 2026-10-30. **Not executed. No push or AWS API calls in this
preparation task.** The commit containing this plan is the proposed release; use
the full SHA reported with the local commit, never a moving branch tip. The
[idempotency review](PHASE3_IDEMPOTENCY_REVIEW.md) describes the API and migration;
the [AWS handoff](AWS_DEPLOYMENT_HANDOFF.md) is a historical infrastructure record,
not evidence that applications remain undeployed. Last completed remote work
recorded all five services healthy at source revision
`e75a6a307ba1e9c91eef03b4b027963a5647fb7c`. No live AWS health is claimed here.

## Verified recovery point

Workstation archive, outside Git:

```text
/home/bigshadow1008/.local/share/platform-lab-backups/20261030T133949Z/postgres-backup-20261030T133949Z.tar.gz.cms
SHA-256: 491c1d847b437243be214b009af17c60faaccda3469626943ce5a15e14c66cd5
```

This preparation rehashed the actual archive and compared it with the recorded
value, `SHA256SUMS`, and `VERIFICATION.json`. Directories are 0700; private key and
certificate exist separately with mode 0600. Recorded authenticated decryption,
internal checksums, and full isolated PostgreSQL 16 restore passed; the restored
`security_events` table contained **2 records**, with no Phase 3 column. Recovery
instructions are `RECOVERY.md` beside the archive. No credentials/key material are
part of this commit. Back up the recovery key separately to trusted storage.

The recovery point is dated, not a continuously updated backup. At cutover pause
writers and compare the current table with the recorded recovery point. If any
records or other database objects have changed since that snapshot, obtain a
fresh encrypted off-instance backup and verify it before migration. Do not treat
matching counts alone as proof that the entire database is unchanged. The SQL
count gate below is an additional check, not a substitute for that review.

## Risks and acceptance boundary

| Risk | Control / limitation |
| --- | --- |
| Ingestor downtime | One instance, no rolling replica. Pause all producers; build first, then stop/recreate only log-ingestor. Expect seconds to minutes; the 120-second readiness deadline is a stop threshold, not a downtime guarantee. |
| PostgreSQL write blocking | ALTER TABLE/unique-index build takes locks. Migration uses 5-second lock and 60-second statement timeouts; multiple statements mean the total outage can exceed 60 seconds. Retain PostgreSQL running. |
| Resource pressure | t3.small has 2 GiB RAM. Check disk, free memory, and OOM/restarts; require at least 256 MiB available and 6 GiB free as minimum existing guardrails, with additional build/index headroom. Stop if insufficient; do not resize infrastructure in this rollout. |
| Data loss | Migration only adds a nullable column/constraint. No backfill, row updates, deletes, or volume operations. Rollback retains new data and schema. Restoring an older database loses later writes and requires separate recovery approval. |
| Duplicate legacy events | Missing/null IDs still insert separately. Old null-ID history is not retroactively deduplicated. Do not replay it as newly keyed data. |
| Duplicate keyed events | Retries must preserve source ID and full payload; reuse of aliases/log generations or resetting IDs defeats the identity design. Record deletion or old-backup restoration removes deduplication history. |
| Mixed versions | Old API silently ignores the new field. Keep collectors/producers paused throughout migration, verification, and rollback. New collector capability checks reduce risk but cannot protect a mid-batch server downgrade. |
| API compatibility | Existing request/response shapes remain valid; keyed ACK adds source_event_id, changed content returns 409, invalid input 422. Naive timestamps now explicitly mean UTC; clients should send offsets. |
| Monitoring gap | Prometheus can observe log-ingestor DOWN while it is stopped; EventFlow should remain UP. Require both targets UP after restart and review the planned gap. |
| Backup scope | PostgreSQL-only backup, not a simultaneous Grafana/Prometheus snapshot or complete image archive. Preserve the exact running ingestor image before building. |

Approval covers publishing the reviewed commit, an ingestor-only image build and
replacement, migration 002 on the existing database, and the synthetic acceptance
requests below (one keyed and one legacy record retained). It does **not** enable
real Windows collection or ongoing collector-to-AWS delivery. No Terraform,
security-group, IAM, database restart, other-service recreation, or volume deletion.
Do not run the general `infra/deploy/deploy.sh` for this targeted rollout.

## 1. Proposed publication and access — only after approval

On WSL2 set the full approved SHA from the completion report. This is the only
release substitution required; use that same value in the EC2 shell below.

```bash
release='FULL_APPROVED_PHASE3_COMMIT_SHA'
git status --porcelain
test "$(git rev-parse HEAD)" = "$release"
test -z "$(git status --porcelain)"
git push origin "$release:refs/heads/main"
test "$(git ls-remote origin refs/heads/main | cut -f1)" = "$release"
test "$(aws sts get-caller-identity --profile platform-lab --region us-east-2 --query Account --output text)" = 598387452073
aws ssm start-session --profile platform-lab --region us-east-2 --target i-0ae38585502b57a05
```

No force push. If GitHub has advanced, stop and review/revalidate the release
instead of merging new content into this approval. Do not print credentials,
full Compose configuration, container environments, or raw event records.

## 2. EC2 preflight and build — no application outage yet

Run inside the approved SSM shell as root. Ensure all producers are paused and
the backup freshness review above is complete before progressing to section 3.
No collector is intentionally active in the current design; verify this with the
operator rather than assuming no other client can write.

```bash
sudo -i
set -euo pipefail
umask 077
test -f /etc/platform-engineering-lab-aws
cd /opt/platform-engineering-lab
release='FULL_APPROVED_PHASE3_COMMIT_SHA'
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
free -m
df -h /opt/platform-engineering-lab
"${dc[@]}" exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
psql_live() {
  "${dc[@]}" exec -T postgres sh -c 'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"' sh "$@"
}
psql_live -At -c 'SELECT 1; SELECT to_regclass('\''public.security_events'\''); SELECT pg_size_pretty(pg_total_relation_size('\''public.security_events'\''));'
record="/opt/platform-engineering-lab/backups/phase3-rollout-$release"
test ! -e "$record"
mkdir -m 700 "$record"
printf '%s\n' "$previous" > "$record/previous-revision.txt"
container=$("${dc[@]}" ps -q log-ingestor)
test -n "$container"
old_image=$(docker inspect --format '{{.Image}}' "$container")
rollback_image="platform-lab-log-ingestor-rollback:$previous"
docker image tag "$old_image" "$rollback_image"
printf '%s\n' "$old_image" > "$record/previous-image-id.txt"
printf 'services:\n  log-ingestor:\n    image: %s\n' "$rollback_image" > "$record/rollback-image.yaml"
for service in postgres eventflow prometheus grafana; do
  cid=$("${dc[@]}" ps -q "$service")
  test -n "$cid"
  docker inspect --format '{{.Id}} {{.Image}} {{.State.StartedAt}} {{.RestartCount}} {{json .Mounts}}' "$cid" > "$record/$service-before.txt"
done
"${dc[@]}" build log-ingestor
```

Monitor memory/disk during the build in a second SSM shell. If it fails or causes
OOM/restarts, stop; the old ingestor is still running unless resource pressure
interrupted it. Investigate before touching the schema. Do not prune rollback
images/volumes or print build environments. The running images of PostgreSQL,
EventFlow, Grafana, and Prometheus are not rebuilt or pulled by these commands.

## 3. Short maintenance window: pause writes, migrate, replace ingestor

`001_security_events.sql` is already installed on this database. New empty
environments use 001 then 002 through db-init; this existing deployment applies
only 002 after verifying the original table. Direct execution uses the running
PostgreSQL container/client and avoids starting a different db-init image.

```bash
"${dc[@]}" stop -t 30 log-ingestor
"${dc[@]}" exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
psql_live -At -c 'SELECT count(*), coalesce(min(id),0), coalesce(max(id),0) FROM public.security_events;' > "$record/rows-before.txt"
test "$(cut -d '|' -f1 "$record/rows-before.txt")" = 2
# If this gate fails, use section 5 to resume the old API; refresh/reverify the
# off-instance backup and approve the new recovery point before retrying.
psql_live < database/migrations/002_source_event_id.sql
psql_live -At -c 'SELECT count(*), coalesce(min(id),0), coalesce(max(id),0) FROM public.security_events;' > "$record/rows-after.txt"
cmp "$record/rows-before.txt" "$record/rows-after.txt"
psql_live -At -c "SELECT count(*) FROM pg_constraint WHERE conrelid='public.security_events'::regclass AND conname='security_events_source_event_id_key' AND contype='u' AND convalidated AND NOT condeferrable;" | grep -qx 1
psql_live -At -c 'SELECT count(*) FROM public.security_events WHERE source_event_id IS NOT NULL;' | grep -qx 0
"${dc[@]}" up -d --no-deps --no-build --pull never --force-recreate --wait --wait-timeout 120 log-ingestor
```

Stop on any failure. Do not run the new image if migration/row preservation checks
fail. PostgreSQL stays running. If migration times out, its transaction rolls back;
use the old retained ingestor image to resume service while investigating. If the
migration succeeds but the new API fails, leave the additive schema in place and
follow section 5. Never drop the column/index as application rollback.

## 4. Acceptance — exactly two labeled synthetic records

The commands below retain one keyed record and one legacy record. Reusing the
same rollout ID makes the keyed check repeatable, but the legacy check adds a row
per run. Count verification is written for the **first** acceptance run only.

```bash
export PHASE3_RELEASE="$release"
python3 - <<'PY'
import json, os
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
opener = build_opener(ProxyHandler({}))
def request(path, payload=None, expected=200):
    body = None if payload is None else json.dumps(payload).encode()
    req = Request('http://127.0.0.1:8002' + path, data=body, headers={'Content-Type':'application/json'})
    try:
        with opener.open(req, timeout=15) as response:
            code, data = response.status, response.read()
    except HTTPError as exc:
        code, data = exc.code, exc.read()
    assert code == expected, f'Unexpected status: {code}'
    return json.loads(data)
assert request('/health')['status'] == 'healthy'
assert request('/ready')['capabilities']['source_event_id'] == 'v1'
event = dict(source_event_id='phase3-rollout:' + os.environ['PHASE3_RELEASE'],
             timestamp='2026-10-30T00:00:00Z', service='phase3-synthetic-rollout',
             event_type='idempotency_check', severity='INFO', source_ip=None,
             username=None, message='Synthetic Phase 3 rollout acceptance')
first = request('/events', event)
assert first == request('/events', event)
assert first['source_event_id'] == event['source_event_id']
request('/events', dict(event, message='Synthetic conflicting content'), expected=409)
request('/events', dict(event, severity='invalid'), expected=422)
legacy = dict(event)
legacy.pop('source_event_id')
assert 'source_event_id' not in request('/events', legacy)
print('PASS: readiness, original-ID replay, conflict, validation, legacy compatibility')
PY
psql_live -At -c 'SELECT count(*) FROM public.security_events;' | grep -qx 4
psql_live -At -c "SELECT count(*) FROM public.security_events WHERE source_event_id='phase3-rollout:$release';" | grep -qx 1
"${dc[@]}" exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
"${dc[@]}" ps --all
python3 - <<'PY'
import json, time
from datetime import datetime, timezone
from urllib.request import ProxyHandler, build_opener
opener = build_opener(ProxyHandler({}))
fresh_after = datetime.now(timezone.utc)
for attempt in range(12):
    with opener.open('http://127.0.0.1:9090/api/v1/targets?state=active', timeout=10) as response:
        targets = {t['labels']['job']:t for t in json.load(response)['data']['activeTargets']}
    if all(targets.get(job,{}).get('health') == 'up' and not targets[job]['lastError'] and datetime.fromisoformat(targets[job]['lastScrape'].replace('Z','+00:00')) >= fresh_after for job in ('eventflow','log-ingestor')):
        print('PASS: both Prometheus targets UP'); break
    time.sleep(5)
else:
    raise RuntimeError('Prometheus targets did not recover')
PY
for service in postgres eventflow prometheus grafana; do
  cid=$("${dc[@]}" ps -q "$service")
  docker inspect --format '{{.Id}} {{.Image}} {{.State.StartedAt}} {{.RestartCount}} {{json .Mounts}}' "$cid" > "$record/$service-after.txt"
done
```

Compare each before/after record: IDs, images, start times, restart counts, and
mount destinations/sources must match for the four untouched services. Docker may
return Mounts in different order; compare sorted mounts, not raw list order.
Confirm ingestor healthy, monitor free memory/disk and OOM/restart evidence for at
least 15 minutes, and record the actual ingestor downtime and Prometheus gap.
Do not enable real Windows collection or a collector tunnel after acceptance.

## 5. Rollback — retained image, retained schema/data

Keep producers paused. Use the same SSM shell variables/record directory. This
works before or after migration: the old image accepts the additive schema. It
does not guarantee idempotency; collectors must remain disabled.

```bash
"${dc[@]}" stop -t 30 log-ingestor
test "$(docker image inspect --format '{{.Id}}' "$rollback_image")" = "$(cat "$record/previous-image-id.txt")"
git checkout --detach "$previous"
docker compose --project-directory /opt/platform-engineering-lab --env-file /opt/platform-engineering-lab/.env -f compose.yaml -f infra/deploy/compose.aws.yaml -f "$record/rollback-image.yaml" up -d --no-deps --no-build --pull never --force-recreate --wait --wait-timeout 120 log-ingestor
"${dc[@]}" exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
curl --noproxy '*' --fail --silent http://127.0.0.1:8002/ready
"${dc[@]}" ps --all
```

Recheck both Prometheus targets using section 4's read-only target check. Record
the rollback image ID and reason. Keep any synthetic/new records and deduplication
history. No `down`, `down -v`, `--clean`, database restore, PostgreSQL restart, full
stack deploy, or Terraform command belongs in this rollback. An actual database
recovery uses the protected off-instance archive and a separately approved empty
destination with reconciliation of any post-backup writes.

## Local validation and stop boundary

`bash scripts/test-phase3-local.sh` runs all 16 collector and 11 PostgreSQL/API
tests on disposable local storage. Configuration assertions include private ports,
volume ownership, health dependencies, and baseline-before-migration ordering.
Python/JSON syntax, shell syntax, whitespace, and source exclusion checks are
also required before the local commit. No production image build, SSM access,
AWS health probe, push, migration, or deployment is performed by this plan task.
Commands above are a reviewable proposal and have not been executed remotely.

Preparation result: all **27 tests passed** (16 collector, 11 PostgreSQL/API),
both local and AWS-override Compose checks passed, and all repository Python/JSON,
shell/runbook syntax and whitespace checks passed. Targeted candidate checks
found no local environment-secret values, key/token patterns, or private backup
artifacts in the proposed source. The encrypted archive was rehashed successfully.
