# -*- coding: utf-8 -*-
"""FastAPI webhook receiver + SQLite event log + Mini App для YouGile."""
import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import requests as http_requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

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


# --- Dashboard API (для Mini App) ---
YOUGILE_BASE_URL = "https://yougile.com/api-v2"
YOUGILE_API_KEY = os.environ.get("YOUGILE_API_KEY", "")

STICKER_PRIORITY_ID = "b0435d49-0237-47f7-88d6-c10de7adbc9d"
PRIORITY_MAP = {"8ced62e1d595": "High", "55e6b0a1cb68": "Medium", "414cda413f0a": "Low"}
ACTIVE_COLUMNS = ["Надо сделать", "В работе", "На согласовании"]


def _yg_headers():
    return {"Authorization": f"Bearer {YOUGILE_API_KEY}", "Content-Type": "application/json"}


def _yg_get(path: str, params: dict = None) -> dict:
    r = http_requests.get(f"{YOUGILE_BASE_URL}{path}", headers=_yg_headers(), params=params or {}, timeout=10)
    return r.json() if r.status_code == 200 else {}


def fetch_dashboard_data() -> dict:
    """Собирает данные для дашборда Mini App."""
    # Проекты → доски → колонки → задачи
    projects = _yg_get("/projects", {"limit": 50}).get("content", [])
    target_project = None
    for p in projects:
        if p.get("title") == "Продуктивность":
            target_project = p
            break
    if not target_project:
        return {"error": "Проект не найден"}

    boards = _yg_get("/boards", {"projectId": target_project["id"], "limit": 50}).get("content", [])
    target_board = None
    for b in boards:
        if b.get("title") == "Задачи лог":
            target_board = b
            break
    if not target_board:
        return {"error": "Доска не найдена"}

    columns_data = _yg_get("/columns", {"boardId": target_board["id"], "limit": 50}).get("content", [])

    columns = []
    tasks = []
    total_active = 0
    priority_counts = {"High": 0, "Medium": 0, "Low": 0, "none": 0}
    overdue = 0
    due_soon = 0
    assignee_counts = {}

    # Пользователи
    users_raw = _yg_get("/users", {"limit": 100}).get("content", [])
    users_map = {}
    for u in users_raw:
        name = (u.get("realName") or u.get("name") or "").strip()
        if name:
            users_map[u["id"]] = name

    for col in columns_data:
        col_title = col.get("title", "")
        is_active = col_title in ACTIVE_COLUMNS
        col_tasks = _yg_get("/task-list", {"columnId": col["id"], "limit": 200}).get("content", [])
        active_tasks = [t for t in col_tasks if not t.get("completed") and not t.get("archived")]
        completed_tasks = [t for t in col_tasks if t.get("completed")]

        columns.append({
            "title": col_title,
            "active": len(active_tasks),
            "completed": len(completed_tasks),
            "is_active": is_active,
        })

        if not is_active:
            continue

        total_active += len(active_tasks)
        for t in active_tasks:
            stickers = t.get("stickers") or {}
            prio_state = stickers.get(STICKER_PRIORITY_ID, "")
            priority = PRIORITY_MAP.get(prio_state, "")
            priority_counts[priority or "none"] += 1

            days_left = None
            dl_raw = t.get("deadline")
            if isinstance(dl_raw, dict) and dl_raw.get("deadline"):
                ts = dl_raw["deadline"] // 1000
                days_left = (datetime.fromtimestamp(ts).date() - datetime.now().date()).days
                if days_left < 0:
                    overdue += 1
                elif days_left <= 3:
                    due_soon += 1

            for uid in (t.get("assigned") or []):
                name = users_map.get(uid, uid[:8])
                assignee_counts[name] = assignee_counts.get(name, 0) + 1

            tasks.append({
                "id": t.get("id", ""),
                "title": t.get("title", "")[:80],
                "column": col_title,
                "priority": priority or "none",
                "days_to_deadline": days_left,
                "assignee": ", ".join(users_map.get(uid, "?") for uid in (t.get("assigned") or [])) or None,
                "key": t.get("idTaskProject") or t.get("idTaskCommon") or "",
            })

    # Сортировка: просроченные сверху, потом по дедлайну
    tasks.sort(key=lambda t: (
        0 if t["days_to_deadline"] is not None and t["days_to_deadline"] < 0 else 1,
        t["days_to_deadline"] if t["days_to_deadline"] is not None else 9999,
    ))

    # Событийная статистика за 7 дней
    event_summary = get_activity_summary(7)

    return {
        "project": target_project.get("title", ""),
        "board": target_board.get("title", ""),
        "total_active": total_active,
        "overdue": overdue,
        "due_soon": due_soon,
        "priority_counts": priority_counts,
        "columns": columns,
        "tasks": tasks[:50],
        "assignees": [{"name": k, "count": v} for k, v in sorted(assignee_counts.items(), key=lambda x: -x[1])],
        "events_7d": event_summary,
        "updated_at": datetime.now().isoformat(),
    }


@app.get("/api/dashboard")
async def dashboard_api(request: Request):
    """JSON-данные для Mini App дашборда."""
    try:
        data = fetch_dashboard_data()
        return JSONResponse(data)
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# --- Статика Mini App ---
WEBAPP_DIR = Path(__file__).parent / "webapp"
if WEBAPP_DIR.exists():
    app.mount("/app", StaticFiles(directory=str(WEBAPP_DIR), html=True), name="webapp")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
