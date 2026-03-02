---
name: create-tasks
description: Массовое создание задач в YouGile из структурированного описания
argument-hint: [описание задач или путь к файлу]
disable-model-invocation: true
allowed-tools: Bash(python *), Bash(curl *), Read
---

# Создание задач в YouGile

Создай задачи в YouGile по описанию пользователя.

## YouGile API Reference

Базовый URL: `https://yougile.com/api-v2`
Авторизация: `Authorization: Bearer {key}`

### Создание задачи
```
POST /tasks
{
  "title": "Название",
  "description": "Описание",
  "columnId": "uuid колонки",
  "deadline": {"deadline": timestamp_ms, "withTime": false},
  "checklists": [{"title": "Чеклист", "items": [{"title": "Пункт", "isCompleted": false}]}],
  "assigned": ["user-id-1"],
  "stickers": {"sticker-id": "state-id"},
  "color": "task-primary"
}
```

### Ключевые ID
- Стикер "Приоритет": `b0435d49-0237-47f7-88d6-c10de7adbc9d`
  - High: `8ced62e1d595`, Medium: `55e6b0a1cb68`, Low: `414cda413f0a`
- Стикер "Направление": `54176f3d-77ff-4eb9-a70c-70caa96910e3`
  - Альпина: `8d4f534aec91`, Welcome: `2a1cba107dfd`

### Поиск колонки
```
GET /columns?boardId={board_id}&limit=100
```

## Процесс
1. Разбери описание задач от пользователя
2. Определи целевую колонку (по умолчанию "Надо сделать")
3. Для каждой задачи — POST /tasks
4. Покажи результат: ID, название, ссылка

## Важно
- Всегда запрашивай подтверждение перед созданием
- Показывай превью всех задач перед отправкой в API
- API ключ читай из `.env` (YOUGILE_API_KEY) или `data/yougile_key.txt`
