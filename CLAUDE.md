# YouGile Tracker — AI Automation Suite

## Проект
Автоматизация YouGile: Telegram-бот, AI-приоритизация, еженедельные отчёты, транскрипты созвонов.

## Стек
- Python 3.12, pip, venv
- YouGile API v2: `https://yougile.com/api-v2` + Bearer token
- Gemini 2.0 Flash (бесплатный tier) — транскрипция, саммаризация, задачи
- Telegram Bot API (python-telegram-bot)
- Деплой: Coolify на VPS (8GB RAM, 3 cores)

## Структура
```
bot/             — Telegram-бот, приоритизатор, KB-sync
scripts/tasks/   — Скрипты создания задач
scripts/setup/   — Настройка и обнаружение ресурсов API
scripts/utils/   — Утилиты (отчёты, экспорт)
data/            — JSON-данные, стикеры, структура
docs/            — Документация и планы
```

## Команды
- `pip install -r requirements.txt` — установка зависимостей
- `python bot/yougile_bot.py` — запуск бота
- `python scripts/utils/weekly_report.py` — еженедельный отчёт

## Правила
- Секреты только через `.env` или `os.getenv()`, НИКОГДА в коде
- Язык интерфейса и промптов: русский
- Gemini — основная модель (бесплатная), OpenAI — только если Gemini не справляется
- При работе с YouGile API использовать `/task-list` (не устаревший `/tasks`)

## Навигация
- Скиллы: @.claude/skills/ — `/weekly-report`, `/deploy`, `/transcript`, `/create-tasks`
- Агенты: @.claude/agents/ — yougile-dev, deployer, api-explorer
- План развития: @docs/meeting_transcript_feature.md
- API-документация: @data/document (1).json (OpenAPI spec)
