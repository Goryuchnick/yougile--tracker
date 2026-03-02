# -*- coding: utf-8 -*-
import html
import logging
import os
import requests
import json
import asyncio
import time
from datetime import datetime, date
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
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

# Бесплатные модели OpenRouter (ротация при 429)
FREE_MODELS_CHAT = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "qwen/qwen3-235b-a22b:free",
    "google/gemma-3-27b-it:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
]
FREE_MODELS_TASK = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-235b-a22b:free",
    "deepseek/deepseek-r1:free",
]
FREE_MODELS_AUDIO = [
    "google/gemini-2.5-flash-preview:free",
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

# Ограничение по проекту и доске
TARGET_PROJECT = "Продуктивность"
TARGET_BOARD   = "Задачи лог"

# Кэш project_id / board_id (заполняется при первом обращении)
_project_id: str | None = None
_board_id:   str | None = None

# Кэш списка задач: (timestamp, tasks_list) — живёт 5 минут
_tasks_cache: tuple[float, list[dict]] | None = None
TASKS_CACHE_TTL = 300  # секунд

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- Состояние ---
pending_tasks: dict[int, list[dict]] = {}
chat_history:  dict[int, list[dict]] = {}

# --- Меню ---
BTN_TRANSCRIPT  = "📝 Транскрипт встречи"
BTN_PRIORITIZE  = "🎯 Приоритизация"
BTN_TASKS       = "📊 Задачи YouGile"
BTN_RESET       = "🔄 Сбросить чат"

MAIN_MENU = ReplyKeyboardMarkup(
    [[KeyboardButton(BTN_TRANSCRIPT), KeyboardButton(BTN_PRIORITIZE)],
     [KeyboardButton(BTN_TASKS),      KeyboardButton(BTN_RESET)]],
    resize_keyboard=True,
    input_field_placeholder="Пиши задачу или выбери действие...",
)

MENU_BUTTONS = {BTN_TRANSCRIPT, BTN_PRIORITIZE, BTN_TASKS, BTN_RESET}

# --- Системный промпт Васи ---
CHAT_SYSTEM_PROMPT = (
    "Ты — Вася, умный пацан-ассистент по задачам и проектам 😎. "
    "Общаешься в пацанском стиле: кратко, по делу, без официоза. "
    "Иногда вворачиваешь уместные эмодзи: 🔥 💪 🤙 🫡 👊 😤 — но не переусердствуй. "
    "Можешь использовать сленг, но без мата. "
    "Твоя задача — помочь разобраться с задачами, планами, проблемами. "
    "Задаёшь конкретные наводящие вопросы, чтобы докопаться до сути. "
    "Если чувак не может сформулировать задачу — вытащи её из него вопросами. "
    "Когда понял задачу — предложи создать её в YouGile (пусть нажмёт кнопку 📝). "
    "Если спрашивают про текущие задачи, статус, что делается — скажи нажать 📊. "
    "Работаем только с проектом 'Продуктивность', доска 'Задачи лог'. "
    "Отвечаешь на русском. Не используй markdown-разметку в ответах."
)


# --- Утилиты ---
def esc(text) -> str:
    """Экранирует HTML-спецсимволы в пользовательских данных."""
    return html.escape(str(text))


# --- OpenRouter AI (чат + текст + аудио) с ротацией моделей ---
def _get_openrouter_client() -> OpenAI:
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY не задан.")
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )


def _openrouter_call(models: list, messages: list, max_tokens: int = 4096) -> str:
    """Вызов OpenRouter с ротацией моделей при ошибках."""
    client = _get_openrouter_client()
    last_error = None
    for model in models:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            last_error = e
            err = str(e)
            if "429" in err or "rate" in err.lower():
                logging.warning(f"OpenRouter 429 на {model}, пробую следующую модель")
                time.sleep(2)
                continue
            elif "402" in err:
                logging.warning(f"OpenRouter 402 на {model}, пробую следующую модель")
                continue
            else:
                logging.error(f"OpenRouter ошибка на {model}: {e}")
                continue
    raise Exception(f"Все модели недоступны. Последняя ошибка: {last_error}")


def gemini_generate(prompt: str) -> str:
    """Одиночный запрос для извлечения задач из текста."""
    return _openrouter_call(
        FREE_MODELS_TASK,
        [{"role": "user", "content": prompt}],
    )


def gemini_chat(user_id: int, user_text: str) -> str:
    """Многоходовой чат с Васей через OpenRouter."""
    history = chat_history.get(user_id, [])
    messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})
    reply = _openrouter_call(FREE_MODELS_CHAT, messages, max_tokens=1024)
    history.append({"role": "user",      "content": user_text})
    history.append({"role": "assistant", "content": reply})
    chat_history[user_id] = history[-40:]
    return reply


def gemini_upload_and_generate(file_path: str, prompt: str) -> str:
    """Аудио-транскрипция через OpenRouter (Gemini free preview)."""
    import base64
    with open(file_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode()
    ext = os.path.splitext(file_path)[1].lower().lstrip(".")
    mime_map = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "wav": "audio/wav", "ogg": "audio/ogg", "oga": "audio/ogg"}
    mime = mime_map.get(ext, "audio/mpeg")
    messages = [{
        "role": "user",
        "content": [
            {"type": "input_audio", "input_audio": {"data": audio_b64, "format": ext if ext in ("mp3", "wav") else "mp3"}},
            {"type": "text", "text": prompt},
        ],
    }]
    return _openrouter_call(FREE_MODELS_AUDIO, messages)


def _clean_json(raw: str) -> str:
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


# --- YouGile helpers ---
def _yougile_headers():
    return {"Authorization": f"Bearer {YOUGILE_API_KEY}", "Content-Type": "application/json"}


def _find_project_board() -> tuple[str | None, str | None]:
    """Возвращает (project_id, board_id) для TARGET_PROJECT / TARGET_BOARD. Кэширует результат."""
    global _project_id, _board_id
    if _project_id and _board_id:
        return _project_id, _board_id
    headers = _yougile_headers()
    r = requests.get(f"{YOUGILE_BASE_URL}/projects", headers=headers, params={"limit": 50})
    if r.status_code != 200:
        logger.error("Не удалось получить проекты: %s", r.status_code)
        return None, None
    for p in r.json().get("content", []):
        if p.get("title") == TARGET_PROJECT:
            _project_id = p["id"]
            break
    if not _project_id:
        logger.error("Проект '%s' не найден", TARGET_PROJECT)
        return None, None
    r = requests.get(f"{YOUGILE_BASE_URL}/boards", headers=headers,
                     params={"projectId": _project_id, "limit": 50})
    if r.status_code != 200:
        return None, None
    for b in r.json().get("content", []):
        if b.get("title") == TARGET_BOARD:
            _board_id = b["id"]
            break
    if not _board_id:
        logger.error("Доска '%s' не найдена в проекте '%s'", TARGET_BOARD, TARGET_PROJECT)
        return _project_id, None
    logger.info("Проект '%s' (%s), доска '%s' (%s)", TARGET_PROJECT, _project_id, TARGET_BOARD, _board_id)
    return _project_id, _board_id


def find_column_id(target_columns=None) -> str | None:
    """Ищет колонку только внутри TARGET_PROJECT / TARGET_BOARD."""
    if target_columns is None:
        target_columns = ["Входящие", "Inbox", "Бэклог", "Надо сделать"]
    _, board_id = _find_project_board()
    if not board_id:
        return None
    headers = _yougile_headers()
    cr = requests.get(f"{YOUGILE_BASE_URL}/columns", headers=headers,
                      params={"boardId": board_id, "limit": 50})
    if cr.status_code != 200:
        return None
    cols = cr.json().get("content", [])
    for col in cols:
        if col.get("title") in target_columns:
            return col["id"]
    # Fallback: первая доступная колонка
    return cols[0]["id"] if cols else None


def get_board_columns() -> list[dict]:
    """Возвращает все колонки доски TARGET_BOARD."""
    _, board_id = _find_project_board()
    if not board_id:
        return []
    headers = _yougile_headers()
    cr = requests.get(f"{YOUGILE_BASE_URL}/columns", headers=headers,
                      params={"boardId": board_id, "limit": 50})
    if cr.status_code != 200:
        return []
    return cr.json().get("content", [])


def get_board_tasks(limit: int = 100) -> list[dict]:
    """Возвращает все задачи с доски TARGET_BOARD, добавляет поле _column."""
    columns = get_board_columns()
    if not columns:
        return []
    headers = _yougile_headers()
    tasks: list[dict] = []
    for col in columns:
        tr = requests.get(f"{YOUGILE_BASE_URL}/task-list", headers=headers,
                          params={"columnId": col["id"], "limit": limit})
        if tr.status_code == 200:
            for t in tr.json().get("content", []):
                t["_column"] = col.get("title", "—")
                tasks.append(t)
    return tasks


def format_task_card(task: dict) -> str:
    """Форматирует одну задачу в HTML-карточку."""
    key      = task.get("key", "")
    key_str  = f"<code>{esc(key)}</code> " if key else ""
    stickers = task.get("stickers") or {}
    priority = PRIORITY_MAP_INV.get(stickers.get(STICKER_PRIORITY_ID, ""), "")
    p_emoji  = PRIORITY_EMOJI.get(priority, "⚪")
    column   = esc(task.get("_column", "—"))
    title    = esc(task.get("title", "—"))
    desc     = (task.get("description") or "").strip()
    desc_str = f"\n📝 {esc(desc[:200])}" if desc else ""
    dl_raw   = task.get("deadline")
    if isinstance(dl_raw, dict) and dl_raw.get("deadline"):
        ts       = dl_raw["deadline"] // 1000
        deadline = datetime.fromtimestamp(ts).strftime("%d.%m.%Y")
    else:
        deadline = "—"
    url = task_url(task.get("id", ""))
    return (
        f"{key_str}{p_emoji} <b>{title}</b>\n"
        f"📋 {column} | 📅 {deadline}{desc_str}\n"
        f'🔗 <a href="{url}">Открыть</a>'
    )


def get_tasks_summary() -> str:
    """Возвращает сводку задач по колонкам для BTN_TASKS."""
    tasks = get_board_tasks()
    if not tasks:
        return f"😤 Задач на доске <b>{esc(TARGET_BOARD)}</b> не найдено или подключение не работает."
    by_col: dict[str, list] = {}
    for t in tasks:
        by_col.setdefault(t.get("_column", "—"), []).append(t)
    lines = [f"📊 <b>{esc(TARGET_BOARD)}</b> — всего {len(tasks)} задач\n"]
    for col_name, col_tasks in by_col.items():
        lines.append(f"\n🗂 <b>{esc(col_name)}</b> ({len(col_tasks)}):")
        for t in col_tasks[:6]:
            key      = t.get("key", "")
            key_str  = f"[{esc(key)}] " if key else ""
            stickers = t.get("stickers") or {}
            priority = PRIORITY_MAP_INV.get(stickers.get(STICKER_PRIORITY_ID, ""), "")
            p_emoji  = PRIORITY_EMOJI.get(priority, "⚪")
            lines.append(f"  {p_emoji} {key_str}{esc(t.get('title', '—')[:55])}")
        if len(col_tasks) > 6:
            lines.append(f"  <i>...и ещё {len(col_tasks) - 6}</i>")
    return "\n".join(lines)


def search_tasks(query: str) -> list[dict]:
    """Ищет задачи по вхождению строки в заголовок."""
    q = query.lower()
    return [t for t in get_board_tasks() if q in t.get("title", "").lower()]


def get_yougile_users() -> dict[str, str]:
    r = requests.get(f"{YOUGILE_BASE_URL}/users", headers=_yougile_headers(), params={"limit": 100})
    if r.status_code != 200:
        return {}
    return {(u.get("name") or "").strip().lower(): u["id"]
            for u in r.json().get("content", []) if u.get("name")}


def resolve_assignee(name: str, users: dict[str, str]) -> str | None:
    if not name or name.lower() in ("не назначен", "unknown", ""):
        return None
    nl = name.lower()
    if nl in users:
        return users[nl]
    for key, uid in users.items():
        if nl in key or key in nl:
            return uid
    return None


def task_url(task_id: str) -> str:
    return f"https://yougile.com/task/{task_id}"


def create_yougile_task(task: dict, column_id: str, users: dict[str, str]) -> tuple[bool, dict | str]:
    """Returns (True, task_dict) on success, (False, error_str) on failure."""
    headers = _yougile_headers()
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
    priority = task.get("priority", "Medium")
    if priority in PRIORITY_STATES:
        body["stickers"] = {STICKER_PRIORITY_ID: PRIORITY_STATES[priority]}
    items = task.get("checklist", [])
    if items:
        body["checklists"] = [{"title": "Чеклист",
                                "items": [{"title": t, "isCompleted": False} for t in items]}]
    uid = resolve_assignee(task.get("assignee", ""), users)
    if uid:
        body["assigned"] = [uid]
    resp = requests.post(f"{YOUGILE_BASE_URL}/task-list", headers=headers, json=body)
    logger.info("YouGile create task: status=%s body=%s resp=%s", resp.status_code, body, resp.text[:300])
    if resp.status_code in (200, 201):
        return True, resp.json()
    return False, f"{resp.status_code}: {resp.text[:300]}"


def create_simple_task(title: str, description: str) -> str:
    column_id = find_column_id()
    if not column_id:
        return "Колонка не найдена."
    ok, data = create_yougile_task(
        {"title": title, "description": description, "priority": "Medium"}, column_id, {}
    )
    if ok:
        task_id = data.get("id", "")
        url = task_url(task_id)
        key = data.get("key", "")
        key_str = f" <code>{esc(key)}</code>" if key else ""
        return (
            f"✅ Задача создана!{key_str}\n"
            f"<b>{esc(title)}</b>\n"
            f'🔗 <a href="{url}">Открыть в YouGile</a>'
        )
    return f"Ошибка создания: {esc(data)}"


# --- Извлечение задач из транскрипта ---
def _extraction_prompt(today: str) -> str:
    return (
        f"Ты — опытный проект-менеджер. Проанализируй транскрипт совещания "
        f"и извлеки ВСЕ задачи, поручения и обязательства.\n\n"
        f"Для каждой задачи определи:\n"
        f'- "title" — краткое название (до 80 символов)\n'
        f'- "description" — описание с контекстом из совещания\n'
        f'- "assignee" — кто ответственный (имя или "не назначен")\n'
        f'- "deadline" — дата YYYY-MM-DD если упомянута, иначе null\n'
        f'- "priority" — "High"/"Medium"/"Low"\n'
        f'- "checklist" — список подшагов (массив строк), иначе []\n\n'
        f"Правила:\n"
        f'- Извлекай ТОЛЬКО действия: "я сделаю...", "нужно...", "давайте..."\n'
        f"- Сегодняшняя дата: {today}\n"
        f"- Если дедлайн относительный — вычисли дату\n"
        f"- Верни ТОЛЬКО валидный JSON массив, без markdown-обёрток"
    )


def extract_tasks_from_text(text: str) -> list[dict]:
    today = date.today().strftime("%Y-%m-%d")
    raw = gemini_generate(_extraction_prompt(today) + f"\n\nТранскрипт:\n{text}")
    return json.loads(_clean_json(raw))


def extract_tasks_from_audio_sync(file_path: str) -> list[dict]:
    today = date.today().strftime("%Y-%m-%d")
    prompt = _extraction_prompt(today) + "\n\nПрослушай запись совещания и извлеки задачи."
    raw = gemini_upload_and_generate(file_path, prompt)
    return json.loads(_clean_json(raw))


def format_tasks_preview(tasks: list[dict]) -> str:
    lines = [f"Найдено задач: <b>{len(tasks)}</b>\n"]
    for i, t in enumerate(tasks, 1):
        deadline = esc(t.get("deadline") or "не указан")
        assignee = esc(t.get("assignee") or "не назначен")
        priority = t.get("priority") or "Medium"
        emoji = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(priority, "⚪")
        lines.append(
            f"{i}. {emoji} <b>{esc(t['title'])}</b>\n"
            f"   👤 {assignee} | 📅 {deadline}"
        )
        if t.get("checklist"):
            lines.append(f"   ✅ {len(t['checklist'])} подшагов")
    return "\n".join(lines)


# --- Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Йоу 👊 Я <b>Вася</b> — YouGile Bot на Gemini 2.5.\n\n"
        "💬 Пиши что угодно — разберём задачи, отвечу по делу\n"
        "🎙 Голосовое → одна задача сразу в YouGile\n"
        "🎵 Аудио (.mp3/.m4a/.wav) → транскрипт встречи → задачи\n"
        "📄 Файл .txt → транскрипт → задачи\n\n"
        f"🔥 Работаю с проектом <b>{esc(TARGET_PROJECT)}</b>, доска <b>{esc(TARGET_BOARD)}</b>\n\n"
        "Кнопки внизу — жми 👇",
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )


async def prioritize_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🤖 Gemini анализирует задачи...", reply_markup=MAIN_MENU)
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, ai_prioritizer.run_prioritization, YOUGILE_API_KEY
        )
        await context.bot.edit_message_text(
            esc(result), chat_id=update.effective_chat.id, message_id=msg.message_id
        )
    except Exception as e:
        await context.bot.edit_message_text(
            f"Ошибка: {esc(e)}", chat_id=update.effective_chat.id, message_id=msg.message_id
        )


async def chat_reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_history.pop(update.effective_user.id, None)
    await update.message.reply_text("Базара ноль, начнём с чистого листа. Чё надо?",
                                    reply_markup=MAIN_MENU)


async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает все задачи с доски или ищет по запросу."""
    query_text = " ".join(context.args).strip() if context.args else ""
    msg = await update.message.reply_text("Запрашиваю задачи... 🔄", reply_markup=MAIN_MENU)
    try:
        loop = asyncio.get_event_loop()
        if query_text:
            found = await loop.run_in_executor(None, search_tasks, query_text)
            if not found:
                text = f"😤 По запросу «{esc(query_text)}» ничего не нашёл."
            else:
                cards = [format_task_card(t) for t in found[:8]]
                text  = f"🔍 Найдено: <b>{len(found)}</b>\n\n" + "\n\n".join(cards)
        else:
            text = await loop.run_in_executor(None, get_tasks_summary)
        await context.bot.edit_message_text(
            text, chat_id=update.effective_chat.id,
            message_id=msg.message_id, parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as e:
        await context.bot.edit_message_text(
            f"Ошибка: {esc(e)}", chat_id=update.effective_chat.id, message_id=msg.message_id,
        )


async def sync_kb_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Simulating KB Sync...")
    await asyncio.sleep(2)
    await update.message.reply_text("Synced.", reply_markup=MAIN_MENU)


# --- Голосовое → одна задача ---
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("Слушаю... отправляю в Gemini...")
    voice_path = "voice.ogg"
    try:
        voice_file = await update.message.voice.get_file()
        await voice_file.download_to_drive(voice_path)
        prompt = (
            "Прослушай голосовое сообщение. "
            "Извлеки короткий заголовок ('title') и описание ('description'). "
            'Верни JSON: {"title": "...", "description": "..."}'
        )
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(None, gemini_upload_and_generate, voice_path, prompt)
        try:
            task_info = json.loads(_clean_json(raw))
            title       = task_info.get("title", "Голосовая задача")
            description = task_info.get("description", "")
        except Exception:
            title, description = "Голосовая задача", raw
        result = await loop.run_in_executor(None, create_simple_task, title, description)
        await context.bot.edit_message_text(
            result, chat_id=update.effective_chat.id,
            message_id=status_msg.message_id, parse_mode="HTML",
        )
    except Exception as e:
        await context.bot.edit_message_text(
            f"Ошибка: {esc(e)}", chat_id=update.effective_chat.id,
            message_id=status_msg.message_id,
        )
    finally:
        if os.path.exists(voice_path):
            os.remove(voice_path)


# --- Транскрипт (текст) → задачи → подтверждение ---
async def _process_transcript_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    status_msg = await update.message.reply_text("Анализирую транскрипт с Gemini...")
    try:
        loop  = asyncio.get_event_loop()
        tasks = await loop.run_in_executor(None, extract_tasks_from_text, text)
        if not tasks:
            await context.bot.edit_message_text(
                "Задачи в тексте не найдены.",
                chat_id=update.effective_chat.id, message_id=status_msg.message_id,
            )
            return
        pending_tasks[update.effective_user.id] = tasks
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Создать все задачи", callback_data="meeting_confirm"),
            InlineKeyboardButton("❌ Отмена",             callback_data="meeting_cancel"),
        ]])
        await context.bot.edit_message_text(
            format_tasks_preview(tasks),
            chat_id=update.effective_chat.id, message_id=status_msg.message_id,
            parse_mode="HTML", reply_markup=keyboard,
        )
    except json.JSONDecodeError:
        await context.bot.edit_message_text(
            "Gemini вернул невалидный JSON. Попробуй ещё раз.",
            chat_id=update.effective_chat.id, message_id=status_msg.message_id,
        )
    except Exception as e:
        await context.bot.edit_message_text(
            f"Ошибка: {esc(e)}", chat_id=update.effective_chat.id,
            message_id=status_msg.message_id,
        )


async def meeting_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text(
            "Отправь текст транскрипта после команды:\n"
            "<code>/meeting Иван сделает отчёт к пятнице...</code>\n\n"
            "Или просто кинь .txt файл или аудио (.mp3/.m4a/.wav).",
            parse_mode="HTML", reply_markup=MAIN_MENU,
        )
        return
    await _process_transcript_text(update, context, text)


async def handle_txt_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("Читаю файл транскрипта...")
    txt_path = "transcript.txt"
    try:
        doc_file = await update.message.document.get_file()
        await doc_file.download_to_drive(txt_path)
        with open(txt_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        await context.bot.edit_message_text(
            f"Файл получен ({len(text)} симв.). Анализирую...",
            chat_id=update.effective_chat.id, message_id=status_msg.message_id,
        )
        await _process_transcript_text(update, context, text)
    except Exception as e:
        await context.bot.edit_message_text(
            f"Ошибка чтения файла: {esc(e)}",
            chat_id=update.effective_chat.id, message_id=status_msg.message_id,
        )
    finally:
        if os.path.exists(txt_path):
            os.remove(txt_path)


async def handle_audio_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("Загружаю аудио в Gemini... (может занять минуту)")
    audio_path = "meeting_audio.mp3"
    try:
        if update.message.audio:
            file_obj = await update.message.audio.get_file()
            fname    = update.message.audio.file_name or "audio.mp3"
        else:
            file_obj = await update.message.document.get_file()
            fname    = update.message.document.file_name or "audio.mp3"
        audio_path = f"meeting_audio{os.path.splitext(fname)[1].lower()}"
        await file_obj.download_to_drive(audio_path)
        await context.bot.edit_message_text(
            "Аудио загружено. Gemini транскрибирует и извлекает задачи...",
            chat_id=update.effective_chat.id, message_id=status_msg.message_id,
        )
        loop  = asyncio.get_event_loop()
        tasks = await loop.run_in_executor(None, extract_tasks_from_audio_sync, audio_path)
        if not tasks:
            await context.bot.edit_message_text(
                "Задачи не найдены в аудио.",
                chat_id=update.effective_chat.id, message_id=status_msg.message_id,
            )
            return
        pending_tasks[update.effective_user.id] = tasks
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Создать все задачи", callback_data="meeting_confirm"),
            InlineKeyboardButton("❌ Отмена",             callback_data="meeting_cancel"),
        ]])
        await context.bot.edit_message_text(
            format_tasks_preview(tasks),
            chat_id=update.effective_chat.id, message_id=status_msg.message_id,
            parse_mode="HTML", reply_markup=keyboard,
        )
    except json.JSONDecodeError:
        await context.bot.edit_message_text(
            "Gemini вернул невалидный JSON. Попробуй ещё раз.",
            chat_id=update.effective_chat.id, message_id=status_msg.message_id,
        )
    except Exception as e:
        await context.bot.edit_message_text(
            f"Ошибка: {esc(e)}", chat_id=update.effective_chat.id,
            message_id=status_msg.message_id,
        )
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)


# --- Inline-кнопки подтверждения ---
async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if query.data == "meeting_cancel":
        pending_tasks.pop(user_id, None)
        await query.edit_message_text("Отменено. Задачи не созданы.")
        return

    if query.data != "meeting_confirm":
        return

    tasks = pending_tasks.pop(user_id, None)
    if not tasks:
        await query.edit_message_text("Нет задач для создания.")
        return

    await query.edit_message_text("Создаю задачи в YouGile...")
    loop      = asyncio.get_event_loop()
    column_id = await loop.run_in_executor(None, find_column_id)
    if not column_id:
        await query.edit_message_text("Ошибка: колонка для задач не найдена.")
        return

    users   = await loop.run_in_executor(None, get_yougile_users)
    results = []
    for i, task in enumerate(tasks, 1):
        ok, data = await loop.run_in_executor(None, create_yougile_task, task, column_id, users)
        priority = task.get("priority", "Medium")
        p_emoji  = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(priority, "⚪")
        if ok:
            task_id  = data.get("id", "")
            key      = data.get("key", "")
            key_str  = f" <code>{esc(key)}</code>" if key else ""
            deadline = esc(task.get("deadline") or "не указан")
            assignee = esc(task.get("assignee") or "не назначен")
            results.append(
                f"{i}. ✅ {p_emoji} <b>{esc(task['title'][:55])}</b>{key_str}\n"
                f"   👤 {assignee} | 📅 {deadline}\n"
                f'   🔗 <a href="{task_url(task_id)}">Открыть в YouGile</a>'
            )
        else:
            results.append(
                f"{i}. ❌ {p_emoji} <b>{esc(task['title'][:55])}</b>\n"
                f"   Ошибка: {esc(str(data))}"
            )

    ok_count = sum(1 for r in results if "✅" in r)
    summary  = (
        f"Готово! Создано <b>{ok_count}</b> из <b>{len(tasks)}</b> задач:\n\n"
        + "\n\n".join(results)
    )
    await query.edit_message_text(summary, parse_mode="HTML", disable_web_page_preview=True)


# --- Текстовые сообщения: кнопки меню + чат ---
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # Кнопки меню
    if text == BTN_TRANSCRIPT:
        await update.message.reply_text(
            "Отправь мне:\n"
            "• <b>Аудиофайл</b> (.mp3/.m4a/.wav) — запись встречи\n"
            "• <b>.txt файл</b> — готовый транскрипт\n"
            "• <code>/meeting [текст]</code> — вставь текст напрямую",
            parse_mode="HTML", reply_markup=MAIN_MENU,
        )
        return

    if text == BTN_PRIORITIZE:
        await prioritize_command(update, context)
        return

    if text == BTN_TASKS:
        await tasks_command(update, context)
        return

    if text == BTN_RESET:
        await chat_reset_command(update, context)
        return

    # Обычный чат с Васей
    if not text:
        return
    typing_msg = await update.message.reply_text("...")
    try:
        loop  = asyncio.get_event_loop()
        reply = await loop.run_in_executor(None, gemini_chat, update.effective_user.id, text)
        await context.bot.edit_message_text(
            reply,
            chat_id=update.effective_chat.id,
            message_id=typing_msg.message_id,
        )
    except Exception as e:
        await context.bot.edit_message_text(
            f"Ошибка: {esc(e)}",
            chat_id=update.effective_chat.id,
            message_id=typing_msg.message_id,
        )


# --- Запуск ---
if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN не задан.")
        exit(1)

    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start",      start))
    application.add_handler(CommandHandler("prioritize", prioritize_command))
    application.add_handler(CommandHandler("tasks",      tasks_command))
    application.add_handler(CommandHandler("sync_kb",    sync_kb_command))
    application.add_handler(CommandHandler("meeting",    meeting_command))
    application.add_handler(CommandHandler("reset",      chat_reset_command))

    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.AUDIO, handle_audio_file))
    application.add_handler(
        MessageHandler(
            filters.Document.FileExtension("mp3")
            | filters.Document.FileExtension("m4a")
            | filters.Document.FileExtension("wav")
            | filters.Document.FileExtension("flac")
            | filters.Document.FileExtension("aac"),
            handle_audio_file,
        )
    )
    application.add_handler(MessageHandler(filters.Document.FileExtension("txt"), handle_txt_file))
    application.add_handler(CallbackQueryHandler(handle_confirmation, pattern="^meeting_"))

    # Текст (кнопки меню + чат) — последним
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    print("Пацанский бот запущен")
    application.run_polling()
