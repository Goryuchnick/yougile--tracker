# -*- coding: utf-8 -*-
import html
import logging
import os
import requests
import json
import asyncio
import time
import re
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters,
)
from openai import OpenAI
import ai_prioritizer

load_dotenv()

# --- Конфигурация ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
YOUGILE_BASE_URL   = "https://yougile.com/api-v2"
YOUGILE_API_KEY    = os.environ.get("YOUGILE_API_KEY")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

# Модели OpenRouter — протестированы 2026-03-05
# Чат: бесплатные (простые ответы, не критично)
MODELS_CHAT = [
    "arcee-ai/trinity-large-preview:free",     # 1.1s
    "google/gemma-3-4b-it:free",               # 1.5s
    "google/gemma-3n-e4b-it:free",             # 1.3s
    "liquid/lfm-2.5-1.2b-instruct:free",      # 1.1s
    "mistralai/mistral-nemo",                  # 1.4s, $0.02/M — запас
]
# Задачи: умные модели (JSON, извлечение, приоритизация)
MODELS_TASK = [
    "qwen/qwen-turbo",                        # 0.8s, $0.03/M — быстрый, хороший JSON
    "mistralai/mistral-nemo",                  # 1.4s, $0.02/M
    "microsoft/phi-4",                         # 0.9s, $0.06/M
]
# Анализ: саммари/рекомендации (не нужен JSON, можно бесплатные)
MODELS_ANALYSIS = [
    "arcee-ai/trinity-large-preview:free",     # 1.1s — бесплатная, хороший текст
    "google/gemma-3-4b-it:free",               # 1.5s
    "mistralai/mistral-nemo",                  # 1.4s, $0.02/M — запас
]
# Аудио: транскрипция голоса/встреч
MODELS_AUDIO = [
    "google/gemini-2.0-flash-lite-001",        # 1.1s, $0.075/M
]

# Стикеры приоритета
STICKER_PRIORITY_ID = "b0435d49-0237-47f7-88d6-c10de7adbc9d"
PRIORITY_STATES = {
    "High":   "8ced62e1d595",
    "Medium": "55e6b0a1cb68",
    "Low":    "414cda413f0a",
}
PRIORITY_MAP_INV = {v: k for k, v in PRIORITY_STATES.items()}
PRIORITY_EMOJI   = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}

# Стикеры направления
STICKER_DIRECTION_ID = "54176f3d-77ff-4eb9-a70c-70caa96910e3"
DIRECTION_STATES = {
    "Альпина":   "8d4f534aec91",
    "Welcome":   "2a1cba107dfd",
    "Личное":    "413cd49fb4c4",
    "Агентство": "00db86f5a160",
}

# Проект и доска
TARGET_PROJECT = "Продуктивность"
TARGET_BOARD   = "Задачи лог"

# Колонки, которые считаются «активными» (задачи требуют действий)
ACTIVE_COLUMNS = ["Надо сделать", "В работе", "На согласовании"]
# Колонки с завершёнными задачами
DONE_COLUMNS = ["Готово"]

# Кэш
_project_id: str | None = None
_board_id:   str | None = None
_users_cache: dict[str, str] | None = None

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- Состояние ---
pending_tasks: dict[int, list[dict]] = {}
chat_history:  dict[int, list[dict]] = {}
task_draft:    dict[int, dict] = {}   # user_id -> {title, description, step, board_id, ...}
pending_single_task: dict[int, dict] = {}  # user_id -> разобранная одна задача

# Mini App URL
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://yougile-webhook.147.45.184.108.sslip.io/app")

# --- Меню ---
BTN_ACTIVE    = "📋 Активные задачи"
BTN_ADD_TASK  = "➕ Новая задача"
BTN_REPORT    = "📊 Отчёт"
BTN_PRIORITIZE = "🎯 Приоритизация"
BTN_DASHBOARD = "📱 Дашборд"

MAIN_MENU = ReplyKeyboardMarkup(
    [[KeyboardButton(BTN_ACTIVE), KeyboardButton(BTN_ADD_TASK)],
     [KeyboardButton(BTN_REPORT), KeyboardButton(BTN_PRIORITIZE)],
     [KeyboardButton(BTN_DASHBOARD, web_app=WebAppInfo(url=WEBAPP_URL))]],
    resize_keyboard=True,
    input_field_placeholder="Напиши задачу или выбери действие...",
)

MENU_BUTTONS = {BTN_ACTIVE, BTN_ADD_TASK, BTN_REPORT, BTN_PRIORITIZE, BTN_DASHBOARD}

# --- Системный промпт ---
CHAT_SYSTEM_PROMPT = (
    "Ты — Вася, пацанский AI-помощник по задачам. "
    "Общаешься по-свойски, дружелюбно, с лёгким юморком. Как кореш, который шарит в тайм-менеджменте. "
    "Говоришь просто: «го», «чё», «братан», «красава», «залетай», «не парься». "
    "Но не перебарщивай — ты помощник, а не клоун. Будь по делу, 1-3 предложения. "
    "Если человек описывает задачу — скажи типа «О, похоже на задачу! Жмакай ➕ и кидай мне». "
    "Если спрашивает статус — «Тыкай 📋, там всё видно». "
    "Отвечай на русском. Без markdown."
)


# --- Утилиты ---
def esc(text) -> str:
    return html.escape(str(text))


def strip_html(text: str) -> str:
    """Убирает HTML-теги из описания задачи."""
    return re.sub(r'<[^>]+>', '', text).strip()


# --- OpenRouter AI ---
def _get_client() -> OpenAI:
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY не задан.")
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)


def _ai_call(models: list, messages: list, max_tokens: int = 4096) -> str:
    """Вызов OpenRouter с ротацией моделей."""
    client = _get_client()
    last_error = None
    for model in models:
        try:
            resp = client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens,
            )
            content = resp.choices[0].message.content
            if not content:
                logger.warning(f"Пустой ответ от {model}, пробую следующую")
                continue
            return content.strip()
        except Exception as e:
            last_error = e
            err = str(e)
            if "429" in err or "rate" in err.lower():
                logger.warning(f"429 на {model}, следующая модель")
                time.sleep(2)
            elif "402" in err:
                logger.warning(f"402 на {model}, следующая модель")
            elif "404" in err:
                logger.warning(f"404 на {model}, модель недоступна")
            else:
                logger.error(f"Ошибка {model}: {e}")
            continue
    raise Exception(f"Все модели недоступны. Последняя ошибка: {last_error}")


def ai_generate(prompt: str) -> str:
    return _ai_call(MODELS_TASK, [{"role": "user", "content": prompt}])


def ai_chat(user_id: int, user_text: str) -> str:
    history = chat_history.get(user_id, [])
    messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})
    reply = _ai_call(MODELS_CHAT, messages, max_tokens=500)
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": reply})
    chat_history[user_id] = history[-20:]
    return reply


def ai_audio(file_path: str, prompt: str) -> str:
    import base64
    with open(file_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode()
    ext = os.path.splitext(file_path)[1].lower().lstrip(".")
    mime_map = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "wav": "audio/wav",
                "ogg": "audio/ogg", "oga": "audio/ogg", "flac": "audio/flac"}
    mime = mime_map.get(ext, "audio/mpeg")
    data_url = f"data:{mime};base64,{audio_b64}"
    messages = [{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": data_url}},
            {"type": "text", "text": prompt},
        ],
    }]
    return _ai_call(MODELS_AUDIO, messages)



def _clean_json(raw: str) -> str:
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


# --- YouGile API ---
def _headers():
    return {"Authorization": f"Bearer {YOUGILE_API_KEY}", "Content-Type": "application/json"}


def _find_project_board() -> tuple[str | None, str | None]:
    global _project_id, _board_id
    if _project_id and _board_id:
        return _project_id, _board_id
    h = _headers()
    r = requests.get(f"{YOUGILE_BASE_URL}/projects", headers=h, params={"limit": 50})
    if r.status_code != 200:
        return None, None
    for p in r.json().get("content", []):
        if p.get("title") == TARGET_PROJECT:
            _project_id = p["id"]
            break
    if not _project_id:
        return None, None
    r = requests.get(f"{YOUGILE_BASE_URL}/boards", headers=h, params={"projectId": _project_id, "limit": 50})
    if r.status_code != 200:
        return None, None
    for b in r.json().get("content", []):
        if b.get("title") == TARGET_BOARD:
            _board_id = b["id"]
            break
    return _project_id, _board_id


def get_projects() -> list[dict]:
    """Все проекты (не удалённые)."""
    r = requests.get(f"{YOUGILE_BASE_URL}/projects", headers=_headers(), params={"limit": 50})
    if r.status_code != 200:
        return []
    return [p for p in r.json().get("content", []) if not p.get("deleted")]


def get_boards(project_id: str) -> list[dict]:
    """Доски проекта (не удалённые)."""
    r = requests.get(f"{YOUGILE_BASE_URL}/boards", headers=_headers(), params={"projectId": project_id, "limit": 50})
    if r.status_code != 200:
        return []
    return [b for b in r.json().get("content", []) if not b.get("deleted")]


def get_columns_by_board(board_id: str) -> list[dict]:
    """Колонки доски."""
    r = requests.get(f"{YOUGILE_BASE_URL}/columns", headers=_headers(), params={"boardId": board_id, "limit": 50})
    return r.json().get("content", []) if r.status_code == 200 else []


def get_columns() -> list[dict]:
    _, board_id = _find_project_board()
    if not board_id:
        return []
    return get_columns_by_board(board_id)


def get_column_tasks(column_id: str, limit: int = 100) -> list[dict]:
    r = requests.get(f"{YOUGILE_BASE_URL}/task-list", headers=_headers(), params={"columnId": column_id, "limit": limit})
    return r.json().get("content", []) if r.status_code == 200 else []



def get_users() -> dict[str, str]:
    """Возвращает {имя_lower: id}. Кэширует."""
    global _users_cache
    if _users_cache is not None:
        return _users_cache
    r = requests.get(f"{YOUGILE_BASE_URL}/users", headers=_headers(), params={"limit": 100})
    if r.status_code != 200:
        return {}
    _users_cache = {}
    for u in r.json().get("content", []):
        name = (u.get("realName") or u.get("name") or "").strip()
        if name:
            _users_cache[name.lower()] = u["id"]
            _users_cache[u["id"]] = name  # обратный маппинг
    return _users_cache


def resolve_user_name(user_id: str) -> str:
    users = get_users()
    return users.get(user_id, "")


def find_column_id(target_columns=None) -> str | None:
    if target_columns is None:
        target_columns = ["Входящие", "Надо сделать", "Бэклог"]
    columns = get_columns()
    for col in columns:
        if col.get("title") in target_columns:
            return col["id"]
    return columns[0]["id"] if columns else None


def task_url(task_id: str) -> str:
    return f"https://yougile.com/task/{task_id}"


def create_yougile_task(task: dict, column_id: str) -> tuple[bool, dict | str]:
    users = get_users()
    body: dict = {
        "title": task["title"][:80],
        "columnId": column_id,
        "description": task.get("description", ""),
    }
    if task.get("deadline"):
        try:
            dl = datetime.strptime(task["deadline"], "%Y-%m-%d")
            body["deadline"] = {"deadline": int(dl.timestamp() * 1000), "withTime": False}
        except ValueError:
            pass
    stickers = {}
    priority = task.get("priority", "Medium")
    if priority in PRIORITY_STATES:
        stickers[STICKER_PRIORITY_ID] = PRIORITY_STATES[priority]
    direction = task.get("direction")
    if direction and direction in DIRECTION_STATES:
        stickers[STICKER_DIRECTION_ID] = DIRECTION_STATES[direction]
    if stickers:
        body["stickers"] = stickers
    items = task.get("checklist", [])
    if items:
        body["checklists"] = [{"title": "Чеклист",
                                "items": [{"title": t, "isCompleted": False} for t in items]}]
    assignee = task.get("assignee", "")
    if assignee and assignee.lower() not in ("не назначен", "unknown", ""):
        nl = assignee.lower()
        uid = users.get(nl)
        if not uid:
            for key, val in users.items():
                if isinstance(val, str) and nl in key:
                    uid = val
                    break
        if uid:
            body["assigned"] = [uid]
    resp = requests.post(f"{YOUGILE_BASE_URL}/tasks", headers=_headers(), json=body)
    if resp.status_code in (200, 201):
        return True, resp.json()
    return False, f"{resp.status_code}: {resp.text[:300]}"


# --- Функция 1: Активные задачи (требуют действий) ---
def get_active_tasks_full() -> tuple[str, list[dict]]:
    """Собирает задачи из активных колонок. Возвращает (HTML-текст, raw для AI)."""
    columns = get_columns()
    if not columns:
        return "Не удалось получить колонки.", []

    result_parts = []
    tasks_raw = []
    total = 0
    for col in columns:
        if col["title"] not in ACTIVE_COLUMNS:
            continue
        tasks = get_column_tasks(col["id"])
        active = [t for t in tasks if not t.get("completed") and not t.get("archived")]
        if not active:
            continue
        total += len(active)
        lines = [f"\n🗂 <b>{esc(col['title'])}</b> ({len(active)}):"]
        for t in active:
            stickers = t.get("stickers") or {}
            priority = PRIORITY_MAP_INV.get(stickers.get(STICKER_PRIORITY_ID, ""), "")
            p_emoji = PRIORITY_EMOJI.get(priority, "⚪")
            key = t.get("idTaskProject") or t.get("idTaskCommon") or ""
            key_str = f"<code>{esc(key)}</code> " if key else ""

            days_left = None
            dl_raw = t.get("deadline")
            dl_str = ""
            if isinstance(dl_raw, dict) and dl_raw.get("deadline"):
                ts = dl_raw["deadline"] // 1000
                dl_date = datetime.fromtimestamp(ts)
                days_left = (dl_date.date() - date.today()).days
                if days_left < 0:
                    dl_str = f" 🔥 просрочен {abs(days_left)}д"
                elif days_left == 0:
                    dl_str = " ⚡ сегодня"
                elif days_left <= 3:
                    dl_str = f" ⏰ {days_left}д"
                else:
                    dl_str = f" 📅 {dl_date.strftime('%d.%m')}"

            if len(lines) <= 10:
                lines.append(f"  {p_emoji} {key_str}<b>{esc(t['title'][:60])}</b>{dl_str}")
            tasks_raw.append({
                "title": t.get("title", "")[:60],
                "column": col["title"],
                "priority": priority or "нет",
                "days_to_deadline": days_left,
            })

        if len(active) > 10:
            lines.append(f"  <i>...и ещё {len(active) - 10}</i>")
        result_parts.append("\n".join(lines))

    if not result_parts:
        return "Нет активных задач. Всё чисто! 💪", []

    header = f"📋 <b>Активные задачи</b> — {total} шт.\n"
    return header + "\n".join(result_parts), tasks_raw


def get_active_tasks() -> str:
    """Обёртка для обратной совместимости."""
    text, _ = get_active_tasks_full()
    return text


def ai_active_analysis(tasks_raw: list[dict]) -> str:
    """AI-анализ активных задач: что критично, на что обратить внимание."""
    if not tasks_raw:
        return ""
    lines = []
    for t in tasks_raw[:30]:
        dl = f"дедлайн через {t['days_to_deadline']}д" if t["days_to_deadline"] is not None else "без дедлайна"
        if t["days_to_deadline"] is not None and t["days_to_deadline"] < 0:
            dl = f"просрочен {abs(t['days_to_deadline'])}д"
        lines.append(f"- {t['title']} | колонка: {t['column']} | {dl} | приоритет: {t['priority']}")
    tasks_text = "\n".join(lines)
    prompt = (
        f"Ты — помощник по задачам. Активные задачи ({len(tasks_raw)} шт.):\n\n"
        f"{tasks_text}\n\n"
        f"Проанализируй:\n"
        f"1. Критичные: просроченные или горящие задачи (дедлайн сегодня/завтра)\n"
        f"2. Внимание: задачи без движения, возможные блокеры\n"
        f"3. Рекомендация: на что обратить внимание сегодня (1-2 задачи)\n\n"
        f"До 400 символов. На русском. Без markdown."
    )
    try:
        return _ai_call(MODELS_ANALYSIS, [{"role": "user", "content": prompt}], max_tokens=500)
    except Exception as e:
        logger.warning(f"AI active analysis failed: {e}")
        return ""


# --- Функция 3: Отчёт за период ---
def get_completed_report(days: int = 7) -> str:
    """Собирает выполненные задачи за N дней с подзадачами и комментами."""
    columns = get_columns()
    if not columns:
        return "Не удалось получить колонки."

    cutoff_ts = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
    completed_tasks = []

    # Ищем завершённые задачи во всех колонках
    for col in columns:
        tasks = get_column_tasks(col["id"], limit=200)
        for t in tasks:
            if t.get("completed"):
                # completedTimestamp может быть в самой задаче или нет
                # Если есть — фильтруем по периоду
                ct = t.get("completedTimestamp") or t.get("timestamp", 0)
                if ct >= cutoff_ts:
                    t["_column"] = col["title"]
                    completed_tasks.append(t)

    if not completed_tasks:
        return f"За последние {days} дн. нет завершённых задач."

    # Сортируем: новые сверху
    completed_tasks.sort(key=lambda t: t.get("completedTimestamp") or t.get("timestamp", 0), reverse=True)

    lines = [f"📊 <b>Отчёт за {days} дн.</b> — {len(completed_tasks)} задач выполнено\n"]

    for t in completed_tasks[:20]:
        key = t.get("idTaskProject") or t.get("idTaskCommon") or ""
        key_str = f"<code>{esc(key)}</code> " if key else ""
        stickers = t.get("stickers") or {}
        priority = PRIORITY_MAP_INV.get(stickers.get(STICKER_PRIORITY_ID, ""), "")
        p_emoji = PRIORITY_EMOJI.get(priority, "⚪")

        # Дата завершения
        ct = t.get("completedTimestamp") or t.get("timestamp", 0)
        done_date = datetime.fromtimestamp(ct / 1000).strftime("%d.%m") if ct else ""
        date_str = f" ({done_date})" if done_date else ""

        # Описание из task-list (без доп. API-запросов)
        desc = (t.get("description") or "").replace("\n", " ").strip()[:80]
        desc_str = f"\n  <i>{esc(desc)}</i>" if desc else ""

        lines.append(f"✅ {p_emoji} {key_str}<b>{esc(t['title'][:60])}</b>{date_str}{desc_str}")

    if len(completed_tasks) > 20:
        lines.append(f"\n<i>...и ещё {len(completed_tasks) - 20} задач</i>")

    return "\n".join(lines)


def get_event_report(report_type: str, days: int = 7) -> str:
    """Отчёт по событиям из event_log (SQLite)."""
    try:
        from event_log import query_events, get_activity_summary
    except ImportError:
        return "Event log недоступен. Webhook-сервис не запущен."

    type_map = {
        "created": (["task-created"], "📝 Созданные задачи"),
        "moved": (["task-moved"], "🔀 Перемещения задач"),
        "comments": (["chat_message-created"], "💬 Комментарии"),
        "activity": (["task-created", "task-updated", "task-moved", "task-deleted", "chat_message-created"], "📊 Вся активность"),
    }
    event_types, title = type_map.get(report_type, (None, "📊 Отчёт"))

    since_ms = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
    events = query_events(event_types=event_types, since_ms=since_ms, limit=200)

    if not events:
        # Fallback — если event_log пуст (webhooks ещё не накопили данные)
        if report_type == "created":
            return _report_created_from_api(days)
        return f"Нет событий за {days} дн. Лог событий начнёт заполняться после подключения webhooks."

    lines = [f"{title} <b>за {days} дн.</b> — {len(events)} событий\n"]

    event_labels = {
        "task-created": "📝",
        "task-updated": "✏️",
        "task-moved": "🔀",
        "task-deleted": "🗑",
        "task-restored": "♻️",
        "chat_message-created": "💬",
    }

    for ev in events[:30]:
        ts = ev.get("timestamp", 0)
        dt = datetime.fromtimestamp(ts / 1000).strftime("%d.%m %H:%M") if ts else ""
        emoji = event_labels.get(ev["event_type"], "•")
        data = json.loads(ev.get("data") or "{}") if isinstance(ev.get("data"), str) else ev.get("data", {})
        title_text = data.get("title", ev.get("object_id", "")[:20])
        lines.append(f"{emoji} {dt} <b>{esc(str(title_text)[:50])}</b>")

    if len(events) > 30:
        lines.append(f"\n<i>...и ещё {len(events) - 30}</i>")

    return "\n".join(lines)


def _report_created_from_api(days: int) -> str:
    """Fallback: созданные задачи из API (по timestamp)."""
    columns = get_columns()
    if not columns:
        return "Не удалось получить колонки."

    cutoff_ts = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
    created = []

    for col in columns:
        tasks = get_column_tasks(col["id"], limit=200)
        for t in tasks:
            if t.get("timestamp", 0) >= cutoff_ts:
                t["_column"] = col["title"]
                created.append(t)

    if not created:
        return f"За последние {days} дн. нет новых задач."

    created.sort(key=lambda t: t.get("timestamp", 0), reverse=True)
    lines = [f"📝 <b>Созданные задачи за {days} дн.</b> — {len(created)} шт.\n"]

    for t in created[:20]:
        key = t.get("idTaskProject") or t.get("idTaskCommon") or ""
        key_str = f"<code>{esc(key)}</code> " if key else ""
        ts = t.get("timestamp", 0)
        dt = datetime.fromtimestamp(ts / 1000).strftime("%d.%m") if ts else ""
        lines.append(f"📝 {key_str}<b>{esc(t['title'][:60])}</b> ({dt}) → {esc(t.get('_column', ''))}")

    if len(created) > 20:
        lines.append(f"\n<i>...и ещё {len(created) - 20}</i>")

    return "\n".join(lines)


# --- AI-анализ данных ---
def ai_report_summary(report_text: str, report_type: str, days: int) -> str:
    """AI-саммари для отчёта. Возвращает текст анализа или '' при ошибке."""
    plain = strip_html(report_text)[:2000]
    if len(plain) < 30:
        return ""
    type_names = {
        "completed": "завершённые задачи", "created": "созданные задачи",
        "moved": "перемещения задач", "comments": "комментарии",
        "activity": "вся активность", "workload": "загрузка команды",
    }
    type_name = type_names.get(report_type, "отчёт")
    prompt = (
        f"Ты — аналитик задач. Проанализируй отчёт ({type_name}) за {days} дней.\n\n"
        f"Отчёт:\n{plain}\n\n"
        f"Ответь кратко (3-5 пунктов):\n"
        f"1. Главные достижения или итоги\n"
        f"2. Узкие места (что буксует или вызывает вопросы)\n"
        f"3. Рекомендации (на что обратить внимание)\n\n"
        f"До 500 символов. На русском. Без markdown."
    )
    try:
        return _ai_call(MODELS_ANALYSIS, [{"role": "user", "content": prompt}], max_tokens=600)
    except Exception as e:
        logger.warning(f"AI report summary failed: {e}")
        return ""


def get_workload_report(days: int = 7) -> str:
    """Отчёт по загрузке: активные, созданные, завершённые, по исполнителям."""
    columns = get_columns()
    if not columns:
        return "Не удалось получить колонки."

    # 1. Текущее состояние по активным колонкам
    col_counts = {}
    all_active = []
    for col in columns:
        if col["title"] not in ACTIVE_COLUMNS:
            continue
        tasks = get_column_tasks(col["id"])
        active = [t for t in tasks if not t.get("completed") and not t.get("archived")]
        col_counts[col["title"]] = len(active)
        all_active.extend(active)

    total_active = sum(col_counts.values())

    # 2. Создано / завершено за период из event_log
    created_count = 0
    completed_count = 0
    since_ms = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
    try:
        from event_log import query_events
        created_events = query_events(event_types=["task-created"], since_ms=since_ms, limit=500)
        created_count = len(created_events)
        updated_events = query_events(event_types=["task-updated"], since_ms=since_ms, limit=500)
        for ev in updated_events:
            data = json.loads(ev.get("data") or "{}") if isinstance(ev.get("data"), str) else ev.get("data", {})
            if data.get("completed"):
                completed_count += 1
    except Exception:
        # Fallback: считаем завершённые из API
        cutoff_ts = since_ms
        for col in columns:
            for t in get_column_tasks(col["id"], limit=200):
                if t.get("completed"):
                    ct = t.get("completedTimestamp") or t.get("timestamp", 0)
                    if ct >= cutoff_ts:
                        completed_count += 1
                elif t.get("timestamp", 0) >= cutoff_ts and col["title"] in ACTIVE_COLUMNS:
                    created_count += 1

    # 3. По исполнителям (текущие активные)
    assignee_counts: dict[str, int] = {}
    for t in all_active:
        for uid in (t.get("assigned") or []):
            name = resolve_user_name(uid) or uid[:8]
            assignee_counts[name] = assignee_counts.get(name, 0) + 1
    unassigned = sum(1 for t in all_active if not t.get("assigned"))

    # 4. Форматируем
    cols_str = " | ".join(f"{k}: {v}" for k, v in col_counts.items())
    delta = created_count - completed_count
    delta_emoji = "📈" if delta > 0 else ("📉" if delta < 0 else "➡️")
    delta_sign = f"+{delta}" if delta > 0 else str(delta)

    lines = [
        f"📈 <b>Загрузка за {days} дн.</b>\n",
        f"📊 Активных: <b>{total_active}</b>",
        f"  {cols_str}\n",
        f"📝 Создано: <b>{created_count}</b>  ✅ Завершено: <b>{completed_count}</b>",
        f"{delta_emoji} Бэклог: <b>{delta_sign}</b>\n",
    ]

    if assignee_counts:
        lines.append("👥 <b>Исполнители:</b>")
        for name, cnt in sorted(assignee_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {esc(name)}: {cnt} задач")
    if unassigned:
        lines.append(f"  <i>Без исполнителя: {unassigned}</i>")

    return "\n".join(lines)


def ai_workload_analysis(report_text: str, days: int) -> str:
    """AI-анализ загрузки команды."""
    plain = strip_html(report_text)[:1500]
    if len(plain) < 20:
        return ""
    prompt = (
        f"Ты — аналитик загруженности команды. Данные за {days} дней:\n\n"
        f"{plain}\n\n"
        f"Ответь кратко:\n"
        f"1. Тренд: бэклог растёт или сокращается?\n"
        f"2. Баланс: создание vs завершение\n"
        f"3. Загрузка: кто перегружен?\n"
        f"4. Рекомендация: что улучшить\n\n"
        f"До 500 символов. На русском. Без markdown."
    )
    try:
        return _ai_call(MODELS_ANALYSIS, [{"role": "user", "content": prompt}], max_tokens=600)
    except Exception as e:
        logger.warning(f"AI workload analysis failed: {e}")
        return ""


# --- Промпт и разбор одной задачи ---
def _task_parse_prompt(today: str) -> str:
    directions = ", ".join(DIRECTION_STATES.keys())
    return (
        f"Сегодня {today}. Ты — ассистент по задачам. "
        f"Пользователь описывает одну задачу свободным текстом или голосом. "
        f"Извлеки из описания:\n"
        f'- "title": краткий заголовок задачи, до 80 символов\n'
        f'- "description": дополнительный контекст или детали (может быть пустым)\n'
        f'- "deadline": дата в формате YYYY-MM-DD или null (понедельник/пятница/через неделю — рассчитай от сегодня)\n'
        f'- "priority": "High" (важно/срочно), "Medium" (нормально/обычно), "Low" (не срочно/не важно). '
        f'Если не указано — "Medium"\n'
        f'- "direction": одно из [{directions}] или null. Определяй по названиям проектов/контексту\n'
        f'- "subtasks": массив строк — подзадачи/шаги (если перечислены), иначе []\n\n'
        f"Верни только JSON-объект без пояснений."
    )


def parse_single_task(text: str) -> dict:
    """Разбирает свободный текст в структуру одной задачи через AI."""
    today = date.today().strftime("%Y-%m-%d")
    prompt = _task_parse_prompt(today) + f"\n\nОписание задачи:\n{text}"
    raw = ai_generate(prompt)
    return json.loads(_clean_json(raw))


def parse_single_task_from_audio(file_path: str) -> dict:
    """Разбирает голосовое сообщение в структуру одной задачи через AI."""
    today = date.today().strftime("%Y-%m-%d")
    prompt = _task_parse_prompt(today) + "\n\nПрослушай запись и извлеки задачу."
    raw = ai_audio(file_path, prompt)
    return json.loads(_clean_json(raw))


def format_single_task_preview(task: dict) -> str:
    """Форматирует превью одной задачи в HTML."""
    priority = task.get("priority", "Medium")
    p_emoji = PRIORITY_EMOJI.get(priority, "⚪")
    priority_labels = {"High": "Важно", "Medium": "Нормально", "Low": "Не важно"}
    p_label = priority_labels.get(priority, priority)

    lines = [f"<b>{esc(task.get('title', '—'))}</b>"]

    desc = (task.get("description") or "").strip()
    if desc:
        lines.append(f"<i>{esc(desc[:200])}</i>")

    deadline = task.get("deadline")
    if deadline:
        lines.append(f"Дедлайн: {esc(deadline)}")
    else:
        lines.append("Дедлайн: не указан")

    lines.append(f"Приоритет: {p_emoji} {esc(p_label)}")

    direction = task.get("direction")
    if direction:
        lines.append(f"Направление: {esc(direction)}")

    subtasks = task.get("subtasks") or []
    if subtasks:
        lines.append("\nПодзадачи:")
        for i, st in enumerate(subtasks, 1):
            lines.append(f"  {i}. {esc(st)}")

    return "\n".join(lines)


def create_subtasks(subtask_titles: list[str], column_id: str, parent_id: str) -> None:
    """Создаёт подзадачи и привязывает к родительской задаче."""
    if not subtask_titles:
        return
    child_ids = []
    for title in subtask_titles:
        body = {"title": title[:80], "columnId": column_id}
        resp = requests.post(f"{YOUGILE_BASE_URL}/tasks", headers=_headers(), json=body)
        if resp.status_code in (200, 201):
            child_id = resp.json().get("id")
            if child_id:
                child_ids.append(child_id)
    if child_ids:
        requests.put(
            f"{YOUGILE_BASE_URL}/tasks/{parent_id}",
            headers=_headers(),
            json={"subtasks": child_ids},
        )


# --- Извлечение задач из текста ---
def _extraction_prompt(today: str) -> str:
    return (
        f"Извлеки задачи из текста. Для каждой:\n"
        f'- "title" — краткое название (до 80 символов)\n'
        f'- "description" — контекст\n'
        f'- "assignee" — кто отвечает (или "не назначен")\n'
        f'- "deadline" — YYYY-MM-DD или null\n'
        f'- "priority" — High/Medium/Low\n'
        f'- "checklist" — подшаги (массив строк) или []\n\n'
        f"Дата: {today}. Верни только JSON массив."
    )


def extract_tasks_from_text(text: str) -> list[dict]:
    today = date.today().strftime("%Y-%m-%d")
    raw = ai_generate(_extraction_prompt(today) + f"\n\nТекст:\n{text}")
    return json.loads(_clean_json(raw))


def extract_tasks_from_audio_sync(file_path: str) -> list[dict]:
    today = date.today().strftime("%Y-%m-%d")
    prompt = _extraction_prompt(today) + "\n\nПрослушай запись и извлеки задачи."
    raw = ai_audio(file_path, prompt)
    return json.loads(_clean_json(raw))


def format_tasks_preview(tasks: list[dict]) -> str:
    lines = [f"Найдено: <b>{len(tasks)}</b>\n"]
    for i, t in enumerate(tasks, 1):
        deadline = esc(t.get("deadline") or "—")
        assignee = esc(t.get("assignee") or "—")
        emoji = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(t.get("priority", "Medium"), "⚪")
        lines.append(f"{i}. {emoji} <b>{esc(t['title'])}</b>\n   👤 {assignee} | 📅 {deadline}")
        if t.get("checklist"):
            lines.append(f"   ✅ {len(t['checklist'])} подшагов")
    return "\n".join(lines)


# --- Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Йо! Я <b>Вася</b> — твой пацан по задачам YouGile.\n\n"
        "📋 Активные задачи — что горит\n"
        "➕ Новая задача — закинуть дело\n"
        "📊 Отчёт — чё сделали за период\n"
        "🎯 Приоритизация — разложить по полочкам\n"
        "📱 Дашборд — красивая сводка\n\n"
        "Можешь просто написать — разберёмся.",
        parse_mode="HTML", reply_markup=MAIN_MENU,
    )


async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Открытие Mini App дашборда через inline-кнопку."""
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📱 Открыть дашборд", web_app=WebAppInfo(url=WEBAPP_URL)),
    ]])
    await update.message.reply_text(
        "Нажми кнопку, чтобы открыть дашборд с задачами:",
        reply_markup=keyboard,
    )


async def handle_active_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Гружу задачки...", reply_markup=MAIN_MENU)
    try:
        loop = asyncio.get_event_loop()
        text, tasks_raw = await loop.run_in_executor(None, get_active_tasks_full)
        await context.bot.edit_message_text(
            text, chat_id=update.effective_chat.id, message_id=msg.message_id,
            parse_mode="HTML", disable_web_page_preview=True,
        )
        # AI-анализ вторым сообщением (без повторных API-запросов)
        if tasks_raw:
            ai_text = await loop.run_in_executor(None, ai_active_analysis, tasks_raw)
            if ai_text:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"🤖 <b>На что обратить внимание:</b>\n{esc(ai_text)}",
                    parse_mode="HTML",
                )
    except Exception as e:
        await context.bot.edit_message_text(
            f"Ошибка: {esc(e)}", chat_id=update.effective_chat.id, message_id=msg.message_id,
        )


async def handle_report_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает выбор типа отчёта."""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Завершённые", callback_data="rtype_completed"),
         InlineKeyboardButton("📝 Созданные", callback_data="rtype_created")],
        [InlineKeyboardButton("🔀 Перемещения", callback_data="rtype_moved"),
         InlineKeyboardButton("💬 Комментарии", callback_data="rtype_comments")],
        [InlineKeyboardButton("📊 Вся активность", callback_data="rtype_activity")],
        [InlineKeyboardButton("📈 Загрузка", callback_data="rtype_workload")],
    ])
    await update.message.reply_text(
        "Какой отчёт нужен, бро?", reply_markup=keyboard,
    )


async def handle_report_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор типа отчёта → выбор периода."""
    query = update.callback_query
    await query.answer()
    report_type = query.data.replace("rtype_", "")
    context.user_data["report_type"] = report_type

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("3 дня", callback_data="report_3"),
         InlineKeyboardButton("7 дней", callback_data="report_7"),
         InlineKeyboardButton("14 дней", callback_data="report_14")],
        [InlineKeyboardButton("30 дней", callback_data="report_30")],
    ])
    type_labels = {
        "completed": "✅ Завершённые",
        "created": "📝 Созданные",
        "moved": "🔀 Перемещения",
        "comments": "💬 Комментарии",
        "activity": "📊 Вся активность",
        "workload": "📈 Загрузка",
    }
    label = type_labels.get(report_type, report_type)
    await query.edit_message_text(f"{label}\nЗа какой период?", reply_markup=keyboard)


async def handle_report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    days = int(query.data.replace("report_", ""))
    report_type = context.user_data.pop("report_type", "completed")

    await query.edit_message_text(f"Собираю отчёт за {days} дн....")
    try:
        loop = asyncio.get_event_loop()
        if report_type == "completed":
            text = await loop.run_in_executor(None, get_completed_report, days)
        elif report_type == "workload":
            text = await loop.run_in_executor(None, get_workload_report, days)
        elif report_type in ("created", "moved", "comments", "activity"):
            text = await loop.run_in_executor(None, get_event_report, report_type, days)
        else:
            text = await loop.run_in_executor(None, get_completed_report, days)
        await query.edit_message_text(text, parse_mode="HTML", disable_web_page_preview=True)
        # AI-анализ вторым сообщением
        if report_type == "workload":
            ai_text = await loop.run_in_executor(None, ai_workload_analysis, text, days)
        else:
            ai_text = await loop.run_in_executor(None, ai_report_summary, text, report_type, days)
        if ai_text:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"🤖 <b>Анализ:</b>\n{esc(ai_text)}",
                parse_mode="HTML",
            )
    except Exception as e:
        await query.edit_message_text(f"Ошибка: {esc(e)}")


async def handle_add_task_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Го задачу! Пиши текстом или кидай голосовое.\n\n"
        "Можно в свободной форме, я разберусь:\n"
        "<i>«Сделать презу для Welcome, дедлайн пятница, важно»</i>\n"
        "<i>«Закупить домен для Альпины, не горит»</i>\n\n"
        "А если у тебя запись встречи — кидай аудио (.mp3/.m4a/.wav) или .txt, разберу по полочкам.",
        parse_mode="HTML", reply_markup=MAIN_MENU,
    )
    context.user_data["awaiting_task"] = True


# --- AI-разбор и подтверждение одной задачи ---

# Маппинг callback → название направления
DIRECTION_CALLBACK_MAP = {
    "sdir_alpina":   "Альпина",
    "sdir_welcome":  "Welcome",
    "sdir_personal": "Личное",
    "sdir_agency":   "Агентство",
}


def _deadline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Сегодня",      callback_data="sdt_0"),
            InlineKeyboardButton("Завтра",        callback_data="sdt_1"),
        ],
        [
            InlineKeyboardButton("Через 3 дня",  callback_data="sdt_3"),
            InlineKeyboardButton("Через неделю", callback_data="sdt_7"),
        ],
        [InlineKeyboardButton("Без дедлайна",    callback_data="sdt_skip")],
    ])


def _direction_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Альпина",  callback_data="sdir_alpina"),
            InlineKeyboardButton("Welcome",  callback_data="sdir_welcome"),
        ],
        [
            InlineKeyboardButton("Личное",   callback_data="sdir_personal"),
            InlineKeyboardButton("Агентство", callback_data="sdir_agency"),
        ],
        [InlineKeyboardButton("Пропустить", callback_data="sdir_skip")],
    ])


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Го создавать!", callback_data="stask_confirm"),
        InlineKeyboardButton("❌ Не, отбой",    callback_data="stask_cancel"),
    ]])


async def _show_single_task_preview(update, context, task: dict, msg):
    """Показывает превью задачи и определяет первый недостающий шаг."""
    user_id = update.effective_user.id
    pending_single_task[user_id] = task
    preview = format_single_task_preview(task)

    if not task.get("deadline"):
        task["_step"] = "confirm_deadline"
        text = preview + "\n\n⏰ Братан, дедлайна не было. Поставим?"
        keyboard = _deadline_keyboard()
    elif not task.get("direction"):
        task["_step"] = "confirm_direction"
        text = preview + "\n\n📦 Какое направление? Или пропустим?"
        keyboard = _direction_keyboard()
    else:
        task["_step"] = "ready"
        text = preview
        keyboard = _confirm_keyboard()

    await context.bot.edit_message_text(
        text,
        chat_id=update.effective_chat.id,
        message_id=msg.message_id,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def _advance_to_direction_or_confirm(query, task: dict) -> None:
    """После выбора дедлайна: переходим к направлению или к финальному превью."""
    preview = format_single_task_preview(task)
    if not task.get("direction"):
        task["_step"] = "confirm_direction"
        await query.edit_message_text(
            preview + "\n\n📦 Какое направление? Или пропустим?",
            parse_mode="HTML",
            reply_markup=_direction_keyboard(),
        )
    else:
        task["_step"] = "ready"
        await query.edit_message_text(
            preview,
            parse_mode="HTML",
            reply_markup=_confirm_keyboard(),
        )


async def handle_stask_deadline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора дедлайна (sdt_)."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    task = pending_single_task.get(user_id)
    if not task:
        await query.edit_message_text("Братан, сессия протухла. Давай по новой.")
        return

    choice = query.data.replace("sdt_", "")
    if choice != "skip":
        days = int(choice)
        dl_date = date.today() + timedelta(days=days)
        task["deadline"] = dl_date.strftime("%Y-%m-%d")

    await _advance_to_direction_or_confirm(query, task)


async def handle_stask_direction_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора направления (sdir_)."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    task = pending_single_task.get(user_id)
    if not task:
        await query.edit_message_text("Братан, сессия протухла. Давай по новой.")
        return

    if query.data != "sdir_skip":
        direction = DIRECTION_CALLBACK_MAP.get(query.data)
        if direction:
            task["direction"] = direction

    task["_step"] = "ready"
    preview = format_single_task_preview(task)
    await query.edit_message_text(
        preview,
        parse_mode="HTML",
        reply_markup=_confirm_keyboard(),
    )


async def handle_single_task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение или отмена создания одной задачи."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if query.data == "stask_cancel":
        pending_single_task.pop(user_id, None)
        await query.edit_message_text("Отбой, не вопрос.")
        return

    task = pending_single_task.pop(user_id, None)
    if not task:
        await query.edit_message_text("Братан, сессия протухла. Давай по новой.")
        return

    await query.edit_message_text("Погнали, создаю...")
    loop = asyncio.get_event_loop()
    column_id = await loop.run_in_executor(None, find_column_id, ["Надо сделать"])
    if not column_id:
        await query.edit_message_text("Блин, колонку 'Надо сделать' не нашёл. Чекни доску.")
        return

    ok, data = await loop.run_in_executor(None, create_yougile_task, task, column_id)
    if not ok:
        await query.edit_message_text(f"Ошибка: {esc(data)}")
        return

    tid = data.get("id", "")
    key = data.get("idTaskProject") or data.get("key") or ""
    key_str = f" <code>{esc(key)}</code>" if key else ""

    # Создаём подзадачи
    subtasks = task.get("subtasks") or []
    if subtasks and tid:
        await loop.run_in_executor(None, create_subtasks, subtasks, column_id, tid)

    # Формируем итог
    priority = task.get("priority", "Medium")
    p_emoji = PRIORITY_EMOJI.get(priority, "")
    priority_labels = {"High": "Важно", "Medium": "Нормально", "Low": "Не важно"}
    p_label = f"{p_emoji} {priority_labels.get(priority, priority)}"

    dl = task.get("deadline")
    dl_line = f"\nДедлайн: {esc(dl)}" if dl else ""

    direction = task.get("direction")
    dir_line = f"\nНаправление: {esc(direction)}" if direction else ""

    sub_line = f"\nПодзадач создано: {len(subtasks)}" if subtasks else ""

    await query.edit_message_text(
        f"Задача залетела! 🚀{key_str}\n<b>{esc(task['title'][:80])}</b>"
        f"\nПриоритет: {p_label}{dl_line}{dir_line}{sub_line}"
        f"\n<a href=\"{task_url(tid)}\">Открыть в YouGile</a>",
        parse_mode="HTML", disable_web_page_preview=True,
    )


async def prioritize_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню анализа задач — готовые фильтры без AI."""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Просроченные", callback_data="prio_overdue"),
         InlineKeyboardButton("⏰ Горят (3 дня)", callback_data="prio_soon")],
        [InlineKeyboardButton("❓ Без приоритета", callback_data="prio_noprio"),
         InlineKeyboardButton("📋 Без дедлайна", callback_data="prio_nodl")],
        [InlineKeyboardButton("🤖 AI расставить приоритеты", callback_data="prio_ai")],
    ])
    await update.message.reply_text(
        "🎯 <b>Чё анализируем?</b>",
        parse_mode="HTML", reply_markup=keyboard,
    )


def _get_filtered_tasks(filter_type: str) -> str:
    """Фильтрация задач без AI — чисто по данным API."""
    columns = get_columns()
    if not columns:
        return "Не удалось получить колонки."

    today_ts = int(datetime.now().timestamp() * 1000)
    today_date = date.today()
    results = []

    for col in columns:
        if col["title"] not in ACTIVE_COLUMNS:
            continue
        tasks = get_column_tasks(col["id"])
        for t in tasks:
            if t.get("completed") or t.get("archived"):
                continue

            dl_raw = t.get("deadline")
            dl_ts = dl_raw.get("deadline") if isinstance(dl_raw, dict) else None
            has_deadline = dl_ts is not None
            dl_date_obj = datetime.fromtimestamp(dl_ts / 1000).date() if dl_ts else None
            days_left = (dl_date_obj - today_date).days if dl_date_obj else None

            stickers = t.get("stickers") or {}
            has_priority = bool(stickers.get(STICKER_PRIORITY_ID))

            if filter_type == "overdue" and has_deadline and days_left is not None and days_left < 0:
                results.append((t, col["title"], days_left))
            elif filter_type == "soon" and has_deadline and days_left is not None and 0 <= days_left <= 3:
                results.append((t, col["title"], days_left))
            elif filter_type == "noprio" and not has_priority:
                results.append((t, col["title"], days_left))
            elif filter_type == "nodl" and not has_deadline:
                results.append((t, col["title"], days_left))

    if not results:
        labels = {"overdue": "просроченных", "soon": "горящих", "noprio": "без приоритета", "nodl": "без дедлайна"}
        return f"Нет {labels.get(filter_type, '')} задач. Всё в порядке!"

    titles = {"overdue": "🔥 Просроченные", "soon": "⏰ Горят (≤3 дня)", "noprio": "❓ Без приоритета", "nodl": "📋 Без дедлайна"}
    lines = [f"{titles.get(filter_type, '🎯')} — {len(results)} шт.\n"]

    for t, col_title, days_left in results[:20]:
        key = t.get("idTaskProject") or t.get("idTaskCommon") or ""
        key_str = f"<code>{esc(key)}</code> " if key else ""
        stickers = t.get("stickers") or {}
        priority = PRIORITY_MAP_INV.get(stickers.get(STICKER_PRIORITY_ID, ""), "")
        p_emoji = PRIORITY_EMOJI.get(priority, "⚪")

        dl_str = ""
        if days_left is not None:
            if days_left < 0:
                dl_str = f" 🔥 просрочен {abs(days_left)}д"
            elif days_left == 0:
                dl_str = " ⚡ сегодня"
            elif days_left <= 3:
                dl_str = f" ⏰ {days_left}д"

        lines.append(f"{p_emoji} {key_str}<b>{esc(t['title'][:55])}</b>{dl_str}\n  → {esc(col_title)}")

    if len(results) > 20:
        lines.append(f"\n<i>...и ещё {len(results) - 20}</i>")

    return "\n".join(lines)


async def handle_prio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    filter_type = query.data.replace("prio_", "")

    if filter_type == "ai":
        await query.edit_message_text("🤖 AI расставляет приоритеты (до 10 задач)...")
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, ai_prioritizer.run_prioritization, YOUGILE_API_KEY)
            await query.edit_message_text(esc(result))
        except Exception as e:
            await query.edit_message_text(f"Ошибка: {esc(e)}")
        return

    await query.edit_message_text("Анализирую...")
    try:
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, _get_filtered_tasks, filter_type)
        await query.edit_message_text(text, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        await query.edit_message_text(f"Ошибка: {esc(e)}")


async def chat_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_history.pop(update.effective_user.id, None)
    context.user_data.clear()
    await update.message.reply_text("Чат обнулён. Как будто ничего не было 😎", reply_markup=MAIN_MENU)


# --- Голосовое → задача (с полным AI-разбором) ---
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Голосовое используется для создания одной задачи только если ожидается задача,
    # либо всегда (удобнее — всегда разбираем как задачу)
    msg = await update.message.reply_text("Секунду, слушаю...")
    voice_path = "voice.ogg"
    try:
        voice_file = await update.message.voice.get_file()
        await voice_file.download_to_drive(voice_path)
        loop = asyncio.get_event_loop()
        task = await loop.run_in_executor(None, parse_single_task_from_audio, voice_path)
        context.user_data.pop("awaiting_task", None)
        await _show_single_task_preview(update, context, task, msg)
    except json.JSONDecodeError:
        await context.bot.edit_message_text(
            "Что-то AI затупил. Попробуй по-другому сказать.",
            chat_id=update.effective_chat.id, message_id=msg.message_id,
        )
    except Exception as e:
        await context.bot.edit_message_text(
            f"Ошибка: {esc(e)}", chat_id=update.effective_chat.id, message_id=msg.message_id,
        )
    finally:
        if os.path.exists(voice_path):
            os.remove(voice_path)


# --- Транскрипт → задачи ---
async def _process_transcript(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    msg = await update.message.reply_text("Анализирую текст...")
    try:
        loop = asyncio.get_event_loop()
        tasks = await loop.run_in_executor(None, extract_tasks_from_text, text)
        if not tasks:
            await context.bot.edit_message_text("Задачи не найдены.", chat_id=update.effective_chat.id, message_id=msg.message_id)
            return
        pending_tasks[update.effective_user.id] = tasks
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Создать все", callback_data="meeting_confirm"),
            InlineKeyboardButton("❌ Отмена", callback_data="meeting_cancel"),
        ]])
        await context.bot.edit_message_text(
            format_tasks_preview(tasks), chat_id=update.effective_chat.id,
            message_id=msg.message_id, parse_mode="HTML", reply_markup=keyboard,
        )
    except json.JSONDecodeError:
        await context.bot.edit_message_text("AI вернул невалидный JSON. Попробуй ещё.", chat_id=update.effective_chat.id, message_id=msg.message_id)
    except Exception as e:
        await context.bot.edit_message_text(f"Ошибка: {esc(e)}", chat_id=update.effective_chat.id, message_id=msg.message_id)


async def handle_txt_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Читаю файл...")
    txt_path = "transcript.txt"
    try:
        doc = await update.message.document.get_file()
        await doc.download_to_drive(txt_path)
        with open(txt_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        await context.bot.edit_message_text(
            f"Файл получен ({len(text)} симв.). Анализирую...",
            chat_id=update.effective_chat.id, message_id=msg.message_id,
        )
        await _process_transcript(update, context, text)
    except Exception as e:
        await context.bot.edit_message_text(f"Ошибка: {esc(e)}", chat_id=update.effective_chat.id, message_id=msg.message_id)
    finally:
        if os.path.exists(txt_path):
            os.remove(txt_path)


async def handle_audio_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Загружаю аудио...")
    audio_path = "meeting_audio.mp3"
    try:
        if update.message.audio:
            file_obj = await update.message.audio.get_file()
            fname = update.message.audio.file_name or "audio.mp3"
        else:
            file_obj = await update.message.document.get_file()
            fname = update.message.document.file_name or "audio.mp3"
        audio_path = f"meeting_audio{os.path.splitext(fname)[1].lower()}"
        await file_obj.download_to_drive(audio_path)
        await context.bot.edit_message_text(
            "Аудио загружено. Транскрибирую...", chat_id=update.effective_chat.id, message_id=msg.message_id,
        )
        loop = asyncio.get_event_loop()
        tasks = await loop.run_in_executor(None, extract_tasks_from_audio_sync, audio_path)
        if not tasks:
            await context.bot.edit_message_text("Задачи не найдены.", chat_id=update.effective_chat.id, message_id=msg.message_id)
            return
        pending_tasks[update.effective_user.id] = tasks
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Создать все", callback_data="meeting_confirm"),
            InlineKeyboardButton("❌ Отмена", callback_data="meeting_cancel"),
        ]])
        await context.bot.edit_message_text(
            format_tasks_preview(tasks), chat_id=update.effective_chat.id,
            message_id=msg.message_id, parse_mode="HTML", reply_markup=keyboard,
        )
    except json.JSONDecodeError:
        await context.bot.edit_message_text("AI вернул невалидный JSON.", chat_id=update.effective_chat.id, message_id=msg.message_id)
    except Exception as e:
        await context.bot.edit_message_text(f"Ошибка: {esc(e)}", chat_id=update.effective_chat.id, message_id=msg.message_id)
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)


# --- Inline callbacks ---
async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if query.data == "meeting_cancel":
        pending_tasks.pop(user_id, None)
        await query.edit_message_text("Отбой, не вопрос.")
        return

    if query.data != "meeting_confirm":
        return

    tasks = pending_tasks.pop(user_id, None)
    if not tasks:
        await query.edit_message_text("Нет задач.")
        return

    await query.edit_message_text("Создаю задачи...")
    loop = asyncio.get_event_loop()
    column_id = await loop.run_in_executor(None, find_column_id)
    if not column_id:
        await query.edit_message_text("Колонка не найдена.")
        return

    results = []
    for i, task in enumerate(tasks, 1):
        ok, data = await loop.run_in_executor(None, create_yougile_task, task, column_id)
        emoji = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(task.get("priority", "Medium"), "⚪")
        if ok:
            tid = data.get("id", "")
            key = data.get("idTaskProject") or data.get("key") or ""
            key_str = f" <code>{esc(key)}</code>" if key else ""
            results.append(f"{i}. ✅ {emoji} <b>{esc(task['title'][:55])}</b>{key_str}\n   🔗 <a href=\"{task_url(tid)}\">Открыть</a>")
        else:
            results.append(f"{i}. ❌ {emoji} <b>{esc(task['title'][:55])}</b>\n   {esc(str(data)[:80])}")

    ok_count = sum(1 for r in results if "✅" in r)
    summary = f"Создано <b>{ok_count}/{len(tasks)}</b>:\n\n" + "\n\n".join(results)
    await query.edit_message_text(summary, parse_mode="HTML", disable_web_page_preview=True)


# --- Текстовые сообщения ---
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # Кнопки меню
    if text == BTN_ACTIVE:
        await handle_active_tasks(update, context)
        return
    if text == BTN_ADD_TASK:
        await handle_add_task_prompt(update, context)
        return
    if text == BTN_REPORT:
        await handle_report_menu(update, context)
        return
    if text == BTN_PRIORITIZE:
        await prioritize_command(update, context)
        return
    if not text:
        return

    # Если ожидаем задачу (после нажатия ➕) — разбираем через AI
    if context.user_data.get("awaiting_task"):
        context.user_data.pop("awaiting_task", None)
        msg = await update.message.reply_text("Разбираю, чё написал...")
        try:
            loop = asyncio.get_event_loop()
            task = await loop.run_in_executor(None, parse_single_task, text)
            await _show_single_task_preview(update, context, task, msg)
        except json.JSONDecodeError:
            await context.bot.edit_message_text(
                "Что-то AI затупил. Попробуй по-другому сказать.",
                chat_id=update.effective_chat.id, message_id=msg.message_id,
            )
        except Exception as e:
            await context.bot.edit_message_text(
                f"Ошибка: {esc(e)}", chat_id=update.effective_chat.id, message_id=msg.message_id,
            )
        return

    # Обычный чат
    typing_msg = await update.message.reply_text("...")
    try:
        loop = asyncio.get_event_loop()
        reply = await loop.run_in_executor(None, ai_chat, update.effective_user.id, text)
        await context.bot.edit_message_text(
            reply, chat_id=update.effective_chat.id, message_id=typing_msg.message_id,
        )
    except Exception as e:
        await context.bot.edit_message_text(
            f"Ошибка: {esc(e)}", chat_id=update.effective_chat.id, message_id=typing_msg.message_id,
        )


# --- Запуск ---
if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN не задан.")
        exit(1)

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("tasks", handle_active_tasks))
    app.add_handler(CommandHandler("report", handle_report_menu))
    app.add_handler(CommandHandler("prioritize", prioritize_command))
    app.add_handler(CommandHandler("dashboard", dashboard_command))
    app.add_handler(CommandHandler("reset", chat_reset))

    # Медиа
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio_file))
    app.add_handler(MessageHandler(
        filters.Document.FileExtension("mp3") | filters.Document.FileExtension("m4a")
        | filters.Document.FileExtension("wav") | filters.Document.FileExtension("flac")
        | filters.Document.FileExtension("aac"),
        handle_audio_file,
    ))
    app.add_handler(MessageHandler(filters.Document.FileExtension("txt"), handle_txt_file))

    # Callbacks — создание одной задачи (дедлайн / направление / подтверждение)
    app.add_handler(CallbackQueryHandler(handle_stask_deadline_callback,   pattern="^sdt_"))
    app.add_handler(CallbackQueryHandler(handle_stask_direction_callback,  pattern="^sdir_"))
    app.add_handler(CallbackQueryHandler(handle_single_task_callback,      pattern="^stask_"))
    # Callbacks — отчёты
    app.add_handler(CallbackQueryHandler(handle_report_type_callback, pattern="^rtype_"))
    app.add_handler(CallbackQueryHandler(handle_report_callback, pattern="^report_"))
    # Callbacks — анализ задач
    app.add_handler(CallbackQueryHandler(handle_prio_callback, pattern="^prio_"))
    # Callbacks — прочее
    app.add_handler(CallbackQueryHandler(handle_confirmation, pattern="^meeting_"))

    # Текст — последним
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Настройка меню бота при запуске
    async def post_init(application):
        from telegram import BotCommand, MenuButtonWebApp
        await application.bot.set_my_commands([
            BotCommand("start", "Главное меню"),
            BotCommand("tasks", "Активные задачи"),
            BotCommand("report", "Отчёт"),
            BotCommand("prioritize", "Приоритизация"),
            BotCommand("dashboard", "Дашборд"),
            BotCommand("reset", "Сброс чата"),
        ])
        try:
            await application.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="📱 Дашборд", web_app=WebAppInfo(url=WEBAPP_URL))
            )
        except Exception as e:
            logger.warning(f"Не удалось установить Menu Button: {e}")

    app.post_init = post_init

    print("Пацанский бот запущен")
    app.run_polling()
