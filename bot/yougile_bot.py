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

# Модели OpenRouter — протестированы 2026-03-05
# Бесплатные первыми, дешёвые платные как запас
MODELS_CHAT = [
    "arcee-ai/trinity-large-preview:free",     # 1.3s, бесплатная
    "google/gemma-3-27b-it:free",              # 2.6s, бесплатная
    "google/gemma-3-12b-it:free",              # 5.2s, бесплатная
    "mistralai/mistral-small-creative",        # 0.5s, $0.10/M
    "xiaomi/mimo-v2-flash",                    # 2.9s, $0.09/M
    "liquid/lfm-2-24b-a2b",                   # 3.3s, $0.03/M
]
MODELS_TASK = [
    "arcee-ai/trinity-large-preview:free",
    "google/gemma-3-27b-it:free",
    "mistralai/mistral-small-creative",
    "xiaomi/mimo-v2-flash",
    "liquid/lfm-2-24b-a2b",
]
MODELS_AUDIO = [
    "openai/gpt-audio-mini",                   # $0.60/M, аудио-вход
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
report_period: dict[int, str] = {}  # user_id -> выбранный период для отчёта

# --- Меню ---
BTN_ACTIVE    = "📋 Активные задачи"
BTN_ADD_TASK  = "➕ Новая задача"
BTN_REPORT    = "📊 Отчёт"
BTN_PRIORITIZE = "🎯 Приоритизация"
BTN_RESET     = "🔄 Сброс"

MAIN_MENU = ReplyKeyboardMarkup(
    [[KeyboardButton(BTN_ACTIVE), KeyboardButton(BTN_ADD_TASK)],
     [KeyboardButton(BTN_REPORT), KeyboardButton(BTN_PRIORITIZE)],
     [KeyboardButton(BTN_RESET)]],
    resize_keyboard=True,
    input_field_placeholder="Напиши задачу или выбери действие...",
)

MENU_BUTTONS = {BTN_ACTIVE, BTN_ADD_TASK, BTN_REPORT, BTN_PRIORITIZE, BTN_RESET}

# --- Системный промпт ---
CHAT_SYSTEM_PROMPT = (
    "Ты — Вася, AI-ассистент по задачам. "
    "Отвечай кратко: 1-3 предложения. Без воды и повторов. "
    "Стиль: дружелюбный, по делу. "
    "Если человек описывает задачу — предложи создать её (кнопка ➕). "
    "Если спрашивает статус — скажи нажать 📋. "
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
    messages = [{
        "role": "user",
        "content": [
            {"type": "input_audio", "input_audio": {"data": audio_b64, "format": ext if ext in ("mp3", "wav") else "mp3"}},
            {"type": "text", "text": prompt},
        ],
    }]
    return _ai_call(MODELS_AUDIO, messages)


def ai_summarize(tasks_text: str, question: str) -> str:
    """AI-саммари задач."""
    prompt = (
        f"{question}\n\n"
        f"Данные задач:\n{tasks_text}\n\n"
        "Ответь кратко, структурированно. Без markdown. На русском."
    )
    return _ai_call(MODELS_CHAT, [{"role": "user", "content": prompt}], max_tokens=1000)


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


def get_columns() -> list[dict]:
    _, board_id = _find_project_board()
    if not board_id:
        return []
    r = requests.get(f"{YOUGILE_BASE_URL}/columns", headers=_headers(), params={"boardId": board_id, "limit": 50})
    return r.json().get("content", []) if r.status_code == 200 else []


def get_column_tasks(column_id: str, limit: int = 100) -> list[dict]:
    r = requests.get(f"{YOUGILE_BASE_URL}/task-list", headers=_headers(), params={"columnId": column_id, "limit": limit})
    return r.json().get("content", []) if r.status_code == 200 else []


def get_task_detail(task_id: str) -> dict | None:
    r = requests.get(f"{YOUGILE_BASE_URL}/tasks/{task_id}", headers=_headers())
    return r.json() if r.status_code == 200 else None


def get_task_comments(task_id: str, limit: int = 10) -> list[dict]:
    r = requests.get(f"{YOUGILE_BASE_URL}/chats/{task_id}/messages", headers=_headers(), params={"limit": limit})
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
    priority = task.get("priority", "Medium")
    if priority in PRIORITY_STATES:
        body["stickers"] = {STICKER_PRIORITY_ID: PRIORITY_STATES[priority]}
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
    resp = requests.post(f"{YOUGILE_BASE_URL}/task-list", headers=_headers(), json=body)
    if resp.status_code in (200, 201):
        return True, resp.json()
    return False, f"{resp.status_code}: {resp.text[:300]}"


# --- Функция 1: Активные задачи (требуют действий) ---
def get_active_tasks() -> str:
    """Собирает задачи из активных колонок."""
    columns = get_columns()
    if not columns:
        return "Не удалось получить колонки."

    result_parts = []
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
        for t in active[:10]:
            stickers = t.get("stickers") or {}
            priority = PRIORITY_MAP_INV.get(stickers.get(STICKER_PRIORITY_ID, ""), "")
            p_emoji = PRIORITY_EMOJI.get(priority, "⚪")
            key = t.get("idTaskProject") or t.get("idTaskCommon") or ""
            key_str = f"<code>{esc(key)}</code> " if key else ""

            # Дедлайн
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

            lines.append(f"  {p_emoji} {key_str}<b>{esc(t['title'][:60])}</b>{dl_str}")

        if len(active) > 10:
            lines.append(f"  <i>...и ещё {len(active) - 10}</i>")
        result_parts.append("\n".join(lines))

    if not result_parts:
        return "Нет активных задач. Всё чисто! 💪"

    header = f"📋 <b>Активные задачи</b> — {total} шт.\n"
    return header + "\n".join(result_parts)


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

    # Собираем детали (подзадачи, комменты)
    lines = [f"📊 <b>Отчёт за {days} дн.</b> — {len(completed_tasks)} задач выполнено\n"]

    for t in completed_tasks[:15]:
        key = t.get("idTaskProject") or t.get("idTaskCommon") or ""
        key_str = f"<code>{esc(key)}</code> " if key else ""
        stickers = t.get("stickers") or {}
        priority = PRIORITY_MAP_INV.get(stickers.get(STICKER_PRIORITY_ID, ""), "")
        p_emoji = PRIORITY_EMOJI.get(priority, "⚪")

        lines.append(f"✅ {p_emoji} {key_str}<b>{esc(t['title'][:60])}</b>")

        # Подзадачи
        detail = get_task_detail(t["id"])
        if detail:
            subtask_ids = detail.get("subtasks", [])
            if subtask_ids:
                sub_done = 0
                sub_names = []
                for sid in subtask_ids[:5]:
                    sub = get_task_detail(sid)
                    if sub:
                        status = "✓" if sub.get("completed") else "○"
                        sub_names.append(f"{status} {sub['title'][:40]}")
                        if sub.get("completed"):
                            sub_done += 1
                lines.append(f"  📎 Подзадачи: {sub_done}/{len(subtask_ids)}")
                for sn in sub_names:
                    lines.append(f"    {sn}")

        # Последний коммент
        comments = get_task_comments(t["id"], limit=3)
        if comments:
            last = comments[-1]
            text = last.get("text", "")[:80]
            if text:
                lines.append(f"  💬 {esc(text)}")

        lines.append("")  # пустая строка между задачами

    if len(completed_tasks) > 15:
        lines.append(f"<i>...и ещё {len(completed_tasks) - 15} задач</i>")

    return "\n".join(lines)


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
        "Привет! Я <b>Вася</b> — помощник по задачам YouGile.\n\n"
        "📋 Активные задачи — что требует внимания\n"
        "➕ Новая задача — текстом или голосом\n"
        "📊 Отчёт — что сделано за период\n"
        "🎯 Приоритизация — AI расставит приоритеты\n\n"
        "Также можешь просто написать — поможу разобраться.",
        parse_mode="HTML", reply_markup=MAIN_MENU,
    )


async def handle_active_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Загружаю задачи...", reply_markup=MAIN_MENU)
    try:
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, get_active_tasks)
        await context.bot.edit_message_text(
            text, chat_id=update.effective_chat.id, message_id=msg.message_id,
            parse_mode="HTML", disable_web_page_preview=True,
        )
    except Exception as e:
        await context.bot.edit_message_text(
            f"Ошибка: {esc(e)}", chat_id=update.effective_chat.id, message_id=msg.message_id,
        )


async def handle_report_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает выбор периода для отчёта."""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("3 дня", callback_data="report_3"),
         InlineKeyboardButton("7 дней", callback_data="report_7"),
         InlineKeyboardButton("14 дней", callback_data="report_14")],
        [InlineKeyboardButton("30 дней", callback_data="report_30")],
    ])
    await update.message.reply_text(
        "За какой период показать отчёт?", reply_markup=keyboard,
    )


async def handle_report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    days = int(query.data.replace("report_", ""))
    await query.edit_message_text(f"Собираю отчёт за {days} дн....")
    try:
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, get_completed_report, days)
        await query.edit_message_text(text, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        await query.edit_message_text(f"Ошибка: {esc(e)}")


async def handle_add_task_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Отправь:\n"
        "• <b>Текст</b> — опишу задачу и создам\n"
        "• <b>Голосовое</b> — распознаю и создам\n"
        "• <b>Аудиофайл</b> (.mp3/.m4a/.wav) — транскрипт встречи → задачи",
        parse_mode="HTML", reply_markup=MAIN_MENU,
    )
    context.user_data["awaiting_task"] = True


async def prioritize_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("AI анализирует задачи...", reply_markup=MAIN_MENU)
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, ai_prioritizer.run_prioritization, YOUGILE_API_KEY)
        await context.bot.edit_message_text(
            esc(result), chat_id=update.effective_chat.id, message_id=msg.message_id,
        )
    except Exception as e:
        await context.bot.edit_message_text(
            f"Ошибка: {esc(e)}", chat_id=update.effective_chat.id, message_id=msg.message_id,
        )


async def chat_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_history.pop(update.effective_user.id, None)
    context.user_data.clear()
    await update.message.reply_text("Чат сброшен.", reply_markup=MAIN_MENU)


# --- Голосовое → задача ---
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Распознаю голосовое...")
    voice_path = "voice.ogg"
    try:
        voice_file = await update.message.voice.get_file()
        await voice_file.download_to_drive(voice_path)
        prompt = 'Распознай речь. Верни JSON: {"title": "краткий заголовок задачи", "description": "описание"}'
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(None, ai_audio, voice_path, prompt)
        try:
            task_info = json.loads(_clean_json(raw))
            title = task_info.get("title", "Голосовая задача")
            description = task_info.get("description", "")
        except Exception:
            title, description = "Голосовая задача", raw

        column_id = await loop.run_in_executor(None, find_column_id)
        if not column_id:
            await context.bot.edit_message_text("Колонка не найдена.", chat_id=update.effective_chat.id, message_id=msg.message_id)
            return
        ok, data = await loop.run_in_executor(None, create_yougile_task,
            {"title": title, "description": description, "priority": "Medium"}, column_id)
        if ok:
            task_id = data.get("id", "")
            key = data.get("idTaskProject") or data.get("key") or ""
            key_str = f" <code>{esc(key)}</code>" if key else ""
            await context.bot.edit_message_text(
                f"✅ Задача создана!{key_str}\n<b>{esc(title)}</b>\n🔗 <a href=\"{task_url(task_id)}\">Открыть</a>",
                chat_id=update.effective_chat.id, message_id=msg.message_id,
                parse_mode="HTML", disable_web_page_preview=True,
            )
        else:
            await context.bot.edit_message_text(f"Ошибка: {esc(data)}", chat_id=update.effective_chat.id, message_id=msg.message_id)
    except Exception as e:
        await context.bot.edit_message_text(f"Ошибка: {esc(e)}", chat_id=update.effective_chat.id, message_id=msg.message_id)
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
        await query.edit_message_text("Отменено.")
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
    if text == BTN_RESET:
        await chat_reset(update, context)
        return

    if not text:
        return

    # Если ожидаем задачу (после нажатия ➕) — обрабатываем как задачу
    if context.user_data.get("awaiting_task"):
        context.user_data.pop("awaiting_task", None)
        # Короткий текст → простая задача, длинный → транскрипт
        if len(text) < 200:
            msg = await update.message.reply_text("Создаю задачу...")
            try:
                loop = asyncio.get_event_loop()
                column_id = await loop.run_in_executor(None, find_column_id)
                if not column_id:
                    await context.bot.edit_message_text("Колонка не найдена.", chat_id=update.effective_chat.id, message_id=msg.message_id)
                    return
                ok, data = await loop.run_in_executor(None, create_yougile_task,
                    {"title": text[:80], "description": "", "priority": "Medium"}, column_id)
                if ok:
                    tid = data.get("id", "")
                    key = data.get("idTaskProject") or data.get("key") or ""
                    key_str = f" <code>{esc(key)}</code>" if key else ""
                    await context.bot.edit_message_text(
                        f"✅ Создана!{key_str}\n<b>{esc(text[:80])}</b>\n🔗 <a href=\"{task_url(tid)}\">Открыть</a>",
                        chat_id=update.effective_chat.id, message_id=msg.message_id,
                        parse_mode="HTML", disable_web_page_preview=True,
                    )
                else:
                    await context.bot.edit_message_text(f"Ошибка: {esc(data)}", chat_id=update.effective_chat.id, message_id=msg.message_id)
            except Exception as e:
                await context.bot.edit_message_text(f"Ошибка: {esc(e)}", chat_id=update.effective_chat.id, message_id=msg.message_id)
        else:
            await _process_transcript(update, context, text)
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

    # Callbacks
    app.add_handler(CallbackQueryHandler(handle_confirmation, pattern="^meeting_"))
    app.add_handler(CallbackQueryHandler(handle_report_callback, pattern="^report_"))

    # Текст — последним
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Пацанский бот запущен")
    app.run_polling()
