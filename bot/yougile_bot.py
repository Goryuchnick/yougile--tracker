# -*- coding: utf-8 -*-
import html
import logging
import os
import requests
import json
import asyncio
import time
import re
from functools import partial
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters,
)
from openai import OpenAI
import ai_prioritizer
import yougile_config as yc

load_dotenv()

# --- Конфигурация ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
YOUGILE_BASE_URL   = "https://yougile.com/api-v2"
YOUGILE_API_KEY    = os.environ.get("YOUGILE_API_KEY")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

# Чат: дешёвая + free fallback
MODELS_CHAT = [
    "google/gemini-3.1-flash-lite-preview",  # $0.25/M — стабильная, дешёвая
    "arcee-ai/trinity-large-preview:free",    # fallback free
]
# Задачи: умная модель для JSON-парсинга и отчётов
MODELS_TASK = [
    "qwen/qwen2.5-14b-instruct",              # компактная модель для JSON/задач
    "qwen/qwen2.5-7b-instruct",               # дешёвый fallback
    "mistralai/mistral-nemo",                 # fallback для простых структур
    "google/gemini-3-flash-preview",          # надёжный fallback
]
# Анализ: саммари/рекомендации
MODELS_ANALYSIS = [
    "qwen/qwen2.5-7b-instruct",               # компактный анализ
    "mistralai/mistral-nemo",                 # дешёвый fallback
    "google/gemini-3.1-flash-lite-preview",   # надёжный fallback
]
# Аудио: транскрипция голоса
MODELS_AUDIO = [
    "google/gemini-3-flash-preview",          # мультимодальный, поддерживает аудио
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

# Проект и доска (см. yougile_config / env YOUGILE_DEFAULT_PROJECT, YOUGILE_DEFAULT_BOARD)
TARGET_PROJECT = yc.DEFAULT_PROJECT
TARGET_BOARD = yc.DEFAULT_BOARD
EVENT_LOG_API_URL = os.environ.get("EVENT_LOG_API_URL", "").rstrip("/")

# Колонки с завершёнными задачами (для будущих фильтров)
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
chat_history:    dict[int, list[dict]] = {}
chat_history_ts: dict[int, float] = {}  # timestamp последнего сообщения (для TTL 2 ч)
task_draft:    dict[int, dict] = {}   # user_id -> {title, description, step, board_id, ...}
pending_single_task: dict[int, dict] = {}  # user_id -> разобранная одна задача
pending_report: dict[int, dict] = {}       # user_id -> параметры периода/направления
llm_json_metrics = {"ok": 0, "repair": 0, "fail": 0}

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


def chunk_telegram_html(text: str, max_len: int | None = None) -> list[str]:
    """Делит HTML-текст на части под лимит Telegram (~4096)."""
    limit = max_len or yc.TELEGRAM_HTML_MAX
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    buf: list[str] = []
    n = 0

    def flush():
        nonlocal buf, n
        if buf:
            parts.append("\n".join(buf))
            buf = []
            n = 0

    for raw_line in text.split("\n"):
        line = raw_line
        while len(line) > limit:
            parts.append(line[:limit])
            line = line[limit:]
        add = len(line) + (1 if buf else 0)
        if n + add > limit and buf:
            flush()
        if buf:
            n += 1
        buf.append(line)
        n += len(line)
    flush()
    return parts


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
    # Авто-сброс если прошло больше 2 часов
    last_ts = chat_history_ts.get(user_id, 0)
    if time.time() - last_ts > 7200:
        chat_history.pop(user_id, None)
    chat_history_ts[user_id] = time.time()

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


def ai_generate_json(prompt: str, expected: str = "object"):
    """JSON-вызов с repair-pass для компактных моделей."""
    try:
        raw = ai_generate(prompt)
        data = json.loads(_clean_json(raw))
        llm_json_metrics["ok"] += 1
        return data
    except Exception:
        repair_prompt = (
            f"Исправь JSON-ответ. Верни только валидный JSON ({expected}), без пояснений и markdown.\n\n"
            f"Исходный ответ:\n{raw if 'raw' in locals() else ''}"
        )
        repaired = ai_generate(repair_prompt)
        try:
            data = json.loads(_clean_json(repaired))
            llm_json_metrics["repair"] += 1
            return data
        except Exception:
            llm_json_metrics["fail"] += 1
            raise


def _safe_callback_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())[:20] or "x"


def _task_context(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> dict:
    drafts = context.user_data.setdefault("task_drafts", {})
    return drafts.setdefault(user_id, {})


def _clear_task_context(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    drafts = context.user_data.get("task_drafts", {})
    drafts.pop(user_id, None)
    context.user_data.pop("awaiting_task", None)
    context.user_data.pop("editing_single_task_field", None)


def _normalize_task(task: dict, fallback_direction: str | None = None) -> dict:
    task["title"] = (task.get("title") or "Без названия").strip()[:80]
    task["description"] = (task.get("description") or "").strip()
    task["priority"] = task.get("priority") if task.get("priority") in PRIORITY_STATES else "Medium"
    if fallback_direction and fallback_direction in DIRECTION_STATES:
        task["direction"] = fallback_direction
    elif task.get("direction") not in DIRECTION_STATES:
        task["direction"] = None
    task["subtasks"] = [str(x).strip() for x in (task.get("subtasks") or []) if str(x).strip()]
    task["checklist"] = [str(x).strip() for x in (task.get("checklist") or []) if str(x).strip()]
    if task.get("steps_mode") not in ("subtasks", "checklist"):
        task["steps_mode"] = "subtasks" if task["subtasks"] else "checklist"
    return task


def _ensure_steps_mode(task: dict) -> None:
    mode = task.get("steps_mode", "subtasks")
    if mode == "subtasks":
        if not task.get("subtasks") and task.get("checklist"):
            task["subtasks"] = list(task["checklist"])
    else:
        if not task.get("checklist") and task.get("subtasks"):
            task["checklist"] = list(task["subtasks"])


# --- YouGile API ---
def _headers():
    return {"Authorization": f"Bearer {YOUGILE_API_KEY}", "Content-Type": "application/json"}


def _headers_welcome() -> dict | None:
    if not yc.YOUGILE_API_KEY_WELCOME:
        return None
    return {"Authorization": f"Bearer {yc.YOUGILE_API_KEY_WELCOME}", "Content-Type": "application/json"}


_welcome_mirror_column_id: str | None = None


def resolve_welcome_mirror_column_id() -> str | None:
    """Колонка на доске Welcome для дубля (кэшируется)."""
    global _welcome_mirror_column_id
    if _welcome_mirror_column_id:
        return _welcome_mirror_column_id
    hw = _headers_welcome()
    if not hw:
        return None
    try:
        board_id = yc.YOUGILE_WELCOME_BOARD_ID
        if board_id:
            r = requests.get(
                f"{YOUGILE_BASE_URL}/columns", headers=hw, params={"boardId": board_id, "limit": 50}, timeout=30
            )
            if r.status_code != 200:
                logger.warning("Welcome columns: HTTP %s", r.status_code)
                return None
            cols = r.json().get("content", []) or []
        else:
            rp = requests.get(f"{YOUGILE_BASE_URL}/projects", headers=hw, params={"limit": 100}, timeout=30)
            if rp.status_code != 200:
                return None
            pid = None
            for p in rp.json().get("content", []):
                if not p.get("deleted") and p.get("title") == yc.YOUGILE_WELCOME_PROJECT:
                    pid = p["id"]
                    break
            if not pid:
                logger.warning("Welcome mirror: проект «%s» не найден", yc.YOUGILE_WELCOME_PROJECT)
                return None
            rb = requests.get(
                f"{YOUGILE_BASE_URL}/boards", headers=hw, params={"projectId": pid, "limit": 100}, timeout=30
            )
            if rb.status_code != 200:
                return None
            board_id = None
            for b in rb.json().get("content", []):
                if not b.get("deleted") and b.get("title") == yc.YOUGILE_WELCOME_BOARD:
                    board_id = b["id"]
                    break
            if not board_id:
                logger.warning("Welcome mirror: доска «%s» не найдена", yc.YOUGILE_WELCOME_BOARD)
                return None
            r = requests.get(
                f"{YOUGILE_BASE_URL}/columns", headers=hw, params={"boardId": board_id, "limit": 50}, timeout=30
            )
            if r.status_code != 200:
                return None
            cols = r.json().get("content", []) or []
        want = yc.normalize_column_title(yc.YOUGILE_WELCOME_COLUMN)
        for c in cols:
            if yc.normalize_column_title(c.get("title", "")) == want:
                _welcome_mirror_column_id = c["id"]
                return _welcome_mirror_column_id
        logger.warning("Welcome mirror: колонка «%s» не найдена", yc.YOUGILE_WELCOME_COLUMN)
    except Exception as e:
        logger.warning("Welcome mirror resolve: %s", e)
    return None


def mirror_task_to_welcome(task: dict) -> tuple[bool, str]:
    """Дублирует карточку в компанию Welcome (без стикеров и исполнителей). Возвращает (успех, HTML-фрагмент для сообщения)."""
    if not yc.YOUGILE_API_KEY_WELCOME:
        return True, ""
    hw = _headers_welcome()
    col_id = resolve_welcome_mirror_column_id()
    if not col_id:
        return False, "\n⚠️ Дубль Welcome: проверь YOUGILE_WELCOME_* и названия проекта/доски/колонки."

    body: dict = {
        "title": task["title"][:80],
        "columnId": col_id,
        "description": (task.get("description") or "").strip(),
    }
    if task.get("deadline"):
        try:
            dl = datetime.strptime(task["deadline"], "%Y-%m-%d")
            body["deadline"] = {"deadline": int(dl.timestamp() * 1000), "withTime": False}
        except ValueError:
            pass
    prefix = (os.environ.get("YOUGILE_WELCOME_DESC_PREFIX") or "[Дубль] ").strip()
    if body["description"]:
        body["description"] = f"{prefix}\n{body['description']}"
    else:
        body["description"] = prefix

    try:
        resp = requests.post(f"{YOUGILE_BASE_URL}/tasks", headers=hw, json=body, timeout=30)
    except Exception as e:
        return False, f"\n⚠️ Дубль Welcome: {esc(str(e)[:120])}"
    if resp.status_code in (200, 201):
        wid = (resp.json() or {}).get("id", "")
        if wid:
            return True, f'\n🔗 <a href="{task_url(wid)}">Копия в Welcome</a>'
        return True, "\n✅ Дубль Welcome создан."
    return False, f"\n⚠️ Дубль Welcome: HTTP {resp.status_code}"


def invalidate_project_board_cache() -> None:
    global _project_id, _board_id
    _project_id = None
    _board_id = None


def find_default_project_board_with_diagnostics(reset_cache: bool = False) -> tuple[str | None, str | None, str]:
    """Возвращает (project_id, board_id, сообщение_об_ошибке). При успехе err пустая строка."""
    global _project_id, _board_id
    if reset_cache:
        _project_id = None
        _board_id = None
    if _project_id and _board_id:
        return _project_id, _board_id, ""
    h = _headers()
    try:
        r = requests.get(f"{YOUGILE_BASE_URL}/projects", headers=h, params={"limit": 50}, timeout=30)
    except Exception as e:
        return None, None, f"Сеть/YouGile недоступны: {e}"
    if r.status_code == 401:
        return None, None, "YouGile API: неверный ключ (401). Проверь YOUGILE_API_KEY."
    if r.status_code == 403:
        return None, None, "YouGile API: доступ запрещён (403)."
    if r.status_code != 200:
        return None, None, f"YouGile API: /projects вернул HTTP {r.status_code}."
    projects = [p for p in r.json().get("content", []) if not p.get("deleted")]
    proj_titles = [p.get("title") for p in projects]
    for p in projects:
        if p.get("title") == yc.DEFAULT_PROJECT:
            _project_id = p["id"]
            break
    if not _project_id:
        hint = ", ".join(str(t) for t in proj_titles[:8]) or "пусто"
        return None, None, (
            f"Проект «{yc.DEFAULT_PROJECT}» не найден. Укажи YOUGILE_DEFAULT_PROJECT в .env. "
            f"Доступные примеры: {hint}"
        )
    try:
        r = requests.get(
            f"{YOUGILE_BASE_URL}/boards", headers=h, params={"projectId": _project_id, "limit": 50}, timeout=30
        )
    except Exception as e:
        return _project_id, None, f"Ошибка запроса досок: {e}"
    if r.status_code != 200:
        return _project_id, None, f"YouGile API: /boards вернул HTTP {r.status_code}."
    boards = [b for b in r.json().get("content", []) if not b.get("deleted")]
    board_titles = [b.get("title") for b in boards]
    for b in boards:
        if b.get("title") == yc.DEFAULT_BOARD:
            _board_id = b["id"]
            break
    if not _board_id:
        hint = ", ".join(str(t) for t in board_titles[:8]) or "пусто"
        return _project_id, None, (
            f"Доска «{yc.DEFAULT_BOARD}» не найдена. Укажи YOUGILE_DEFAULT_BOARD в .env. "
            f"Доступные примеры: {hint}"
        )
    return _project_id, _board_id, ""


def _find_project_board() -> tuple[str | None, str | None]:
    pid, bid, _ = find_default_project_board_with_diagnostics()
    return pid, bid


def resolve_list_board_id_for_user(context: ContextTypes.DEFAULT_TYPE | None, user_id: int) -> tuple[str | None, str]:
    """Доска для списков: последний выбор в «Новая задача», иначе дефолт из env."""
    if context and getattr(context, "user_data", None):
        draft = context.user_data.get("task_drafts", {}).get(user_id) or {}
        bid = draft.get("board_id")
        if bid:
            cols = get_columns_by_board(bid)
            if cols:
                return bid, ""
            invalidate_project_board_cache()
            logger.warning("board_id из черновика не дал колонок, сброс кэша и дефолтная доска")
    _, bid, err = find_default_project_board_with_diagnostics()
    return bid, err


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


def get_column_tasks(column_id: str, limit: int | None = None, paginate: bool = True) -> list[dict]:
    """Список задач колонки с пагинацией (offset), до YOUGILE_TASK_LIST_MAX_PAGES страниц."""
    lim = limit if limit is not None else yc.TASK_LIST_LIMIT
    lim = max(1, min(1000, lim))
    out: list[dict] = []
    offset = 0
    for _ in range(yc.TASK_LIST_MAX_PAGES):
        try:
            r = requests.get(
                f"{YOUGILE_BASE_URL}/task-list",
                headers=_headers(),
                params={"columnId": column_id, "limit": lim, "offset": offset},
                timeout=30,
            )
        except Exception:
            break
        if r.status_code != 200:
            break
        batch = r.json().get("content", []) or []
        out.extend(batch)
        if not paginate or len(batch) < lim:
            break
        offset += len(batch)
    return out



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


def find_column_id(target_columns=None, board_id: str | None = None) -> str | None:
    if target_columns is None:
        target_columns = ["Входящие", "Надо сделать", "Бэклог"]
    columns = get_columns_by_board(board_id) if board_id else get_columns()
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
def get_active_tasks_full(board_id: str | None = None, list_diag: str = "") -> tuple[str, list[dict]]:
    """Собирает задачи из активных колонок. Возвращает (HTML-текст, raw для AI)."""
    active_norm = yc.active_column_normalized_set()
    if board_id:
        columns = get_columns_by_board(board_id)
    else:
        columns = get_columns()
    if not columns:
        hint = list_diag.strip() or "Проверь YOUGILE_DEFAULT_PROJECT / YOUGILE_DEFAULT_BOARD и доступ API."
        return f"Не удалось получить колонки. {hint}", []

    result_parts = []
    tasks_raw = []
    total = 0
    matched_any_column = False
    for col in columns:
        if not yc.column_title_matches(col.get("title", ""), active_norm):
            continue
        matched_any_column = True
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
        all_titles = [c.get("title") or "?" for c in columns]
        titles_hint = ", ".join(all_titles[:20])
        if not matched_any_column:
            want = ", ".join(yc.ACTIVE_COLUMN_TITLES)
            return (
                f"На доске нет колонок с именами как в настройке ({want}). "
                f"Колонки на доске: {titles_hint}. Задай YOUGILE_ACTIVE_COLUMNS через запятую.",
                [],
            )
        return "Нет активных задач в выбранных колонках. Всё чисто! 💪", []

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



# --- AI-анализ данных ---

def collect_work_summary(days: int, direction: str | None = None) -> str:
    """Собирает все данные о работе за период для AI-агрегации."""
    columns = get_columns()
    if not columns:
        return ""

    cutoff_ts = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)

    completed_items = []
    active_items = []
    processed_detail = 0  # счётчик задач, для которых тянем комменты + подзадачи

    for col in columns:
        tasks = get_column_tasks(col["id"])
        for t in tasks:
            # Фильтр по направлению
            if direction:
                stickers = t.get("stickers") or {}
                task_dir_state = stickers.get(STICKER_DIRECTION_ID)
                if task_dir_state != DIRECTION_STATES.get(direction):
                    continue

            title = t["title"]
            task_id = t["id"]

            # Завершённые за период
            if t.get("completed"):
                ct = t.get("completedTimestamp") or t.get("timestamp", 0)
                if ct >= cutoff_ts:
                    desc = strip_html((t.get("description") or ""))[:200].strip()
                    completed_items.append(f"- {title}" + (f": {desc}" if desc else ""))

            # Активные задачи — собираем свежие комменты и подзадачи
            elif yc.column_title_matches(col.get("title", ""), yc.active_column_normalized_set()):
                task_info = f"- {title} [колонка: {col['title']}]"

                if processed_detail < 50:
                    processed_detail += 1

                    # Проверяем подзадачи
                    subtask_ids = t.get("subtasks") or []
                    done_subs = []
                    total_subs = len(subtask_ids)
                    for sid in subtask_ids:
                        try:
                            sr = requests.get(f"{YOUGILE_BASE_URL}/tasks/{sid}", headers=_headers())
                            if sr.status_code == 200:
                                sub = sr.json()
                                if sub.get("completed"):
                                    sub_ct = sub.get("completedTimestamp") or sub.get("timestamp", 0)
                                    if sub_ct >= cutoff_ts:
                                        done_subs.append(sub["title"])
                        except Exception:
                            pass

                    if done_subs:
                        task_info += f"\n  Выполнены подзадачи: {', '.join(done_subs)}"
                    if total_subs:
                        task_info += f"\n  Подзадач: {len(done_subs)}/{total_subs} выполнено"

                    # Свежие комменты
                    try:
                        cr = requests.get(
                            f"{YOUGILE_BASE_URL}/chats/{task_id}/messages",
                            headers=_headers(), params={"since": cutoff_ts, "limit": 10},
                        )
                        if cr.status_code == 200:
                            msgs = cr.json().get("content", [])
                            for m in msgs:
                                comment_text = strip_html((m.get("text") or ""))[:150].strip()
                                if comment_text:
                                    task_info += f"\n  Коммент: {comment_text}"
                    except Exception:
                        pass

                active_items.append(task_info)

    parts = []
    if completed_items:
        parts.append("ЗАВЕРШЁННЫЕ ЗАДАЧИ:\n" + "\n".join(completed_items))
    if active_items:
        parts.append("АКТИВНЫЕ ЗАДАЧИ (с прогрессом):\n" + "\n".join(active_items))

    return "\n\n".join(parts) if parts else "Нет данных за этот период."


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
        f'- "subtasks": массив строк — подзадачи (если перечислены), иначе []\n'
        f'- "checklist": массив строк — чеклист-шаги (если перечислены), иначе []\n\n'
        f"Верни только JSON-объект без пояснений."
    )


def parse_single_task(text: str) -> dict:
    """Разбирает свободный текст в структуру одной задачи через AI."""
    today = date.today().strftime("%Y-%m-%d")
    prompt = _task_parse_prompt(today) + f"\n\nОписание задачи:\n{text}"
    return ai_generate_json(prompt, expected="object")


def parse_single_task_from_audio(file_path: str) -> dict:
    """Разбирает голосовое сообщение: транскрипция → парсинг текста."""
    # Шаг 1: транскрибируем аудио
    transcript = ai_audio(file_path, "Транскрибируй это голосовое сообщение дословно на русском. Верни только текст, без пояснений.")
    if not transcript or not transcript.strip():
        raise ValueError("Не удалось распознать голос")
    logger.info("Voice transcript: %s", transcript[:200])
    # Шаг 2: парсим текст как обычную задачу
    return parse_single_task(transcript.strip())


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
    if task.get("board_title"):
        lines.append(f"Доска: {esc(task['board_title'])}")
    if task.get("column_title"):
        lines.append(f"Колонка: {esc(task['column_title'])}")

    subtasks = task.get("subtasks") or []
    checklist = task.get("checklist") or []
    mode = task.get("steps_mode", "subtasks")
    if mode == "subtasks" and subtasks:
        lines.append("\nПодзадачи:")
        for i, st in enumerate(subtasks, 1):
            lines.append(f"  {i}. {esc(st)}")
    elif mode == "checklist" and checklist:
        lines.append("\nЧеклист:")
        for i, st in enumerate(checklist, 1):
            lines.append(f"  {i}. {esc(st)}")

    return "\n".join(lines)


def create_subtasks(subtask_titles: list[str], column_id: str, parent_id: str, stickers: dict | None = None) -> tuple[bool, list[str]]:
    """Создаёт подзадачи и привязывает к родительской задаче.
    Возвращает (успех_линка, child_ids).
    """
    if not subtask_titles:
        return True, []
    child_ids = []
    for title in subtask_titles:
        body = {"title": title[:80], "columnId": column_id}
        if stickers:
            body["stickers"] = stickers
        resp = requests.post(f"{YOUGILE_BASE_URL}/tasks", headers=_headers(), json=body)
        if resp.status_code in (200, 201):
            child_id = resp.json().get("id")
            if child_id:
                child_ids.append(child_id)
    if child_ids:
        # Привязываем подзадачи к родителю — после этого они пропадут из колонки
        link_resp = requests.put(
            f"{YOUGILE_BASE_URL}/tasks/{parent_id}",
            headers=_headers(),
            json={"subtasks": child_ids},
        )
        if link_resp.status_code not in (200, 201):
            return False, child_ids
        verify_resp = requests.get(f"{YOUGILE_BASE_URL}/tasks/{parent_id}", headers=_headers())
        if verify_resp.status_code != 200:
            return False, child_ids
        linked_ids = set(verify_resp.json().get("subtasks") or [])
        return set(child_ids).issubset(linked_ids), child_ids
    return True, []


def add_checklist_to_task(task_id: str, items: list[str]) -> bool:
    if not items:
        return True
    payload = {
        "checklists": [{
            "title": "Чеклист",
            "items": [{"title": t[:120], "isCompleted": False} for t in items],
        }]
    }
    resp = requests.put(f"{YOUGILE_BASE_URL}/tasks/{task_id}", headers=_headers(), json=payload)
    return resp.status_code in (200, 201)


# --- Извлечение задач из текста ---
def _extraction_prompt(today: str) -> str:
    return (
        f"Извлеки задачи из текста. Для каждой:\n"
        f'- "title" — краткое название (до 80 символов)\n'
        f'- "description" — контекст\n'
        f'- "assignee" — кто отвечает (или "не назначен")\n'
        f'- "deadline" — YYYY-MM-DD или null\n'
        f'- "priority" — High/Medium/Low\n'
        f'- "subtasks" — подзадачи (массив строк) или []\n'
        f'- "checklist" — подшаги (массив строк) или []\n\n'
        f"Дата: {today}. Верни только JSON массив."
    )


def extract_tasks_from_text(text: str) -> list[dict]:
    today = date.today().strftime("%Y-%m-%d")
    return ai_generate_json(_extraction_prompt(today) + f"\n\nТекст:\n{text}", expected="array")


def extract_tasks_from_audio_sync(file_path: str) -> list[dict]:
    transcript = ai_audio(file_path, "Транскрибируй это голосовое сообщение дословно на русском. Верни только текст, без пояснений.")
    if not transcript or not transcript.strip():
        raise ValueError("Не удалось распознать голос")
    return extract_tasks_from_text(transcript.strip())


def format_tasks_preview(tasks: list[dict]) -> str:
    lines = [f"Найдено: <b>{len(tasks)}</b>\n"]
    for i, t in enumerate(tasks, 1):
        deadline = esc(t.get("deadline") or "—")
        assignee = esc(t.get("assignee") or "—")
        emoji = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(t.get("priority", "Medium"), "⚪")
        lines.append(f"{i}. {emoji} <b>{esc(t['title'])}</b>\n   👤 {assignee} | 📅 {deadline}")
        subtasks = t.get("subtasks") or []
        checklist = t.get("checklist") or []
        if subtasks:
            lines.append(f"   🧩 {len(subtasks)} подзадач")
        if checklist:
            lines.append(f"   ✅ {len(checklist)} подшагов")
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
    user_id = update.effective_user.id
    board_id, list_diag = resolve_list_board_id_for_user(context, user_id)
    msg = await update.message.reply_text("Гружу задачки...", reply_markup=MAIN_MENU)
    try:
        loop = asyncio.get_event_loop()
        text, tasks_raw = await loop.run_in_executor(
            None, partial(get_active_tasks_full, board_id=board_id, list_diag=list_diag),
        )
        chunks = chunk_telegram_html(text)
        await context.bot.edit_message_text(
            chunks[0], chat_id=update.effective_chat.id, message_id=msg.message_id,
            parse_mode="HTML", disable_web_page_preview=True,
        )
        for part in chunks[1:]:
            await context.bot.send_message(
                chat_id=update.effective_chat.id, text=part,
                parse_mode="HTML", disable_web_page_preview=True,
            )
        # AI-анализ вторым сообщением (без повторных API-запросов)
        if tasks_raw:
            ai_text = await loop.run_in_executor(None, ai_active_analysis, tasks_raw)
            if ai_text:
                ai_msg = f"🤖 <b>На что обратить внимание:</b>\n{esc(ai_text)}"
                for ac in chunk_telegram_html(ai_msg):
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id, text=ac, parse_mode="HTML",
                    )
    except Exception as e:
        await context.bot.edit_message_text(
            f"Ошибка: {esc(e)}", chat_id=update.effective_chat.id, message_id=msg.message_id,
        )


def _build_direction_keyboard(prefix: str = "sdir_", include_all: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("Альпина", callback_data=f"{prefix}alpina"),
            InlineKeyboardButton("Welcome", callback_data=f"{prefix}welcome"),
        ],
        [
            InlineKeyboardButton("Личное", callback_data=f"{prefix}personal"),
            InlineKeyboardButton("Агентство", callback_data=f"{prefix}agency"),
        ],
    ]
    if include_all:
        rows.append([InlineKeyboardButton("Все направления", callback_data=f"{prefix}all")])
    return InlineKeyboardMarkup(rows)


DIRECTION_CALLBACK_MAP = {
    "alpina": "Альпина",
    "welcome": "Welcome",
    "personal": "Личное",
    "agency": "Агентство",
}


def _period_from_text(text: str) -> tuple[int, int, str] | None:
    text = text.strip()
    iso_match = re.search(r"(\d{4}-\d{2}-\d{2})\s*[-\s]\s*(\d{4}-\d{2}-\d{2})", text)
    ru_match = re.search(r"(\d{2}\.\d{2}\.\d{4})\s*[-\s]\s*(\d{2}\.\d{2}\.\d{4})", text)
    try:
        if iso_match:
            d1 = datetime.strptime(iso_match.group(1), "%Y-%m-%d").date()
            d2 = datetime.strptime(iso_match.group(2), "%Y-%m-%d").date()
        elif ru_match:
            d1 = datetime.strptime(ru_match.group(1), "%d.%m.%Y").date()
            d2 = datetime.strptime(ru_match.group(2), "%d.%m.%Y").date()
        else:
            return None
    except ValueError:
        return None
    if d2 < d1:
        d1, d2 = d2, d1
    ts_from = int(datetime.combine(d1, datetime.min.time()).timestamp() * 1000)
    ts_to = int((datetime.combine(d2, datetime.max.time())).timestamp() * 1000)
    label = f"{d1.strftime('%d.%m.%Y')} - {d2.strftime('%d.%m.%Y')}"
    return ts_from, ts_to, label


def _event_log_summary(ts_from: int, ts_to: int, direction: str | None) -> tuple[list[str], bool]:
    if not EVENT_LOG_API_URL:
        return [], False
    try:
        days = max(1, int((ts_to - ts_from) / (1000 * 60 * 60 * 24)) + 1)
        resp = requests.get(f"{EVENT_LOG_API_URL}/events", params={"days": days, "limit": 500}, timeout=15)
        if resp.status_code != 200:
            return [], False
        events = resp.json().get("events", [])
    except Exception:
        return [], False

    lines = []
    task_dir_cache: dict[str, bool] = {}
    for ev in events:
        ts = ev.get("timestamp") or 0
        if ts < ts_from or ts > ts_to:
            continue
        object_id = ev.get("object_id") or ""
        if direction:
            if object_id not in task_dir_cache:
                tr = requests.get(f"{YOUGILE_BASE_URL}/tasks/{object_id}", headers=_headers())
                if tr.status_code == 200:
                    stickers = tr.json().get("stickers") or {}
                    task_dir_cache[object_id] = stickers.get(STICKER_DIRECTION_ID) == DIRECTION_STATES.get(direction)
                else:
                    task_dir_cache[object_id] = False
            if not task_dir_cache.get(object_id):
                continue
        ev_type = ev.get("event_type") or "event"
        dt = datetime.fromtimestamp(ts / 1000).strftime("%d.%m %H:%M")
        lines.append(f"- [{dt}] {ev_type} task={object_id[:8]}")
    return lines[:120], len(lines) >= 5


def collect_work_summary_range(ts_from: int, ts_to: int, direction: str | None = None) -> str:
    """Live API сводка по диапазону: завершения, движения, комментарии, прогресс."""
    columns = get_columns()
    if not columns:
        return "Нет данных по колонкам."
    active_norm = yc.active_column_normalized_set()
    done_lines: list[str] = []
    move_lines: list[str] = []
    comm_lines: list[str] = []
    progress_lines: list[str] = []

    for col in columns:
        tasks = get_column_tasks(col["id"])
        for t in tasks:
            stickers = t.get("stickers") or {}
            if direction and stickers.get(STICKER_DIRECTION_ID) != DIRECTION_STATES.get(direction):
                continue
            task_id = t.get("id", "")
            title = t.get("title", "")[:80]
            if not task_id or not title:
                continue

            if t.get("completed"):
                ct = t.get("completedTimestamp") or t.get("timestamp", 0)
                if ts_from <= ct <= ts_to:
                    done_lines.append(f"- {title}")

            # Оставляем только задачи с фактической активностью в периоде.
            if not yc.column_title_matches(col.get("title", ""), active_norm):
                continue
            try:
                cr = requests.get(
                    f"{YOUGILE_BASE_URL}/chats/{task_id}/messages",
                    headers=_headers(),
                    params={"since": ts_from, "limit": 100, "includeSystem": "true"},
                    timeout=15,
                )
                if cr.status_code != 200:
                    continue
                msgs = cr.json().get("content", [])
            except Exception:
                continue

            task_has_activity = False
            for m in msgs:
                msg_ts = m.get("id") or m.get("timestamp") or 0
                if not (ts_from <= msg_ts <= ts_to):
                    continue
                props = m.get("properties") or {}
                if props.get("move"):
                    task_has_activity = True
                    move_lines.append(f"- {title}: переход по статусам")
                    continue
                if props.get("gtd"):
                    task_has_activity = True
                    progress_lines.append(f"- {title}: обновление статуса выполнения")
                    continue
                txt = strip_html(m.get("text") or "")[:120].strip()
                if txt:
                    task_has_activity = True
                    comm_lines.append(f"- {title}: {txt}")

            if task_has_activity and t.get("subtasks"):
                total_subs = len(t.get("subtasks") or [])
                progress_lines.append(f"- {title}: подзадачи {total_subs} шт.")

    parts = []
    if done_lines:
        parts.append("СДЕЛАНО:\n" + "\n".join(dict.fromkeys(done_lines)))
    if move_lines:
        parts.append("ДВИЖЕНИЕ ПО СТАТУСАМ:\n" + "\n".join(dict.fromkeys(move_lines)))
    if comm_lines:
        parts.append("КОММУНИКАЦИЯ:\n" + "\n".join(list(dict.fromkeys(comm_lines))[:80]))
    if progress_lines:
        parts.append("ПРОГРЕСС:\n" + "\n".join(dict.fromkeys(progress_lines)))
    return "\n\n".join(parts) if parts else "Нет значимых действий за выбранный период."


def collect_work_summary_hybrid(ts_from: int, ts_to: int, direction: str | None) -> str:
    event_lines, enough = _event_log_summary(ts_from, ts_to, direction)
    live = collect_work_summary_range(ts_from, ts_to, direction)
    if event_lines and enough:
        return "EVENT_LOG:\n" + "\n".join(event_lines) + "\n\nLIVE_FALLBACK:\n" + live
    if event_lines:
        return "EVENT_LOG (частично):\n" + "\n".join(event_lines) + "\n\nLIVE:\n" + live
    return live


def ai_work_summary(raw_data: str, period_label: str, direction: str | None) -> str:
    """AI-агрегация сводного отчёта о проделанной работе."""
    dir_label = direction or "все"
    prompt = (
        f"Ты — Вася, пацанский AI-помощник. Составь краткий и структурный отчёт о проделанной работе.\n"
        f"Период: {period_label}. Направление: {dir_label}.\n\n"
        f"Данные:\n{raw_data}\n\n"
        f"Составь отчёт строго в формате:\n\n"
        f"Сделано:\n1. ...\n\n"
        f"Движение по статусам:\n1. ...\n\n"
        f"Коммуникация:\n1. ...\n\n"
        f"Блокеры/риски:\n1. ...\n\n"
        f"Пиши по-русски, по делу, без markdown-символов."
    )
    try:
        return _ai_call(MODELS_TASK, [{"role": "user", "content": prompt}], max_tokens=1000)
    except Exception as e:
        logger.warning(f"AI work summary failed: {e}")
        return "AI недоступен, попробуй позже."


def _report_direction_keyboard() -> InlineKeyboardMarkup:
    return _build_direction_keyboard(prefix="rdir_", include_all=True)


async def handle_report_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("3 дня", callback_data="rep_3"),
         InlineKeyboardButton("Неделя", callback_data="rep_7")],
        [InlineKeyboardButton("2 недели", callback_data="rep_14"),
         InlineKeyboardButton("Месяц", callback_data="rep_30")],
        [InlineKeyboardButton("Свой период", callback_data="rep_custom")],
    ])
    await update.message.reply_text("За какой период сделать отчёт, бро?", reply_markup=keyboard)


async def handle_report_period_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if query.data == "rep_custom":
        pending_report[user_id] = {"mode": "custom"}
        context.user_data["awaiting_report_period_text"] = True
        await query.edit_message_text("Напиши период текстом: 2026-04-01 2026-04-06 или 01.04.2026-06.04.2026")
        return

    days = int(query.data.replace("rep_", ""))
    ts_to = int(datetime.now().timestamp() * 1000)
    ts_from = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
    period_labels = {3: "3 дня", 7: "неделя", 14: "2 недели", 30: "месяц"}
    pending_report[user_id] = {"mode": "days", "days": days, "ts_from": ts_from, "ts_to": ts_to, "label": period_labels.get(days, f"{days} дней")}
    await query.edit_message_text(
        f"Период: {pending_report[user_id]['label']}. Какое направление?",
        reply_markup=_report_direction_keyboard(),
    )


async def handle_report_direction_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    report_data = pending_report.pop(user_id, None)
    context.user_data.pop("awaiting_report_period_text", None)
    if not report_data:
        await query.edit_message_text("Сессия протухла, давай по новой — жми Отчёт.")
        return

    suffix = query.data.replace("rdir_", "")
    direction = None if suffix == "all" else DIRECTION_CALLBACK_MAP.get(suffix)
    dir_label = direction or "все направления"
    label = report_data.get("label", "период")
    await query.edit_message_text(f"Собираю данные за {label} ({dir_label})...")

    try:
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(
            None, collect_work_summary_hybrid, report_data["ts_from"], report_data["ts_to"], direction
        )
        ai_text = await loop.run_in_executor(None, ai_work_summary, raw, label, direction)
        header = f"<b>Отчёт за {label}</b> | {esc(dir_label)}\n\n"
        await query.edit_message_text(header + esc(ai_text), parse_mode="HTML")
    except Exception as e:
        await query.edit_message_text(f"Ошибка: {esc(e)}")


def _project_keyboard(projects: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for p in projects[:8]:
        rows.append([InlineKeyboardButton(p.get("title", "Проект"), callback_data=f"tpr_{p['id']}")])
    return InlineKeyboardMarkup(rows)


def _board_keyboard(boards: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for b in boards[:12]:
        rows.append([InlineKeyboardButton(b.get("title", "Доска"), callback_data=f"tbd_{b['id']}")])
    return InlineKeyboardMarkup(rows)


async def handle_add_task_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    _clear_task_context(context, user_id)
    projects = get_projects()
    if not projects:
        await update.message.reply_text("Не нашёл проекты в YouGile. Проверь доступы API.", reply_markup=MAIN_MENU)
        return
    draft = _task_context(context, user_id)
    draft["projects"] = {p["id"]: p.get("title", "") for p in projects[:20]}
    await update.message.reply_text("Шаг 1/3. Выбери проект:", reply_markup=_project_keyboard(projects))


async def handle_task_project_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    project_id = query.data.replace("tpr_", "")
    boards = get_boards(project_id)
    if not boards:
        await query.edit_message_text("В проекте нет доступных досок. Выбери другой проект.")
        return
    draft = _task_context(context, user_id)
    draft["project_id"] = project_id
    draft["project_title"] = draft.get("projects", {}).get(project_id, "Проект")
    draft["boards"] = {b["id"]: b.get("title", "") for b in boards[:30]}
    await query.edit_message_text(
        f"Шаг 2/3. Проект: {draft['project_title']}\nВыбери доску:",
        reply_markup=_board_keyboard(boards),
    )


async def handle_task_board_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    board_id = query.data.replace("tbd_", "")
    draft = _task_context(context, user_id)
    if board_id not in (draft.get("boards") or {}):
        await query.edit_message_text("Сессия выбора сбилась. Нажми ➕ ещё раз.")
        return
    draft["board_id"] = board_id
    draft["board_title"] = draft["boards"][board_id]
    await query.edit_message_text(
        f"Шаг 3/3. Доска: {draft['board_title']}\nТеперь выбери направление:",
        reply_markup=_build_direction_keyboard(prefix="tdir_"),
    )


async def handle_task_direction_preset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    suffix = query.data.replace("tdir_", "")
    direction = DIRECTION_CALLBACK_MAP.get(suffix)
    draft = _task_context(context, user_id)
    if not direction or not draft.get("board_id"):
        await query.edit_message_text("Сессия выбора сбилась. Нажми ➕ ещё раз.")
        return
    draft["direction"] = direction
    context.user_data["awaiting_task"] = True
    await query.edit_message_text(
        f"Отлично. Проект: {draft.get('project_title')}\n"
        f"Доска: {draft.get('board_title')}\n"
        f"Направление: {direction}\n\n"
        "Теперь пиши задачу текстом или кидай голосовое/аудио/.txt.",
    )


# --- AI-разбор и подтверждение одной задачи ---
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
    return _build_direction_keyboard(prefix="sdir_")


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Го создавать!", callback_data="stask_confirm")],
        [InlineKeyboardButton("✏️ Внести правки", callback_data="stedit_menu")],
        [InlineKeyboardButton("❌ Не, отбой", callback_data="stask_cancel")],
    ])


def _priority_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔴 High", callback_data="stprio_High"),
        InlineKeyboardButton("🟡 Medium", callback_data="stprio_Medium"),
        InlineKeyboardButton("🟢 Low", callback_data="stprio_Low"),
    ]])


def _steps_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧩 Подзадачи", callback_data="ststeps_subtasks"),
         InlineKeyboardButton("✅ Чеклист", callback_data="ststeps_checklist")],
    ])


def _columns_keyboard(board_id: str) -> InlineKeyboardMarkup:
    cols = get_columns_by_board(board_id)
    rows = [[InlineKeyboardButton(col.get("title", "Колонка"), callback_data=f"stcol_{col['id']}")] for col in cols[:12]]
    return InlineKeyboardMarkup(rows or [[InlineKeyboardButton("Надо сделать", callback_data="stcol_default")]])


def _edit_keyboard(task: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Заголовок", callback_data="stedit_title"),
         InlineKeyboardButton("Описание", callback_data="stedit_description")],
        [InlineKeyboardButton("Дедлайн", callback_data="stedit_deadline"),
         InlineKeyboardButton("Приоритет", callback_data="stedit_priority")],
        [InlineKeyboardButton("Направление", callback_data="stedit_direction"),
         InlineKeyboardButton("Колонка", callback_data="stedit_column")],
        [InlineKeyboardButton("Формат шагов", callback_data="stedit_steps")],
        [InlineKeyboardButton("⬅️ К превью", callback_data="stedit_back")],
    ])


async def _show_single_task_preview(update, context, task: dict, msg):
    """Показывает превью задачи перед созданием."""
    user_id = update.effective_user.id
    task = _normalize_task(task, fallback_direction=task.get("direction"))
    _ensure_steps_mode(task)
    pending_single_task[user_id] = task
    preview = format_single_task_preview(task)
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
    else:
        task["deadline"] = None
    context.user_data.pop("editing_single_task_field", None)

    task["_step"] = "ready"
    await query.edit_message_text(
        format_single_task_preview(task),
        parse_mode="HTML",
        reply_markup=_confirm_keyboard(),
    )


async def handle_stask_direction_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора направления (sdir_)."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    task = pending_single_task.get(user_id)
    if not task:
        await query.edit_message_text("Братан, сессия протухла. Давай по новой.")
        return

    direction = DIRECTION_CALLBACK_MAP.get(query.data.replace("sdir_", ""))
    if direction:
        task["direction"] = direction
    context.user_data.pop("editing_single_task_field", None)

    task["_step"] = "ready"
    preview = format_single_task_preview(task)
    await query.edit_message_text(
        preview,
        parse_mode="HTML",
        reply_markup=_confirm_keyboard(),
    )


async def handle_stask_priority_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    task = pending_single_task.get(user_id)
    if not task:
        await query.edit_message_text("Сессия протухла. Нажми ➕ ещё раз.")
        return
    task["priority"] = query.data.replace("stprio_", "")
    context.user_data.pop("editing_single_task_field", None)
    await query.edit_message_text(format_single_task_preview(task), parse_mode="HTML", reply_markup=_confirm_keyboard())


async def handle_stask_steps_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    task = pending_single_task.get(user_id)
    if not task:
        await query.edit_message_text("Сессия протухла. Нажми ➕ ещё раз.")
        return
    mode = query.data.replace("ststeps_", "")
    task["steps_mode"] = "checklist" if mode == "checklist" else "subtasks"
    _ensure_steps_mode(task)
    context.user_data.pop("editing_single_task_field", None)
    await query.edit_message_text(format_single_task_preview(task), parse_mode="HTML", reply_markup=_confirm_keyboard())


async def handle_stask_column_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    task = pending_single_task.get(user_id)
    if not task:
        await query.edit_message_text("Сессия протухла. Нажми ➕ ещё раз.")
        return
    selected_col = query.data.replace("stcol_", "")
    if selected_col == "default":
        selected_col = find_column_id(["Надо сделать"], task.get("board_id") or None) or ""
    cols = {c["id"]: c.get("title", "") for c in get_columns_by_board(task.get("board_id", ""))}
    if selected_col:
        task["column_id"] = selected_col
        task["column_title"] = cols.get(selected_col, task.get("column_title", ""))
    context.user_data.pop("editing_single_task_field", None)
    await query.edit_message_text(format_single_task_preview(task), parse_mode="HTML", reply_markup=_confirm_keyboard())


async def handle_stask_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    task = pending_single_task.get(user_id)
    if not task:
        await query.edit_message_text("Сессия протухла. Нажми ➕ ещё раз.")
        return
    action = query.data.replace("stedit_", "")
    if action == "menu":
        await query.edit_message_text("Что правим?", reply_markup=_edit_keyboard(task))
        return
    if action == "back":
        await query.edit_message_text(format_single_task_preview(task), parse_mode="HTML", reply_markup=_confirm_keyboard())
        return
    if action == "deadline":
        context.user_data["editing_single_task_field"] = "deadline"
        await query.edit_message_text("Выбери дедлайн:", reply_markup=_deadline_keyboard())
        return
    if action == "priority":
        await query.edit_message_text("Выбери приоритет:", reply_markup=_priority_keyboard())
        return
    if action == "direction":
        await query.edit_message_text("Выбери направление:", reply_markup=_build_direction_keyboard(prefix="sdir_"))
        return
    if action == "steps":
        await query.edit_message_text("Как хранить шаги?", reply_markup=_steps_mode_keyboard())
        return
    if action == "column":
        board_id = task.get("board_id")
        await query.edit_message_text("Выбери колонку:", reply_markup=_columns_keyboard(board_id))
        return
    context.user_data["editing_single_task_field"] = action
    hints = {
        "title": "Введи новый заголовок (до 80 симв.).",
        "description": "Введи новое описание (можно пусто, отправь '-')",
    }
    await query.edit_message_text(hints.get(action, "Введи новое значение текстом."))


async def handle_single_task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение или отмена создания одной задачи."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if query.data == "stask_cancel":
        pending_single_task.pop(user_id, None)
        _clear_task_context(context, user_id)
        await query.edit_message_text("Отбой, не вопрос.")
        return

    task = pending_single_task.pop(user_id, None)
    if not task:
        await query.edit_message_text("Братан, сессия протухла. Давай по новой.")
        return

    await query.edit_message_text("Погнали, создаю...")
    loop = asyncio.get_event_loop()
    column_id = task.get("column_id") or await loop.run_in_executor(
        None, find_column_id, ["Надо сделать"], task.get("board_id") or None
    )
    if not column_id:
        await query.edit_message_text("Блин, колонку 'Надо сделать' не нашёл. Чекни доску.")
        return

    _ensure_steps_mode(task)
    if task.get("steps_mode") == "subtasks":
        task["checklist"] = []
    else:
        task["subtasks"] = []

    ok, data = await loop.run_in_executor(None, create_yougile_task, task, column_id)
    if not ok:
        await query.edit_message_text(f"Ошибка: {esc(data)}")
        return

    tid = data.get("id", "")
    key = data.get("idTaskProject") or data.get("key") or ""
    key_str = f" <code>{esc(key)}</code>" if key else ""

    # Создаём подзадачи или fallback в чеклист
    subtasks = task.get("subtasks") or []
    creation_mode = "чеклист"
    if subtasks and tid:
        sub_stickers = {}
        direction = task.get("direction")
        if direction and direction in DIRECTION_STATES:
            sub_stickers[STICKER_DIRECTION_ID] = DIRECTION_STATES[direction]
        linked_ok, _child_ids = await loop.run_in_executor(
            None, create_subtasks, subtasks, column_id, tid, sub_stickers or None
        )
        if linked_ok:
            creation_mode = "подзадачи"
        else:
            await loop.run_in_executor(None, add_checklist_to_task, tid, subtasks)
            creation_mode = "чеклист (fallback)"
    elif task.get("checklist"):
        creation_mode = "чеклист"

    mirror_ok, mirror_frag = await loop.run_in_executor(None, mirror_task_to_welcome, task)
    if not mirror_ok:
        mirror_frag = esc(mirror_frag) if mirror_frag else ""

    # Формируем итог
    priority = task.get("priority", "Medium")
    p_emoji = PRIORITY_EMOJI.get(priority, "")
    priority_labels = {"High": "Важно", "Medium": "Нормально", "Low": "Не важно"}
    p_label = f"{p_emoji} {priority_labels.get(priority, priority)}"

    dl = task.get("deadline")
    dl_line = f"\nДедлайн: {esc(dl)}" if dl else ""

    direction = task.get("direction")
    dir_line = f"\nНаправление: {esc(direction)}" if direction else ""

    sub_line = f"\nШаги: {esc(creation_mode)}"

    await query.edit_message_text(
        f"Задача залетела! 🚀{key_str}\n<b>{esc(task['title'][:80])}</b>"
        f"\nПриоритет: {p_label}{dl_line}{dir_line}{sub_line}"
        f"\n<a href=\"{task_url(tid)}\">Открыть в YouGile</a>{mirror_frag}",
        parse_mode="HTML", disable_web_page_preview=True,
    )
    _clear_task_context(context, user_id)


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


def _get_filtered_tasks(filter_type: str, board_id: str | None = None, list_diag: str = "") -> str:
    """Фильтрация задач без AI — чисто по данным API."""
    if board_id:
        columns = get_columns_by_board(board_id)
    else:
        columns = get_columns()
    if not columns:
        return "Не удалось получить колонки." + (f" {list_diag}" if list_diag else "")

    today_date = date.today()
    results = []
    active_norm = yc.active_column_normalized_set()

    for col in columns:
        if not yc.column_title_matches(col.get("title", ""), active_norm):
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
    user_id = update.effective_user.id
    board_id, list_diag = resolve_list_board_id_for_user(context, user_id)

    if filter_type == "ai":
        await query.edit_message_text("🤖 AI расставляет приоритеты (до 10 задач)...")
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, partial(ai_prioritizer.run_prioritization, YOUGILE_API_KEY, board_id=board_id),
            )
            await query.edit_message_text(esc(result))
        except Exception as e:
            await query.edit_message_text(f"Ошибка: {esc(e)}")
        return

    await query.edit_message_text("Анализирую...")
    try:
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(
            None, partial(_get_filtered_tasks, filter_type, board_id=board_id, list_diag=list_diag),
        )
        chunks = chunk_telegram_html(text)
        await query.edit_message_text(chunks[0], parse_mode="HTML", disable_web_page_preview=True)
        for part in chunks[1:]:
            await context.bot.send_message(
                chat_id=update.effective_chat.id, text=part,
                parse_mode="HTML", disable_web_page_preview=True,
            )
    except Exception as e:
        await query.edit_message_text(f"Ошибка: {esc(e)}")


async def chat_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_history.pop(update.effective_user.id, None)
    context.user_data.clear()
    await update.message.reply_text("Чат обнулён. Как будто ничего не было 😎", reply_markup=MAIN_MENU)


# --- Голосовое → задача (с полным AI-разбором) ---
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    draft = _task_context(context, update.effective_user.id)
    if not draft.get("board_id") or not draft.get("direction"):
        await update.message.reply_text("Сначала нажми ➕ Новая задача и выбери проект/доску/направление.")
        return
    # Голосовое используется для создания одной задачи только если ожидается задача,
    # либо всегда (удобнее — всегда разбираем как задачу)
    msg = await update.message.reply_text("Секунду, слушаю...")
    voice_path = "voice.ogg"
    try:
        voice_file = await update.message.voice.get_file()
        await voice_file.download_to_drive(voice_path)
        loop = asyncio.get_event_loop()
        task = await loop.run_in_executor(None, parse_single_task_from_audio, voice_path)
        task = _normalize_task(task, fallback_direction=draft.get("direction"))
        task["project_id"] = draft.get("project_id")
        task["project_title"] = draft.get("project_title")
        task["board_id"] = draft.get("board_id")
        task["board_title"] = draft.get("board_title")
        task["column_id"] = find_column_id(["Надо сделать", "Входящие", "Бэклог"], draft.get("board_id"))
        if task["column_id"]:
            cols = {c["id"]: c.get("title", "") for c in get_columns_by_board(draft.get("board_id", ""))}
            task["column_title"] = cols.get(task["column_id"], "")
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
        draft = _task_context(context, update.effective_user.id)
        normalized = []
        for t in tasks:
            tt = _normalize_task(t, fallback_direction=draft.get("direction"))
            tt["project_id"] = draft.get("project_id")
            tt["project_title"] = draft.get("project_title")
            tt["board_id"] = draft.get("board_id")
            tt["board_title"] = draft.get("board_title")
            normalized.append(tt)
        tasks = normalized
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
    draft = _task_context(context, update.effective_user.id)
    if not draft.get("board_id") or not draft.get("direction"):
        await update.message.reply_text("Сначала нажми ➕ Новая задача и выбери проект/доску/направление.")
        return
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
    draft = _task_context(context, update.effective_user.id)
    if not draft.get("board_id") or not draft.get("direction"):
        await update.message.reply_text("Сначала нажми ➕ Новая задача и выбери проект/доску/направление.")
        return
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
        _clear_task_context(context, user_id)
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

    results = []
    for i, task in enumerate(tasks, 1):
        board_id = task.get("board_id")
        column_id = task.get("column_id") or await loop.run_in_executor(
            None, find_column_id, ["Надо сделать", "Входящие", "Бэклог"], board_id or None
        )
        if not column_id:
            results.append(f"{i}. ❌ <b>{esc(task['title'][:55])}</b>\n   Колонка не найдена")
            continue
        _ensure_steps_mode(task)
        if task.get("steps_mode") == "subtasks":
            task["checklist"] = []
        ok, data = await loop.run_in_executor(None, create_yougile_task, task, column_id)
        emoji = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(task.get("priority", "Medium"), "⚪")
        if ok:
            tid = data.get("id", "")
            key = data.get("idTaskProject") or data.get("key") or ""
            key_str = f" <code>{esc(key)}</code>" if key else ""
            mode_line = ""
            if task.get("subtasks"):
                linked_ok, _ = await loop.run_in_executor(None, create_subtasks, task["subtasks"], column_id, tid, None)
                if linked_ok:
                    mode_line = "\n   🧩 Подзадачи"
                else:
                    await loop.run_in_executor(None, add_checklist_to_task, tid, task["subtasks"])
                    mode_line = "\n   ✅ Чеклист (fallback)"
            elif task.get("checklist"):
                mode_line = "\n   ✅ Чеклист"
            w_ok, w_frag = await loop.run_in_executor(None, mirror_task_to_welcome, task)
            w_line = (esc(w_frag) if not w_ok else w_frag) if w_frag else ""
            results.append(
                f"{i}. ✅ {emoji} <b>{esc(task['title'][:55])}</b>{key_str}{mode_line}\n   🔗 <a href=\"{task_url(tid)}\">Открыть</a>{w_line}"
            )
        else:
            results.append(f"{i}. ❌ {emoji} <b>{esc(task['title'][:55])}</b>\n   {esc(str(data)[:80])}")

    ok_count = sum(1 for r in results if "✅" in r)
    summary = f"Создано <b>{ok_count}/{len(tasks)}</b>:\n\n" + "\n\n".join(results)
    await query.edit_message_text(summary, parse_mode="HTML", disable_web_page_preview=True)
    _clear_task_context(context, user_id)


# --- Текстовые сообщения ---
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id

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

    # Ввод пользовательского периода отчёта
    if context.user_data.get("awaiting_report_period_text"):
        parsed = _period_from_text(text)
        if not parsed:
            await update.message.reply_text("Не понял даты. Формат: 2026-04-01 2026-04-06 или 01.04.2026-06.04.2026")
            return
        ts_from, ts_to, label = parsed
        pending_report[user_id] = {"mode": "custom", "ts_from": ts_from, "ts_to": ts_to, "label": label}
        context.user_data["awaiting_report_period_text"] = False
        await update.message.reply_text(
            f"Период: {label}. Теперь выбери направление:",
            reply_markup=_report_direction_keyboard(),
        )
        return

    # Редактирование полей single-task после кнопки "Внести правки"
    edit_field = context.user_data.get("editing_single_task_field")
    if edit_field:
        task = pending_single_task.get(user_id)
        if not task:
            context.user_data.pop("editing_single_task_field", None)
            await update.message.reply_text("Сессия правок протухла. Нажми ➕ ещё раз.")
            return
        if edit_field == "title":
            task["title"] = text[:80]
        elif edit_field == "description":
            task["description"] = "" if text == "-" else text
        elif edit_field == "deadline":
            if text == "-":
                task["deadline"] = None
            else:
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
                    task["deadline"] = text
                else:
                    await update.message.reply_text("Для дедлайна используй YYYY-MM-DD или '-' чтобы убрать.")
                    return
        context.user_data.pop("editing_single_task_field", None)
        await update.message.reply_text(
            format_single_task_preview(task),
            parse_mode="HTML",
            reply_markup=_confirm_keyboard(),
        )
        return

    # Если ожидаем задачу (после нажатия ➕) — разбираем через AI
    if context.user_data.get("awaiting_task"):
        context.user_data.pop("awaiting_task", None)
        msg = await update.message.reply_text("Разбираю, чё написал...")
        try:
            loop = asyncio.get_event_loop()
            draft = _task_context(context, user_id)
            if not draft.get("board_id") or not draft.get("direction"):
                await context.bot.edit_message_text(
                    "Сначала выбери проект/доску/направление через ➕ Новая задача.",
                    chat_id=update.effective_chat.id, message_id=msg.message_id,
                )
                return
            task = await loop.run_in_executor(None, parse_single_task, text)
            task = _normalize_task(task, fallback_direction=draft.get("direction"))
            task["project_id"] = draft.get("project_id")
            task["project_title"] = draft.get("project_title")
            task["board_id"] = draft.get("board_id")
            task["board_title"] = draft.get("board_title")
            task["column_id"] = find_column_id(["Надо сделать", "Входящие", "Бэклог"], draft.get("board_id"))
            if task["column_id"]:
                cols = {c["id"]: c.get("title", "") for c in get_columns_by_board(draft.get("board_id", ""))}
                task["column_title"] = cols.get(task["column_id"], "")
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

    # Callbacks — выбор контекста новой задачи (проект / доска / направление)
    app.add_handler(CallbackQueryHandler(handle_task_project_callback, pattern="^tpr_"))
    app.add_handler(CallbackQueryHandler(handle_task_board_callback, pattern="^tbd_"))
    app.add_handler(CallbackQueryHandler(handle_task_direction_preset_callback, pattern="^tdir_"))

    # Callbacks — создание/редактирование одной задачи
    app.add_handler(CallbackQueryHandler(handle_stask_deadline_callback,   pattern="^sdt_"))
    app.add_handler(CallbackQueryHandler(handle_stask_direction_callback,  pattern="^sdir_"))
    app.add_handler(CallbackQueryHandler(handle_stask_priority_callback,   pattern="^stprio_"))
    app.add_handler(CallbackQueryHandler(handle_stask_steps_callback,      pattern="^ststeps_"))
    app.add_handler(CallbackQueryHandler(handle_stask_column_callback,     pattern="^stcol_"))
    app.add_handler(CallbackQueryHandler(handle_stask_edit_callback,       pattern="^stedit_"))
    app.add_handler(CallbackQueryHandler(handle_single_task_callback,      pattern="^stask_"))
    # Callbacks — отчёты
    app.add_handler(CallbackQueryHandler(handle_report_period_callback,    pattern="^rep_"))
    app.add_handler(CallbackQueryHandler(handle_report_direction_callback, pattern="^rdir_"))
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
