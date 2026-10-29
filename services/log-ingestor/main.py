from datetime import datetime
from ipaddress import IPv4Address, IPv6Address
import json
import os

import psycopg
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, field_validator

from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI(
    title="Security Log Ingestor",
    version="0.1.0",
)

Instrumentator().instrument(app).expose(app)


class SecurityEvent(BaseModel):
    timestamp: datetime
    service: str
    event_type: str
    severity: str
    source_ip: IPv4Address | IPv6Address | None = None
    username: str | None = None
    message: str

    @field_validator("severity")
    @classmethod
    def normalize_severity(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        normalized = value.upper()

        if normalized not in allowed:
            raise ValueError(
                f"severity must be one of: {', '.join(sorted(allowed))}"
            )

        return normalized


def get_connection():
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "host.docker.internal"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "postgres"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
        connect_timeout=5,
    )


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/ready")
def readiness():
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, event_time, service, event_type, severity, "
                    "source_ip, username, message, raw_event "
                    "FROM public.security_events LIMIT 0;"
                )

        return {
            "status": "ready",
            "database": "connected",
        }

    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database unavailable: {exc}",
        )


@app.post("/events")
def ingest_event(event: SecurityEvent):
    raw_event = event.model_dump(mode="json")

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.security_events (
                        event_time,
                        service,
                        event_type,
                        severity,
                        source_ip,
                        username,
                        message,
                        raw_event
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id;
                    """,
                    (
                        event.timestamp,
                        event.service,
                        event.event_type,
                        event.severity,
                        str(event.source_ip) if event.source_ip else None,
                        event.username,
                        event.message,
                        json.dumps(raw_event),
                    ),
                )

                event_id = cur.fetchone()[0]

        return {
            "status": "stored",
            "event_id": event_id,
            "service": event.service,
            "event_type": event.event_type,
            "severity": event.severity,
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Database error: {exc}",
        )
