#!/usr/bin/env python3
"""Validate scraping, Grafana provisioning, and real dashboard query results.

Uses credentials from the environment or resolved local Compose configuration.
Generates read requests, intentional 404s, and invalid events; no database writes.
"""

import base64
import json
import math
import os
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent
PROMETHEUS = os.getenv("PROMETHEUS_URL", "http://127.0.0.1:9090").rstrip("/")
GRAFANA = os.getenv("GRAFANA_URL", "http://127.0.0.1:3000").rstrip("/")


def request(url, payload=None, auth=None, expected=200):
    headers = {}
    if auth:
        headers["Authorization"] = auth
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = Request(url, data=None if payload is None else json.dumps(payload).encode(), headers=headers)
    try:
        with urlopen(req, timeout=15) as response:
            status, body = response.status, response.read()
    except HTTPError as exc:
        status, body = exc.code, exc.read()
    if status != expected:
        raise RuntimeError(f"{req.get_method()} {url}: expected {expected}, got {status}")
    return json.loads(body) if body else None


def grafana_auth():
    user = os.getenv("GF_SECURITY_ADMIN_USER")
    password = os.getenv("GF_SECURITY_ADMIN_PASSWORD")
    if not user or not password:
        result = subprocess.run(
            ["docker", "compose", "config", "--format", "json"],
            cwd=ROOT, capture_output=True, text=True,
        )
        if result.returncode:
            raise RuntimeError("Cannot resolve Grafana credentials; check Compose configuration or export GF_SECURITY_ADMIN_USER and GF_SECURITY_ADMIN_PASSWORD")
        settings = json.loads(result.stdout)["services"]["grafana"]["environment"]
        user = user or settings["GF_SECURITY_ADMIN_USER"]
        password = password or settings["GF_SECURITY_ADMIN_PASSWORD"]
    encoded = base64.b64encode(f"{user}:{password}".encode()).decode()
    return "Basic " + encoded


def targets_ready():
    data = request(PROMETHEUS + "/api/v1/targets?state=active")["data"]["activeTargets"]
    targets = {item["labels"]["job"]: item for item in data}
    for job in ("eventflow", "log-ingestor"):
        target = targets.get(job, {})
        if target.get("health") != "up":
            return False
        if target["scrapeUrl"] != f"http://{job}:8000/metrics" or target["scrapeInterval"] != "15s":
            raise RuntimeError(f"Unexpected scrape configuration for {job}")
    return True


def main():
    deadline = time.monotonic() + 90
    while True:
        try:
            if targets_ready():
                break
        except URLError:
            pass
        if time.monotonic() > deadline:
            raise RuntimeError("Application scrape targets did not become healthy within 90 seconds")
        time.sleep(2)
    print("Both applications scrape successfully by service name every 15 seconds.", flush=True)

    eventflow = os.getenv("EVENTFLOW_URL", "http://127.0.0.1:8001").rstrip("/")
    ingestor = os.getenv("LOG_INGESTOR_URL", "http://127.0.0.1:8002").rstrip("/")
    request(eventflow + "/health")
    request(ingestor + "/health")
    request(ingestor + "/ready")
    print("Generating 45 seconds of sample traffic, including intentional 404 and 422 responses; no database writes.", flush=True)
    for _ in range(30):
        request(eventflow + "/events")
        request(eventflow + "/observability-sample-not-found", expected=404)
        request(ingestor + "/events", payload={}, expected=422)
        time.sleep(1.5)

    auth = grafana_auth()
    if request(GRAFANA + "/api/health")["database"] != "ok":
        raise RuntimeError("Grafana database is unhealthy")
    datasource = request(GRAFANA + "/api/datasources/uid/prometheus", auth=auth)
    if datasource["url"] != "http://prometheus:9090" or not datasource["isDefault"]:
        raise RuntimeError("Prometheus data source was not provisioned as expected")
    if request(GRAFANA + "/api/datasources/uid/prometheus/health", auth=auth)["status"] != "OK":
        raise RuntimeError("Grafana cannot connect to Prometheus")
    dashboard = request(GRAFANA + "/api/dashboards/uid/platform-lab-observability", auth=auth)["dashboard"]
    if dashboard["title"] != "Platform Engineering Lab — Observability":
        raise RuntimeError("Unexpected dashboard title")

    for panel in dashboard["panels"]:
        for target in panel["targets"]:
            query = "?" + urlencode({"query": target["expr"]})
            for base, headers in (
                (PROMETHEUS + "/api/v1/query", None),
                (GRAFANA + "/api/datasources/proxy/uid/prometheus/api/v1/query", auth),
            ):
                response = request(base + query, auth=headers)
                if response["status"] != "success" or not response["data"]["result"]:
                    raise RuntimeError(f"No measurements for panel {panel['title']}")
                results = response["data"]["result"]
                values = [float(item["value"][1]) for item in results]
                if not all(math.isfinite(value) for value in values):
                    raise RuntimeError(f"Non-finite measurements for panel {panel['title']}")
                if panel["id"] in (1, 2, 3, 4, 5) and not all(value > 0 for value in values):
                    raise RuntimeError(f"Sample traffic not reflected in panel {panel['title']}")
                if panel["id"] == 6 and (len(results) != 2 or values != [1.0, 1.0]):
                    raise RuntimeError("Both application scrape status series must be UP")
            print(f"{panel['title']}: {values} (Prometheus and Grafana data source verified)", flush=True)
    print("Passed: dashboard is provisioned and all panel queries return real measurements through Grafana.")


if __name__ == "__main__":
    main()
