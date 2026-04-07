# -*- coding: utf-8 -*-
"""Общие настройки YouGile для бота и ai_prioritizer (env + дефолты)."""
import os
from dotenv import load_dotenv

load_dotenv()


def _split_csv(s: str, default: list[str]) -> list[str]:
    if not s or not str(s).strip():
        return list(default)
    return [x.strip() for x in str(s).split(",") if x.strip()]


# Проект и доска по умолчанию (списки задач, отчёты, приоритизация)
DEFAULT_PROJECT = os.environ.get(
    "YOUGILE_DEFAULT_PROJECT",
    os.environ.get("DEFAULT_YOUGILE_PROJECT", "Продуктивность"),
).strip()
DEFAULT_BOARD = os.environ.get(
    "YOUGILE_DEFAULT_BOARD",
    os.environ.get("DEFAULT_YOUGILE_BOARD", "Задачи лог"),
).strip()

# Колонки для «Активные задачи» и фильтров приоритизации (без AI)
_ACTIVE_DEFAULT = ["Надо сделать", "В работе", "На согласовании"]
ACTIVE_COLUMN_TITLES = _split_csv(os.environ.get("YOUGILE_ACTIVE_COLUMNS", ""), _ACTIVE_DEFAULT)

# Колонки для AI-приоритизации (расстановка стикера)
_PRIORITY_AI_DEFAULT = ["Надо сделать", "Бэклог", "Входящие", "В работе"]
PRIORITY_AI_COLUMN_TITLES = _split_csv(
    os.environ.get("YOUGILE_PRIORITY_AI_COLUMNS", ""),
    _PRIORITY_AI_DEFAULT,
)

# GET /task-list: лимит и пагинация (макс. limit 1000 по API)
TASK_LIST_LIMIT = max(1, min(1000, int(os.environ.get("YOUGILE_TASK_LIST_LIMIT", "200"))))
TASK_LIST_MAX_PAGES = max(1, int(os.environ.get("YOUGILE_TASK_LIST_MAX_PAGES", "5")))

# Длинные ответы Telegram (HTML)
TELEGRAM_HTML_MAX = int(os.environ.get("TELEGRAM_HTML_CHUNK", "4000"))

# Вторая компания (Welcome): дублирование созданных задач
YOUGILE_API_KEY_WELCOME = (os.environ.get("YOUGILE_API_KEY_WELCOME") or "").strip()
# Либо UUID доски напрямую:
YOUGILE_WELCOME_BOARD_ID = (os.environ.get("YOUGILE_WELCOME_BOARD_ID") or "").strip()
# Либо точные названия проекта и доски (если BOARD_ID не задан):
YOUGILE_WELCOME_PROJECT = (os.environ.get("YOUGILE_WELCOME_PROJECT") or "Маркетинг").strip()
YOUGILE_WELCOME_BOARD = (os.environ.get("YOUGILE_WELCOME_BOARD") or "Онлайн-маркетинг").strip()
YOUGILE_WELCOME_COLUMN = (os.environ.get("YOUGILE_WELCOME_COLUMN") or "Надо сделать").strip()


def normalize_column_title(title: str) -> str:
    return " ".join((title or "").strip().lower().split())


def normalize_title_for_match(title: str) -> str:
    """Сравнение названий проекта/доски: em/en dash, пробелы вокруг дефиса."""
    t = (title or "").strip().lower()
    for ch in ("\u2014", "\u2013", "\u2212"):
        t = t.replace(ch, "-")
    t = " ".join(t.split())
    return t.replace(" ", "")


def active_column_normalized_set() -> set[str]:
    return {normalize_column_title(x) for x in ACTIVE_COLUMN_TITLES}


def priority_ai_column_normalized_set() -> set[str]:
    return {normalize_column_title(x) for x in PRIORITY_AI_COLUMN_TITLES}


def column_title_matches(title: str, normalized_set: set[str]) -> bool:
    return normalize_column_title(title) in normalized_set
