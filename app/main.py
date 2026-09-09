import socket

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

events = []


class Event(BaseModel):
    source: str
    event_type: str
    value: float


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "message": "EventFlow is running.",
        "hostname": socket.gethostname()
    }


@app.post("/events", status_code=201)
def create_event(event: Event):
    events.append(event.model_dump())
    return {
        "status": "created",
        "event": event
    }


@app.get("/events")
def get_events():
    return events