# Структура проекта — yougile api

## Дерево файлов
```
bot/                   — Telegram бот и AI приоритизатор
  yougile_bot.py       — Все хендлеры бота
  ai_prioritizer.py    — AI приоритизация задач
  event_log.py         — SQLite + FastAPI + дашборд
  webapp/              — Mini App (Telegram WebApp)
  run_bot.bat          — Запуск бота (Windows)
data/                  — JSON данные, стикеры, API спецификация
docker-compose.yml     — Docker Compose конфигурация
Dockerfile             — Docker-образ для деплоя
docs/                  — Планы и документация фич
requirements.txt       — Python зависимости
scripts/               — Скрипты
  setup/               — API discovery/setup
  tasks/               — Скрипты создания задач
  utils/               — Отчёты, экспорт
```

## Зависимости
- requests >=2.28.0
- python-telegram-bot >=20.0
- openai >=1.0.0
- python-dotenv >=1.0.0
- fastapi >=0.100.0
- uvicorn >=0.20.0

## Точки входа
- `python bot/yougile_bot.py` — запуск Telegram бота
- `python scripts/utils/weekly_report.py` — еженедельный отчёт
- Mini App: `bot/webapp/index.html`
