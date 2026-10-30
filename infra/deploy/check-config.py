#!/usr/bin/env python3
"""Read-only validation of the AWS Compose merge; never starts containers."""

import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent.parent.parent
result = subprocess.run(
    ["docker", "compose", "--env-file", ".env.example", "-f", "compose.yaml", "-f", "infra/deploy/compose.aws.yaml", "config", "--format", "json"],
    cwd=root, check=True, capture_output=True, text=True,
)
config = json.loads(result.stdout)
services = config["services"]
assert set(services) == {"postgres", "eventflow", "log-ingestor", "db-init", "prometheus", "grafana"}
assert not services["postgres"].get("ports"), "PostgreSQL must not have a host port"
assert not config["networks"]["database"].get("external", False)
assert "postgres_data" in config["volumes"]
for service, port in [("eventflow", "8001"), ("log-ingestor", "8002"), ("prometheus", "9090"), ("grafana", "3000")]:
    published = services[service]["ports"]
    assert len(published) == 1 and published[0]["host_ip"] == "127.0.0.1" and published[0]["published"] == port
    assert services[service]["restart"] == "unless-stopped"
assert services["db-init"]["depends_on"]["postgres"]["condition"] == "service_healthy"
assert services["db-init"]["command"] == [
    "psql", "-X", "-v", "ON_ERROR_STOP=1",
    "-f", "/init/001_security_events.sql", "-f", "/init/002_source_event_id.sql",
], "Initialization must apply the baseline before the additive idempotency migration"
mounts = {volume["target"]: volume for volume in services["db-init"]["volumes"]}
for sql in ("001_security_events.sql", "002_source_event_id.sql"):
    assert mounts[f"/init/{sql}"]["read_only"], "SQL provisioning must be read-only"
print("AWS Compose merge passed: private PostgreSQL, loopback application ports, health ordering, persistent volumes.")
