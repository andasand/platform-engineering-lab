# Platform Engineering Lab

Phase 3's synthetic-only Windows security collector prototype and proposed private
SSM transport are documented in [the collector design](docs/PHASE3_WINDOWS_COLLECTOR.md).
The proposed additive ingestion migration and rollout compatibility are in
[the idempotency review](docs/PHASE3_IDEMPOTENCY_REVIEW.md).
Exact proposed rollout/rollback commands are in the
[Phase 3 approval plan](docs/PHASE3_ROLLOUT_PLAN.md).

A progressive hands-on lab for building, securing, automating, observing, and troubleshooting a production-style Kubernetes platform.

The project is designed around a simple learning cycle:

> **Build it → Break it → Explain it**

Rather than deploying a complete platform at once, each capability is introduced incrementally, tested, deliberately broken, diagnosed, and documented.

---

## Purpose

The primary goal of this project is to develop practical platform engineering skills while exploring how modern Kubernetes platforms can implement concepts described in U.S. Department of Defense DevSecOps and Zero Trust guidance.

The lab uses the **DoD Enterprise DevSecOps Fundamentals v2.5 (October 2024)** as current foundational guidance.

The **DoD Enterprise DevSecOps Reference Design: CNCF Kubernetes v2.1 (2021)** is used as an architectural reference for Kubernetes-based software factories.

The lab does **not** represent an authorized DoD environment or claim compliance, certification, or an Authority to Operate (ATO).

Instead, it provides an environment for experimenting with architecture, controls, automation, testing, telemetry, and evidence generation.

---

## Architecture

The platform currently runs locally using **kind (Kubernetes IN Docker)**.

```text
Developer
    |
    | kubectl
    v
Docker
    |
    v
kind Kubernetes Cluster
    |
    +-- Kubernetes Control Plane
    |      |
    |      +-- API Server
    |      +-- etcd
    |      +-- Scheduler
    |      +-- Controller Manager
    |
    +-- CoreDNS
    |
    +-- kindnet
    |
    +-- EventFlow Deployment
           |
           +-- Pod
           +-- Pod
           +-- Pod
                  |
                  v
             FastAPI :8000
```

The current application workload is **EventFlow**, a small FastAPI application intentionally kept simple so the focus remains on the platform.

---

## EventFlow

EventFlow currently provides a health endpoint:

```http
GET /health
```

Example:

```json
{
  "status": "healthy",
  "message": "EventFlow is running.",
  "hostname": "eventflow-76787b8f76-kjxl2"
}
```

The hostname makes it possible to observe which Kubernetes replica handles a request.

The application will gradually evolve as additional platform capabilities are introduced.

### Run EventFlow locally

From the repository root, use Python 3.12 and a virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another terminal, verify the application and Prometheus instrumentation:

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/metrics
```

Both endpoints should return HTTP 200. `/metrics` exposes Python/process metrics
and HTTP request counts and durations. Request `/health` before scraping metrics
to populate its request series. Interactive API documentation is at `/docs`.

The entry point is `app/main.py` (`app.main:app`). The root `requirements.txt`
pins FastAPI, Uvicorn, Pydantic, `prometheus-fastapi-instrumentator`,
`prometheus_client`, and their dependencies. EventFlow requires no database or
environment variables. `POST /events` accepts `source`, `event_type`, and numeric
`value`; `GET /events` lists events. Events are held in process memory and are
lost on restart; each worker or replica has its own list.

### Run EventFlow with Docker

```bash
docker build -t eventflow:local .
docker run --rm --name eventflow-local -p 127.0.0.1:8000:8000 eventflow:local
```

The root Dockerfile uses `python:3.12-slim`, installs the root requirements,
copies `app/`, and starts Uvicorn on `0.0.0.0:8000`. Use the same health and
metrics checks above. If the native server already occupies port 8000, stop it
with Ctrl+C or publish the container with `-p 127.0.0.1:8001:8000` and check
port 8001 instead.

For import errors, run commands from the repository root and use the virtual
environment's Python. For missing modules, install the root requirements into
that environment. For an occupied port, stop your existing server or choose
another port. Docker socket permission errors require access to the Docker
daemon. A missing Docker Buildx plugin may produce a warning and fall back to
the legacy builder; check the final build result.

### Separate security log ingestor

`services/log-ingestor/main.py` defines the separate `main:app` FastAPI app.
Its pinned requirements include FastAPI, Uvicorn, Pydantic, Psycopg 3 with its
binary distribution, and Prometheus instrumentation. Its Dockerfile uses
Python 3.12 slim and starts Uvicorn on container port 8000.

| Endpoint | Behavior |
| --- | --- |
| `GET /health` | Application liveness; does not require PostgreSQL |
| `GET /ready` | Checks database connectivity and required table/column access; 503 on failure |
| `POST /events` | Validates/commits an event; optional `source_event_id` deduplicates retries and returns the original ID; conflicting content returns 409 |
| `GET /metrics` | Prometheus process and HTTP metrics |

Severity values are normalized to uppercase and restricted to DEBUG, INFO,
WARNING, ERROR, or CRITICAL. Source IP accepts IPv4 or IPv6. Invalid input
returns 422 before database insertion. The existing response format is preserved.

### Run both applications with the existing PostgreSQL container

Prerequisites: Docker Engine with Compose and a running PostgreSQL 16 container.
The existing environment uses container `data-engineering-postgres`, database
`data_engineering`, user `pipeline_user`, and network
`data-engineering-lab_default`. Its existing volume is
`data-engineering-lab_postgres_data`. This repository's Compose configuration
does not own, recreate, or mount that database volume.

From the repository root:

```bash
# Only copy when .env does not already exist.
cp -n .env.example .env
chmod 600 .env
# Edit .env: set POSTGRES_PASSWORD to the EXISTING database user's password.
# Adjust the container hostname, database, user, and network if needed.
docker ps --filter name=data-engineering-postgres
docker network inspect data-engineering-lab_default
docker compose config --quiet
docker compose up --build -d --wait --wait-timeout 120
docker compose ps --all
```

`.env` is ignored by Git and excluded from Docker build contexts. Do not print
or commit credentials. Compose passes these settings to the ingestor and the
schema initializer; it does not change the existing database password. Native
Uvicorn does not automatically load `.env`.

Compose starts EventFlow at `http://127.0.0.1:8001` and the ingestor at
`http://127.0.0.1:8002`. Set `EVENTFLOW_PORT` or `LOG_INGESTOR_PORT` in `.env`
to choose other ports. Port 8001 avoids the native EventFlow server on 8000.
Both applications expose interactive API documentation at `/docs`.

The one-shot `db-init` service runs
`database/init/001_security_events.sql` with `ON_ERROR_STOP=1`; the ingestor
starts only after it succeeds. `db-init` exiting with code 0 is expected.
The initializer then applies the additive `database/migrations/002_source_event_id.sql`
before the updated ingestor starts. Existing rows retain null source identifiers;
producers that omit the identifier retain insert-per-request behavior.
The initializer creates `public.security_events` in the configured database
with an identity primary key, event timestamp, service/type/severity, optional
IP and username, message, raw JSONB, and insertion timestamp. It uses a
transaction and `CREATE TABLE IF NOT EXISTS`; no existing rows or tables are
removed or replaced. Reapplying it leaves existing events intact:

```bash
docker compose run --rm db-init
```

This explicitly initializes the existing database; PostgreSQL image startup
hooks are not relied on for a database that already contains data.
`IF NOT EXISTS` does not migrate an incompatible preexisting table. If schema
checks fail, inspect the table before making changes.

The PostgreSQL network is declared external, so the existing database must be
running before Compose starts. `docker compose down` stops and removes this
project's application containers and initializer, leaving the external database
and its network/volume in place. Do not remove or recreate the existing database
volume to troubleshoot initialization.

### Test the integrated services

```bash
curl -fsS http://127.0.0.1:8001/health
curl -fsS http://127.0.0.1:8001/metrics
curl -fsS http://127.0.0.1:8002/health
curl -fsS http://127.0.0.1:8002/ready
curl -fsS -H 'Content-Type: application/json' \
  -d '{"timestamp":"2026-10-29T12:00:00Z","service":"local-test","event_type":"login_failed","severity":"warning","source_ip":"192.0.2.10","username":"test-user","message":"Local integration test"}' \
  http://127.0.0.1:8002/events
curl -fsS http://127.0.0.1:8002/metrics
python3 scripts/smoke-test.py
```

The smoke test checks both applications, EventFlow event creation/retrieval,
database readiness, security ingestion with severity normalization and IPv6,
invalid severity/IP rejection, and HTTP metrics. Each run retains one labeled
security event in PostgreSQL and one in-memory EventFlow event. To test other
ports, set `EVENTFLOW_URL` and `LOG_INGESTOR_URL` for the script.

To inspect stored events using the existing container's local database access:

```bash
docker exec data-engineering-postgres sh -c \
  'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT id, event_time, service, severity, source_ip FROM public.security_events ORDER BY id DESC LIMIT 5;"'
```

For initialization failures, inspect `docker compose logs db-init`. For
readiness or insertion failures, inspect `docker compose logs log-ingestor`
and confirm the database hostname/network, credentials, and table columns.
A healthy `/health` with a failing `/ready` means the application is alive
but its database dependency is unavailable. Database connections have a
five-second connection timeout. `/events` database failures return 500.
If the database becomes unavailable after startup, readiness returns 503;
after fixing connectivity, it can recover without rebuilding the application.

For native ingestor development, install its requirements in a separate virtual
environment, export the `POSTGRES_*` settings (use `POSTGRES_HOST=127.0.0.1`
for the existing host-published database), and start from the repository root:

```bash
python -m uvicorn main:app --app-dir services/log-ingestor --port 8002
```

---

## Local Prometheus and Grafana

The Compose stack includes pinned images `prom/prometheus:v3.15.1` and
`grafana/grafana:13.3.0`. Application code, existing containers, and PostgreSQL
storage are unchanged. Prometheus joins both existing application networks:
the default network for EventFlow and the external database network for the
ingestor. Grafana connects to Prometheus on the default network.

| Service | Local address |
| --- | --- |
| EventFlow | http://localhost:8001 (API docs at `/docs`) |
| Security Log Ingestor | http://localhost:8002 (API docs at `/docs`) |
| PostgreSQL 16 | `localhost:5432`, database `data_engineering` |
| Prometheus | http://localhost:9090 (targets at `/targets`) |
| Grafana | http://localhost:3000 |
| Dashboard | http://localhost:3000/d/platform-lab-observability |

The new HTTP ports bind to loopback. PostgreSQL retains its existing binding.
Keep the database container running as described above. For a new checkout,
copy `.env.example` to `.env` only if `.env` does not exist, set the existing
database credentials, and set a unique `GF_SECURITY_ADMIN_PASSWORD`.
`GF_SECURITY_ADMIN_USER` defaults to `admin`. For an existing checkout, append
the Grafana settings to the existing `.env`; preserve all database settings.
Never commit `.env` or paste `docker compose config` output containing secrets.

```bash
chmod 600 .env
docker compose config --quiet
# Existing applications running: add only the two new services.
docker compose up -d --no-recreate --no-deps --wait --wait-timeout 180 prometheus grafana
# For a fresh application startup instead:
# docker compose up --build -d --wait --wait-timeout 180
docker compose ps --all
```

Sign in to Grafana with the administrator credentials configured in your local
`.env`, then open **Platform Engineering Lab — Observability** under the
**Platform Engineering Lab** folder. Credentials initialize Grafana on its first
startup; changing the environment later does not reset an existing user's
password. Use Grafana account settings to change it rather than removing its
volume.

Prometheus configuration is in `observability/prometheus/prometheus.yml`.
It scrapes `eventflow:8000/metrics` and `log-ingestor:8000/metrics` every
15 seconds. These are Docker service names and container ports, independent
of the host-published ports. Prometheus retains metrics for 15 days in the
`prometheus_data` named volume. Grafana stores its database and settings in
`grafana_data`. Both volumes survive container restarts and ordinary Compose
shutdowns. Do not use `docker compose down -v` or delete volumes.

Grafana automatically provisions its default `Prometheus` data source at
`http://prometheus:9090` from
`observability/grafana/provisioning/datasources/prometheus.yml` and loads the
dashboard from `observability/grafana/dashboards/platform-lab.json`. Edit the
dashboard JSON in the repository; UI changes are disabled to keep the saved
configuration reproducible. Dashboard files are reloaded every 15 seconds.

The dashboard uses these measured series:

| Panel | Calculation |
| --- | --- |
| Application request rate | `sum(rate(http_requests_total[5m]))`, filtered by job |
| Mean response latency | Rate of `http_request_duration_seconds_sum` divided by rate of `http_request_duration_seconds_count` |
| EventFlow HTTP error rate | Percentage of requests with `status="4xx"` or `status="5xx"` |
| Application health | Prometheus `up` for each scrape target: 1 is UP, 0 is DOWN |
| Availability over 5 minutes | `100 * avg_over_time(up[5m])` per application |

Request and latency panels exclude `/metrics`, `/health`, and `/ready` to keep
scrapes and health checks from dominating API traffic. Latency is the measured
mean, not a fabricated percentile. Scrape availability confirms metrics HTTP
access, not database readiness; check the ingestor's `/ready` for PostgreSQL
health. No historical availability is assumed before Prometheus starts.
Rates need at least two scrapes; idle application latency may show no data.

```bash
curl -fsS http://localhost:9090/-/ready
curl -fsS http://localhost:3000/api/health
python3 scripts/smoke-test.py
python3 scripts/observability-check.py
```

The existing smoke test retains one labeled database event per run.
`observability-check.py` generates about 45 seconds of API traffic, including
intentional 404 and 422 responses, without writing database records. It checks
both scrape targets and their 15-second intervals, data source health, dashboard
provisioning, and actual values for every panel through both Prometheus and
Grafana's data source proxy. It obtains Grafana credentials from the environment
or the resolved local Compose configuration without printing them. Override
`EVENTFLOW_URL`, `LOG_INGESTOR_URL`, `PROMETHEUS_URL`, or `GRAFANA_URL` when
using different local addresses.

The intentional 404s temporarily raise the EventFlow error percentage; this is
expected test traffic and falls out of the five-minute rate window.

For missing data, check Prometheus `/targets`, then application `/metrics` and
the container networks. Check `docker compose logs prometheus grafana` for
configuration or provisioning failures. After editing the Prometheus config,
validate and restart only Prometheus. Keep the existing data volumes:

```bash
docker compose exec prometheus promtool check config /etc/prometheus/prometheus.yml
docker compose restart prometheus
```

Configuration references: [Prometheus scraping](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)
and [Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/).

---

## AWS deployment with Terraform

The [AWS infrastructure guide](infra/terraform/aws/README.md) contains a
single-host Terraform configuration, an AWS-only Compose override, deployment
and backup scripts, cost estimates, and SSM access instructions. It reuses this
observability stack without changing the local services or database. No AWS
deployment is automatic. The approved infrastructure is applied; applications
remain undeployed. See the [Phase 2 approval review](docs/AWS_PHASE2_REVIEW.md)
for capacity findings, persistence review, and deployment/rollback instructions.

---

## Kubernetes Deployment

EventFlow currently runs as a three-replica Kubernetes Deployment.

```text
Client
   |
   | eventflow
   v
CoreDNS
   |
   v
Service
ClusterIP :80
   |
   v
EndpointSlice
   |
   +----> EventFlow Pod :8000
   +----> EventFlow Pod :8000
   +----> EventFlow Pod :8000
```

Readiness and liveness probes monitor the `/health` endpoint.

---

## What We Have Tested

### Containerization

EventFlow was packaged as a Docker image using `python:3.12-slim`.

The application was tested locally before deployment to Kubernetes.

### Kubernetes Desired State

Deleting an EventFlow pod demonstrated reconciliation:

```text
Desired replicas: 3

Pod deleted
     |
     v
ReplicaSet detects 2/3
     |
     v
New Pod created
     |
     v
Actual state returns to 3/3
```

### Image Troubleshooting

An incorrect image reference produced:

```text
ErrImagePull
ImagePullBackOff
```

This demonstrated the relationship between Kubernetes manifests, local images, image tags, and the kubelet's image resolution behavior.

### Application Failure

A Python syntax error caused:

```text
CrashLoopBackOff
```

Container logs were used to identify and correct the application failure.

### Readiness and Liveness

Readiness and liveness probes were added to EventFlow.

A deliberately incorrect readiness path demonstrated that a container can be:

```text
Running
```

while not being:

```text
Ready
```

and therefore not eligible to receive Service traffic.

### Kubernetes DNS

A troubleshooting pod successfully resolved:

```text
eventflow.default.svc.cluster.local
```

to the EventFlow Service ClusterIP.

This demonstrated Kubernetes service discovery through CoreDNS.

### Service and Endpoint Troubleshooting

The EventFlow Service uses:

```yaml
selector:
  app: eventflow
```

Changing the selector to:

```yaml
selector:
  app: eventflow-broken
```

caused the Service to have no matching EventFlow endpoints.

DNS continued to resolve successfully, demonstrating the distinction between:

```text
DNS discovery
      |
Service
      |
Pod selection
      |
EndpointSlice
```

This produced a useful troubleshooting sequence:

```text
Can DNS resolve?
       |
       v
Does the Service exist?
       |
       v
Are EndpointSlices populated?
       |
       v
Do labels and selectors match?
       |
       v
Can the Pod/application respond?
```

### Direct Pod Networking

A temporary troubleshooting pod was used to communicate directly with EventFlow Pod IP addresses:

```text
curl-test
     |
     +----> 10.244.0.2:8000
     +----> 10.244.0.5:8000
     +----> 10.244.0.6:8000
```

This bypassed CoreDNS and the Kubernetes Service and demonstrated direct pod-network communication.

### NetworkPolicy Experiment

A default-deny ingress NetworkPolicy was created for:

```text
app=eventflow
```

Kubernetes successfully accepted the policy:

```text
Selected pods are isolated for ingress connectivity
```

However, traffic continued to reach EventFlow.

Investigation showed that the current kind cluster uses:

```text
kindnet
```

as its networking implementation.

The experiment demonstrates an important platform engineering principle:

> **Defining a policy is not the same as enforcing a policy.**

The networking implementation must provide the enforcement capability required by the policy.

---

## Zero Trust Direction

The DoD Enterprise DevSecOps Fundamentals v2.5 identifies Zero Trust as the target security model for DevSecOps software factories and platforms.

As the lab evolves, Zero Trust concepts will be introduced progressively, including:

- deny-by-default communication
- workload and non-person entity identity
- east-west traffic controls
- Kubernetes RBAC
- NetworkPolicy
- workload authentication
- mutual TLS (mTLS)
- secrets management
- continuous monitoring
- policy enforcement and telemetry

The goal is to understand not only how to configure these controls, but also where and how they are actually enforced.

---

## Roadmap

```text
[✓] Containerized application
[✓] Kubernetes Deployment
[✓] Services and EndpointSlices
[✓] Readiness and liveness probes
[✓] Kubernetes DNS
[✓] Pod-to-Pod networking
[✓] Failure and troubleshooting exercises

[ ] Policy-capable Kubernetes networking (Cilium)
[ ] Default-deny NetworkPolicy
[ ] Explicit east-west allow policies
[ ] Persistent storage / PostgreSQL
[ ] Helm
[ ] CI/CD software factory
[ ] Artifact repository
[ ] Software supply-chain security
[ ] GitOps / Argo CD
[ ] Kubernetes RBAC
[ ] Secrets and workload identity
[ ] Observability
[ ] Continuous security monitoring
[ ] Service mesh / mTLS
[ ] OpenShift
[ ] Infrastructure as Code
[ ] High availability and SRE exercises
[ ] NIST SP 800-53 control mapping
[ ] Zero Trust policy architecture
[ ] RMF / cATO-style evidence exercises
[ ] DevSecOps software-factory capstone
```

---

## Next Mission: Cilium

The current kind cluster uses kindnet.

The next networking exercise will replace the default networking configuration with **Cilium** so that NetworkPolicy can be enforced.

The same experiment will then be repeated:

```text
Normal traffic
      |
      v
Default-deny policy
      |
      v
Traffic blocked
      |
      v
Explicit allow policy
      |
      v
Traffic restored
```

This will provide the lab's first practical implementation of deny-by-default east-west workload communication.

---

## Repository Structure

```text
platform-engineering-lab/
├── app/
│   └── main.py
├── docs/
│   ├── architecture/
│   ├── compliance/
│   └── security/
├── kubernetes/
│   └── base/
│       ├── deployment.yaml
│       ├── service.yaml
│       └── networkpolicy.yaml
├── tests/
├── Dockerfile
├── requirements.txt
└── README.md
```

The repository will expand as additional platform capabilities are introduced.

---

## References

- DoD Enterprise DevSecOps Fundamentals v2.5, October 2024
- DoD Enterprise DevSecOps Reference Design: CNCF Kubernetes v2.1, September 2021
- DoD Zero Trust Strategy
- NIST SP 800-53, Security and Privacy Controls for Information Systems and Organizations
- NIST SP 800-190, Application Container Security Guide
- NIST SP 800-218, Secure Software Development Framework
- Kubernetes Documentation
- kind Documentation
- Cilium Documentation
