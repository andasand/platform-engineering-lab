#!/usr/bin/env python3
"""Exercise running services; retains one labeled security event in PostgreSQL."""

import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


def request(base, path, expected=200, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = Request(base + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=15) as response:
            status, body = response.status, response.read().decode()
    except HTTPError as exc:
        status, body = exc.code, exc.read().decode()
    if status != expected:
        raise RuntimeError(f"{req.get_method()} {req.full_url}: expected {expected}, got {status}: {body}")
    print(f"{req.get_method()} {req.full_url}: {status}")
    return body


def main():
    eventflow = os.getenv("EVENTFLOW_URL", "http://127.0.0.1:8001").rstrip("/")
    ingestor = os.getenv("LOG_INGESTOR_URL", "http://127.0.0.1:8002").rstrip("/")
    marker = "smoke-test-" + uuid4().hex

    if json.loads(request(eventflow, "/health"))["status"] != "healthy":
        raise RuntimeError("EventFlow is unhealthy")
    event = {"source": marker, "event_type": "integration_check", "value": 1.0}
    created = json.loads(request(eventflow, "/events", expected=201, payload=event))
    if created["event"] != event or event not in json.loads(request(eventflow, "/events")):
        raise RuntimeError("EventFlow event round trip failed")
    if "http_requests_total" not in request(eventflow, "/metrics"):
        raise RuntimeError("EventFlow HTTP metrics missing")

    if json.loads(request(ingestor, "/health"))["status"] != "healthy":
        raise RuntimeError("Ingestor is unhealthy")
    if json.loads(request(ingestor, "/ready"))["database"] != "connected":
        raise RuntimeError("Ingestor database is not ready")
    security_event = {
        "timestamp": "2026-10-29T12:00:00Z",
        "service": marker,
        "event_type": "integration_check",
        "severity": "info",
        "source_ip": "2001:db8::1",
        "username": "local-smoke-test",
        "message": "Security Log Ingestor integration smoke test",
    }
    stored = json.loads(request(ingestor, "/events", payload=security_event))
    if stored["status"] != "stored" or stored["severity"] != "INFO" or not isinstance(stored["event_id"], int):
        raise RuntimeError("Unexpected stored event response")
    security_event["severity"] = "invalid"
    request(ingestor, "/events", expected=422, payload=security_event)
    security_event["severity"] = "INFO"
    security_event["source_ip"] = "not-an-ip"
    request(ingestor, "/events", expected=422, payload=security_event)
    metrics = request(ingestor, "/metrics")
    if 'http_requests_total{handler="/events",method="POST",status="2xx"}' not in metrics:
        raise RuntimeError("Ingestor successful POST counter missing")
    if 'http_requests_total{handler="/events",method="POST",status="4xx"}' not in metrics:
        raise RuntimeError("Ingestor validation error counter missing")
    print(f"Passed. Retained database event_id={stored['event_id']}, service={marker}")


if __name__ == "__main__":
    main()
