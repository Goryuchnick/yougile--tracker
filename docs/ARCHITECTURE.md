# Архитектура проекта

## Обзор

YouGile AI Automation Suite — Telegram-бот для управления задачами в YouGile через текстовые и голосовые команды с AI-обработкой.

## Стек

| Компонент | Технология |
|-----------|-----------|
| Язык | Python 3.12 |
| Telegram | python-telegram-bot >= 20.0 (async, polling) |
| AI | OpenRouter API (OpenAI-совместимый) |
| Трекер | YouGile API v2 |
| Деплой | Docker → Coolify VPS (8GB RAM, 3 cores) |

## Структура файлов

```
bot/
  yougile_bot.py       — Главный файл бота (все хэндлеры, AI, YouGile API)
  ai_prioritizer.py    — Cron-скрипт: AI-приоритизация задач
  knowledge_base_sync.py — Cron-скрипт: синхронизация базы знаний

scripts/
  tasks/               — Скрипты создания задач
  setup/               — Настройка API, поиск ID
  utils/               — Отчёты, экспорт

data/
  structure.json        — Кэш структуры YouGile (проекты/доски/колонки)
  found_priority_sticker.json — ID стикеров приоритета
  document (1).json     — OpenAPI спецификация YouGile

docs/
  CHANGELOG.md          — Лог изменений
  ROADMAP.md            — Планы развития
  ARCHITECTURE.md       — Этот файл
  TODO.md               — Текущие задачи

Dockerfile              — Образ для деплоя
docker-compose.yml      — Bot + Cron контейнеры
```

## Потоки данных

### 1. Чат с AI (Вася)
```
Пользователь → TG сообщение → gemini_chat() → OpenRouter → ответ → TG
```

### 2. Постановка задачи (текст)
```
Пользователь → текст → gemini_generate() → JSON {title, desc, priority}
→ подтверждение кнопками → POST /task-list → YouGile
```

### 3. Постановка задачи (аудио)
```
Пользователь → голосовое/аудио → gemini_upload_and_generate()
→ транскрипт + задачи JSON → подтверждение → POST /task-list → YouGile
```

### 4. Сбор задач из лога
```
GET /columns/{id}/tasks → фильтр (активные, без прогресса)
→ AI-саммари → TG ответ
```

### 5. Отчёт за период
```
GET /columns/{id}/tasks → фильтр (завершённые за период)
→ + подзадачи + комменты → AI-саммари → TG ответ
```

### 6. Приоритизация (cron)
```
ai_prioritizer.py → GET задачи без приоритета
→ OpenRouter → оценка High/Medium/Low → PUT sticker → YouGile
```

## AI-модели (OpenRouter)

Ротация: при 429 переходим к следующей модели.

| Уровень | Модели |
|---------|--------|
| Бесплатные (первые) | stepfun, nemotron, arcee, upstage, liquid |
| Платные (запас) | mistral-small-creative, qwen3.5-flash, glm-4.7-flash |
| Аудио | gpt-audio-mini (платная) |

## Ограничения

- Один проект: "Продуктивность", одна доска: "Задачи лог"
- Polling mode — только один экземпляр бота одновременно
- Бесплатные модели — лимиты 20-50 RPM
- Аудио только через платную модель
