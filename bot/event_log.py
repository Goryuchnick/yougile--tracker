# -*- coding: utf-8 -*-
"""FastAPI webhook receiver + SQLite event log для YouGile."""
import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

load_dotenv()

DB_PATH = os.environ.get("EVENT_LOG_DB", "/app/data/events.db")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="YouGile Event Log", docs_url=None, redoc_url=None)


# --- SQLite ---
def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                object_id TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                user_id TEXT,
                data TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_object ON events(object_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp)")


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def log_event(event_type: str, object_id: str, timestamp_ms: int,
              user_id: str = None, data: dict = None):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO events (event_type, object_id, timestamp, user_id, data) VALUES (?, ?, ?, ?, ?)",
            (event_type, object_id, timestamp_ms, user_id, json.dumps(data or {}, ensure_ascii=False)),
        )


def query_events(event_types: list[str] = None, since_ms: int = None,
                 until_ms: int = None, object_id: str = None,
                 limit: int = 100) -> list[dict]:
    """Query events with filters."""
    conditions = []
    params = []
    if event_types:
        placeholders = ",".join("?" * len(event_types))
        conditions.append(f"event_type IN ({placeholders})")
        params.extend(event_types)
    if since_ms:
        conditions.append("timestamp >= ?")
        params.append(since_ms)
    if until_ms:
        conditions.append("timestamp <= ?")
        params.append(until_ms)
    if object_id:
        conditions.append("object_id = ?")
        params.append(object_id)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM events {where} ORDER BY timestamp DESC LIMIT ?", params
        ).fetchall()
    return [dict(r) for r in rows]


def get_activity_summary(days: int = 7) -> dict:
    """Summary of activity for the period."""
    since_ms = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT event_type, COUNT(*) as cnt FROM events WHERE timestamp >= ? GROUP BY event_type ORDER BY cnt DESC",
            (since_ms,),
        ).fetchall()
    return {r["event_type"]: r["cnt"] for r in rows}


def get_task_history(task_id: str) -> list[dict]:
    """Full history of a single task."""
    return query_events(object_id=task_id, limit=200)


# --- FastAPI endpoints ---
@app.on_event("startup")
def startup():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    init_db()
    logger.info(f"Event log DB: {DB_PATH}")


@app.post("/webhook")
async def receive_webhook(request: Request):
    """Receive YouGile webhook events."""
    body = await request.json()
    event_type = body.get("event", "unknown")
    payload = body.get("payload", {})
    object_id = payload.get("id", "")
    timestamp_ms = payload.get("timestamp") or int(time.time() * 1000)
    user_id = payload.get("by") or payload.get("createdBy") or payload.get("userId")

    log_event(event_type, object_id, timestamp_ms, user_id, payload)
    logger.info(f"Event: {event_type} | {object_id}")
    return JSONResponse({"status": "ok"})


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/events")
async def list_events(
    event_type: str = None, days: int = 7,
    object_id: str = None, limit: int = 50
):
    """Query events. Use event_type=task-moved, task-updated, etc."""
    since_ms = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
    types = [event_type] if event_type else None
    events = query_events(event_types=types, since_ms=since_ms, object_id=object_id, limit=limit)
    return {"count": len(events), "events": events}


@app.get("/summary")
async def summary(days: int = 7):
    return get_activity_summary(days)


@app.get("/task/{task_id}/history")
async def task_history(task_id: str):
    events = get_task_history(task_id)
    return {"task_id": task_id, "count": len(events), "events": events}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
