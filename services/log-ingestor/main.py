from datetime import datetime, timezone
from ipaddress import IPv4Address, IPv6Address
import json
import os

import psycopg
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI(
    title="Security Log Ingestor",
    version="0.1.0",
)

Instrumentator().instrument(app).expose(app)


class SecurityEvent(BaseModel):
    source_event_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    timestamp: datetime
    service: str
    event_type: str
    severity: str
    source_ip: IPv4Address | IPv6Address | None = None
    username: str | None = None
    message: str

    @field_validator("timestamp")
    @classmethod
    def canonical_timestamp(cls, value: datetime) -> datetime:
        # Legacy naive timestamps remain accepted and are interpreted as UTC.
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

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
    conn = psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "host.docker.internal"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "postgres"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
        connect_timeout=5,
    )
    # The conflict lookup uses a fresh statement snapshot after a concurrent insert.
    conn.isolation_level = psycopg.IsolationLevel.READ_COMMITTED
    return conn


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
                    "source_ip, username, message, raw_event, source_event_id "
                    "FROM public.security_events LIMIT 0;"
                )
                cur.execute(
                    "SELECT 1 FROM pg_constraint WHERE conrelid='public.security_events'::regclass "
                    "AND conname='security_events_source_event_id_key' AND contype='u' AND convalidated "
                    "AND NOT condeferrable AND conkey=ARRAY[(SELECT attnum FROM pg_attribute "
                    "WHERE attrelid='public.security_events'::regclass AND attname='source_event_id')]"
                )
                if cur.fetchone() is None:
                    raise RuntimeError("Source-event uniqueness constraint is missing")

        return {
            "status": "ready",
            "database": "connected",
            "capabilities": {"source_event_id": "v1"},
        }

    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database unavailable: {exc}",
        )


@app.post("/events")
def ingest_event(event: SecurityEvent):
    raw_event = event.model_dump(mode="json")
    if event.source_event_id is None:
        raw_event.pop("source_event_id")

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
                        raw_event,
                        source_event_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (source_event_id) DO NOTHING
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
                        event.source_event_id,
                    ),
                )

                inserted = cur.fetchone()
                if inserted:
                    event_id = inserted[0]
                else:
                    # Separate statement: sees the winner even if the conflicting
                    # insert was invisible to the INSERT's initial MVCC snapshot.
                    cur.execute(
                        "SELECT id, raw_event FROM public.security_events "
                        "WHERE source_event_id=%s FOR SHARE", (event.source_event_id,)
                    )
                    existing = cur.fetchone()
                    if existing is None:
                        raise RuntimeError("Conflicting event unavailable; retry with the same identifier")
                    if existing[1] != raw_event:
                        raise HTTPException(status_code=409, detail="source_event_id already has different content")
                    event_id = existing[0]

        response = {
            "status": "stored",
            "event_id": event_id,
            "service": event.service,
            "event_type": event.event_type,
            "severity": event.severity,
        }
        if event.source_event_id is not None:
            response["source_event_id"] = event.source_event_id
        return response

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Database error: {exc}",
        )
