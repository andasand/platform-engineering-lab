# Phase 2 deployment preparation

Prepared 2026-10-29. **Awaiting approval: no push, source transfer, image build,
container deployment, database initialization, or infrastructure change performed.**
This report supersedes the handoff's statement that the deployment source is
uncommitted once the commit containing this report is created. Use that full Git
commit ID as the proposed release; it remains local until publication is approved.

## Source and secret review

Reviewed application instrumentation, the separate ingestor, schema, Compose,
observability configuration/checkers, deployment/backup scripts, infrastructure,
and documentation. The commit includes these together with the provider lock.
Historical infrastructure reports remain explicitly historical.

- Pattern scan of all 48 then-tracked/nonignored files found no AWS access keys,
  private keys, GitHub tokens, or credential-bearing URLs. Password-assignment
  matches were manually reviewed: placeholders, environment substitutions, and
  environment lookups. The ingestor's development fallback `postgres` is not a
  deployed credential; AWS Compose requires the generated password.
- Compared candidate content against actual local `.env` secrets and locally
  available shared AWS credential values, without printing them: no matches.
  Git history scan for AWS keys, private-key headers, and GitHub tokens passed.
  These targeted checks are not an exhaustive third-party secret audit.
- `.env`, variants, `.aws/`, PEM/key files, Terraform state/backups, plans, private
  variables, providers, and application backups are ignored. `.env.example`,
  `terraform.tfvars.example`, and `.terraform.lock.hcl` remain included.
- Removed tracked Python bytecode from the index; corrected cache ignore rules.
  The local cache file is preserved. Docker build exclusions also cover private
  environment/credential files and all `infra/` artifacts.
- Actual `.env`, state, applied plan, and private Terraform variables are mode
  `0600`. None belongs in the Git revision or deployment source archive.
- Reports contain operational account/resource IDs and a budget contact email;
  these are identifying metadata, not authentication secrets. Publication still
  needs approval. Do not upload the whole working directory.

## Live EC2 evidence through SSM

STS verified profile `platform-lab`, account `598387452073`, caller
`platform-lab-admin`. Session Manager connected to `i-0ae38585502b57a05` in
`us-east-2`. Read-only shell inspection at **2026-10-29 19:10:41 UTC** reported:

| Check | Result |
| --- | --- |
| RAM | 1,909 MiB total; 283 MiB used; **1,459 MiB available** |
| Swap | None |
| Root / Docker filesystem | 30 GiB; 2.3 GiB used; **28 GiB available**, 8% used |
| Inodes | 1% used |
| Docker images / containers / volumes / build cache | All zero |
| Docker directory | 160 KiB |
| Docker and SSM agent | Active |
| cloud-init / AWS marker | Done / present |

The SSM session was closed. No packages, services, permissions, or files on EC2
were changed. These figures are a point-in-time baseline, not a load test.

## Memory and disk estimate

The infrastructure guide records prior local idle observations. The ranges
below are engineering allowances for a light lab workload, not measured AWS
peaks or configured container limits. Docker working-set figures may exclude
reclaimable cache; PostgreSQL's 37 MiB idle observation is especially unsuitable
as its full budget.

| Long-running service | Recorded local idle MiB | Planning allowance MiB |
| --- | ---: | ---: |
| EventFlow, one Uvicorn worker | 44 | 64–128 |
| Security Log Ingestor, one worker | 53 | 96–160 |
| PostgreSQL 16.15 | 37 | 192–320 |
| Prometheus, two 15-second scrape targets | 140 | 192–320 |
| Grafana 13.3.0 | 611 | 640–768 |
| **Application total** | **985** | **1,184–1,696** |

Reserve another roughly 350–500 MiB for Linux, Docker, SSM and transient host
work: estimated steady total **1,534–2,196 MiB**, before build/startup peaks.
The one-shot initializer may add 32–64 MiB while running. Serial Python builds
need additional unmeasured memory; they run before first-start services, but on
updates the existing services remain active during builds.

**Assessment: disk passes; 2 GiB RAM is marginal.** A carefully monitored light
trial may fit, but the upper allowance exceeds available capacity and there is
no swap. No memory limits, swap, or instance changes were introduced. Prefer a
separately reviewed 4 GiB option if reliable headroom is required. Do not apply
Terraform to resize casually: inspect a fresh plan for replacement and data loss.

EventFlow keeps an unbounded in-memory event list, ingestor concurrency opens
database connections without a pool, and metric cardinality/query work can grow.
There is no defensible sustained-load capacity claim. PostgreSQL typically starts
with 128 MB shared buffers; per-operation work memory and concurrent sessions
add to that ([PostgreSQL 16 memory documentation](https://www.postgresql.org/docs/16/runtime-config-resource.html)).

For a trial, monitor `free -m`, `sudo docker stats --no-stream`, `df -h`,
`sudo docker system df`, and kernel OOM messages during builds/startup and at
5/15/30 minutes. Stop progression for any OOM, restart loop, failing readiness,
or sustained available memory below 256 MiB. Require at least 10 GiB free before
initial builds and pause growth below 6 GiB free; these are operator guardrails,
not automatic enforcement. Record actual peaks before approving regular use.

Plan approximately 3–6 GiB for images/build cache, 3–4 GiB for Prometheus including
head/WAL, 1 GiB for initial PostgreSQL/WAL, 0.5–1 GiB Grafana, and at least 4 GiB
for initial backup staging, alongside the existing OS footprint. These are initial
allowances only. PostgreSQL events/backups have no retention or size cap.
Prometheus's 2 GB retention is not a filesystem quota or RAM limit; head/WAL
needs extra space ([Prometheus storage](https://prometheus.io/docs/prometheus/3.9/storage/)).
Keep enough free space for the next complete backup; do not prune rollback images
or volumes to resolve pressure without reviewing what will be lost.

## PostgreSQL persistence and backups

PostgreSQL mounts `platform-engineering-lab_postgres_data` at
`/var/lib/postgresql/data`; the fixed Compose project name keeps volume selection
stable. It has no published host port. AWS does not mount or migrate the existing
local database volume. The additive SQL transaction preserves existing records
but does not upgrade an incompatible existing schema. `/ready` checks required
columns; application rollback cannot automatically reverse a schema migration.

All three persistent volumes and on-host backups live on encrypted root EBS.
Reboots/ordinary container recreation retain them, but instance replacement or
termination deletes them (`delete_on_termination=true`). Encryption does not
provide a backup. Never use `down -v` or remove volumes for troubleshooting.

`infra/deploy/backup.sh` creates a custom-format PostgreSQL dump, saved `.env`,
revision, Compose version, image references, and Grafana/Prometheus tar archives
with checksums. It runs as root with restrictive umask and backup directory 0700.
PostgreSQL stays live; `pg_dump` provides a consistent single-database snapshot
([PostgreSQL pg_dump](https://www.postgresql.org/docs/16/app-pgdump.html)). Grafana
and Prometheus stop briefly for consistent volume archives and restart via an
EXIT trap; verify readiness after restart because the trap does not wait for it.

Limitations: no automated schedule, off-host copy, encryption of exported files,
retention, WAL archiving/PITR, or tested recovery time. The dump omits cluster-wide
roles/tablespaces; the current single database/user can be recreated from the
saved environment, but future extra roles require a global-object backup.
The backup does not include image binaries, exact image IDs, or source; preserve
those separately for deterministic rollback. The three service backups are not
one cross-service transaction. RPO is the last successfully verified off-host
dump; RTO remains unknown until a restore rehearsal.

Before updates take a backup; after first deployment take and restore-test one
before relying on the data. Follow the off-host SSM tunnel/download steps in
the [AWS guide](../infra/terraform/aws/README.md#back-up-before-replacement-or-destroy),
verify `SHA256SUMS` at both ends, and place exported secrets/dumps in encrypted
storage. Securely copy local Terraform state separately as an infrastructure
recovery artifact. No off-host state or application backup was created here.

## Deployment runbook — execute only after approval

1. Approve the local commit for publication and approve either a monitored 2 GiB
   trial or a separately planned capacity change. Publish the exact reviewed
   revision only after approval; no deployment checkout can fetch this local-only
   revision yet. Use a fresh STS identity check and recheck capacity through SSM.
2. Open the session with the handoff's explicit profile/region/instance. Clone
   the repository into `/opt/platform-engineering-lab` and detach at the approved
   full SHA. If an offline transfer is selected instead, use `git archive` of the
   commit, not a tar of the working tree; arrange authenticated transfer separately.
3. Confirm `git rev-parse HEAD`, clean checkout, completed bootstrap, correct AWS
   marker, no port conflicts, and no unexpected volumes. Do not copy local `.env`.
   Run read-only `python3 infra/deploy/check-config.py` before deployment.
4. Run `sudo bash infra/deploy/deploy.sh` while monitoring capacity in another SSM
   session. It creates root-only random secrets once, builds the applications
   serially, starts PostgreSQL, runs additive initialization, and starts services.
   Existing secrets/volumes are preserved on reruns. It then runs smoke and
   observability checks; smoke testing retains one database event.
5. Require five healthy running services and a successful exited initializer,
   both app health/metrics endpoints, EventFlow event round trip, ingestor
   readiness/validation/insertion, both Prometheus targets UP, Grafana datasource
   health, and all dashboard queries. Review exit codes and bounded service logs.
   Allow expected 404/422 traffic generated by the observability checker.
6. Forward Grafana `3000 -> 13000` using the handoff's SSM command and inspect
   the dashboard. Observe resource use for at least 30 minutes, record container
   image IDs/digests and capacity, then take/export/verify a backup and rehearse
   restoration. Keep public ingress closed. Stop for any failed acceptance check.

For every remote Compose command, from the checkout use this shell array:

```bash
cd /opt/platform-engineering-lab
dc=(sudo docker compose --project-directory /opt/platform-engineering-lab --env-file /opt/platform-engineering-lab/.env -f compose.yaml -f infra/deploy/compose.aws.yaml)
"${dc[@]}" ps --all
```

Do not print full resolved Compose configuration or container environments; they
contain passwords. Private Git authentication must not be embedded in URLs.

## Rollback runbook — preserve records and credentials

**First deployment:** there is no prior deployed application revision. If a
build fails, stop and inspect the build error; it occurs before first database
startup. If startup/checks fail, capture status/OOM evidence, then use:

```bash
"${dc[@]}" stop eventflow log-ingestor prometheus grafana
```

Retain PostgreSQL, `.env`, and all volumes for diagnosis and backup. If PostgreSQL
is itself exhausting resources, stop it gracefully too. Do not check out the old
repository HEAD as a supposed working AWS release: it lacks this deployment stack.
Correct the candidate and seek approval for the revised release.

**Future updates:** before building, record the previous full SHA, take/verify an
off-host backup, and tag the exact running application images under the old SHA:

```bash
previous=$(sudo git rev-parse HEAD)
for svc in eventflow log-ingestor; do
  cid=$("${dc[@]}" ps -q "$svc")
  image_id=$(sudo docker inspect --format '{{.Image}}' "$cid")
  sudo docker image tag "$image_id" "platform-lab-rollback-$svc:$previous"
done
```

Record exact IDs/digests for PostgreSQL, Grafana and Prometheus as well. Keep the
previous images, or save them to protected off-host storage; mutable upstream
tags and Python base images make rebuilding an old commit insufficient for exact
rollback. The current backup script's image list alone is insufficient.

For an application-only update with compatible schema, stop both applications,
detach the checkout at that recorded prior SHA, and restore their Compose tags
from the retained images before starting them without builds or pulls:

```bash
"${dc[@]}" stop eventflow log-ingestor
sudo git checkout --detach "$previous"
for svc in eventflow log-ingestor; do
  sudo docker image tag "platform-lab-rollback-$svc:$previous" "platform-engineering-lab-$svc:latest"
done
"${dc[@]}" up -d --no-deps --no-build --pull never --wait --wait-timeout 240 eventflow log-ingestor
sudo python3 scripts/smoke-test.py
sudo python3 scripts/observability-check.py
```

Do not run `deploy.sh` for this exact-image rollback: it rebuilds applications.
Infrastructure, PostgreSQL major-version, observability-format, or incompatible
schema changes need their own reviewed recovery procedure. Preserve writes since
the backup; restoring an old database discards newer records and requires explicit
data-loss approval. EventFlow events are inherently lost when its process stops.

## Restore rehearsal — future approved deployment only

After the first successful backup, verify checksums and archive readability.
The following creates a separate empty rehearsal database on the deployed server;
run during an approved maintenance window with enough disk/memory. It does not
redirect applications or overwrite the live database. Set the exact verified dump:

```bash
dump=/opt/platform-engineering-lab/backups/<UTC-timestamp>/postgres.dump
"${dc[@]}" exec -T postgres sh -c 'createdb -U "$POSTGRES_USER" phase2_restore_check'
sudo cat "$dump" | "${dc[@]}" exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d phase2_restore_check --no-owner --no-acl --exit-on-error --single-transaction'
"${dc[@]}" exec -T postgres sh -c 'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d phase2_restore_check -c "SELECT count(*) FROM public.security_events;" -c "SELECT pg_get_serial_sequence('\''public.security_events'\'', '\''id'\'');"'
```

Compare restored counts/schema/sample marker with the backup snapshot, not a live
database accepting newer writes. Validate identity-sequence continuity before
promotion. Leave the rehearsal database for review; cleanup is a separate action.
For full host recovery, use a separately approved fresh host and empty volumes,
restore saved `.env` root-owned 0600, recreate the original database/user, restore
the dump before running the initializer, and extract Grafana/Prometheus archives
with original numeric ownership while those services are stopped. Never overlay
existing populated volumes. Verify all health/dashboard checks and record RTO.

## Preparation validation

AWS Compose merge assertions, shell syntax checks, source whitespace checks,
Python syntax compilation, candidate/archive exclusion checks, and the targeted
secret review are the preparation checks. Runtime image compatibility, load,
smoke tests, and backup restoration remain unverified on EC2 because deploying
containers is outside this phase's approval boundary.
