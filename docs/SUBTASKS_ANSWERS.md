# YouGile Subtasks API: Полный анализ и ответы

**Дата анализа:** 2026-03-02  
**Источник:** OpenAPI spec document (1).json (YouGile API v2)

---

## Твои 3 вопроса и ответы

### 1️⃣ Есть ли специальный endpoint для создания subtasks?

**Ответ: НЕТ**

Найдено **всего 3 endpoint'а для работы с tasks:**
- `GET /api-v2/tasks` (deprecated) → используй `/task-list`
- `POST /api-v2/tasks` — создание задачи  
- `PUT /api-v2/tasks/{id}` — обновление задачи
- `GET /api-v2/tasks/{id}/chat-subscribers` — подписчики

**Нет:**
- ❌ `POST /tasks/{id}/subtasks`
- ❌ `POST /tasks/{parent_id}/subtasks`  
- ❌ никакого отдельного endpoint'а для подзадач

**Вывод:** YouGile использует единый endpoint для всех задач. Разница в том, передаёшь ли ты `columnId`.

---

### 2️⃣ Нужно ли указывать columnId при создании subtask?

**Ответ: НЕТ, НЕ ПЕРЕДАВАЙ columnId**

Из OpenAPI spec (CreateTaskDto, lines 5496-5794):

```json
{
  "columnId": {
    "type": "string",
    "description": "Id колонки родителя",
    "required": false
  },
  "subtasks": {
    "type": "array",
    "items": { "type": "string" },
    "description": "Массив Id подзадач",
    "required": false
  }
}
```

**Оба поля ОПЦИОНАЛЬНЫ.**

Если передашь `columnId`:
- Задача появляется как **карточка на доске** ← нежелательно для подзадач

Если не передашь `columnId`:
- Задача создаётся как **независимая сущность** (без доски)
- Затем можешь привязать к родителю через `PUT` с полем `subtasks`

---

### 3️⃣ Как создать задачу ТОЛЬКО как подзадачу (без дубля на доске)?

**Ответ: 2-шаговый процесс**

#### Шаг 1: Создать задачу БЕЗ columnId

```bash
curl -X POST "https://yougile.com/api-v2/tasks" \
  -H "Authorization: Bearer {API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Подзадача 1",
    "description": "Описание подзадачи",
    "deadline": {
      "deadline": 1653029146646,
      "withTime": false
    },
    "checklists": [
      {
        "title": "Чеклист",
        "items": [
          {"title": "Шаг 1", "isCompleted": false},
          {"title": "Шаг 2", "isCompleted": false}
        ]
      }
    ]
  }'
```

**Ответ:**
```json
{
  "id": "0fe1e417-2415-4e76-932a-ca07a25d6c64"
}
```

#### Шаг 2: Привязать к родителю

```bash
curl -X PUT "https://yougile.com/api-v2/tasks/{parent_id}" \
  -H "Authorization: Bearer {API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "subtasks": ["0fe1e417-2415-4e76-932a-ca07a25d6c64"]
  }'
```

**Результат:** Подзадача видна ТОЛЬКО внутри родителя.

---

## Почему текущий скрипт создаёт дубли

Файл: `d:/Programmes projects/yougile api/scripts/tasks/add_subtasks_to_tre599.py`

**Проблемные строки 184-192:**

```python
body = {
    "title": title,
    "columnId": column_id,  # ← ПРОБЛЕМА: это создаёт карточку на доске!
    "description": f"Подзадача {PARENT_TASK_KEY}. Дедлайн: ...",
    "deadline": {"deadline": deadline_ts, "withTime": False},
    "checklists": [...]
}
r = requests.post(f"{BASE_URL}/tasks", headers=headers, json=body)
```

**Что происходит:**
1. Создаётся задача С `columnId` → видна на доске как карточка
2. Затем привязывается к родителю (line 212: `"subtasks": subtask_ids`)
3. **Результат:** видна И на доске, И внутри родителя (два зеркала)

---

## Быстрый фикс

### Вариант A: Минимальные изменения в скрипте

**Удалить строку 186:**
```python
"columnId": column_id,  # ← УДАЛИТЬ
```

**Изменить return в find_parent_task (line 158, 160):**

Было:
```python
return task["id"], task.get("columnId"), task.get("title", "")
```

Станет:
```python
return task["id"], None, task.get("title", "")
```

**Изменить распаковку результата (line 177):**

Было:
```python
parent_id, column_id, parent_title = find_parent_task(headers)
```

Станет:
```python
parent_id, _, parent_title = find_parent_task(headers)
```

### Вариант B: Правильная функция для подзадач

```python
def create_subtask(headers, title, description, deadline_ms=None, checklists=None):
    """Создаёт подзадачу БЕЗ columnId."""
    body = {
        "title": title,
        "description": description,
    }
    if deadline_ms:
        body["deadline"] = {"deadline": deadline_ms, "withTime": False}
    if checklists:
        body["checklists"] = checklists
    # НЕ передаём columnId!
    
    r = requests.post(f"{BASE_URL}/tasks", headers=headers, json=body)
    if r.status_code == 201:
        return r.json()["id"]
    raise Exception(f"Error: {r.status_code} {r.text}")
```

---

## Справка из OpenAPI spec

**Файл:** `d:/Programmes projects/yougile api/data/document (1).json`

### CreateTaskDto (lines 5496-5650)

Основные поля:
| Поле | Тип | Обязательное | Описание |
|------|-----|-------------|----------|
| title | string | Да | Название задачи |
| columnId | string | **Нет** | ID колонки (заставляет задачу быть на доске) |
| description | string | Нет | Описание |
| deadline | object | Нет | Объект {deadline: ms, withTime: bool, startDate: ms} |
| checklists | array | Нет | [{title, items: [{title, isCompleted}]}] |
| **subtasks** | **array** | **Нет** | **Массив ID-ов для привязки к родителю** |
| assigned | array | Нет | ID-ы пользователей |
| stickers | object | Нет | Приоритет и прочие стикеры |

### UpdateTaskDto (lines 5753-5900)

Полностью аналогичен CreateTaskDto. Все поля опциональны.

### Структура subtasks

```json
{
  "subtasks": {
    "type": "array",
    "items": { "type": "string" },
    "description": "Массив Id подзадач",
    "example": [
      "0fe1e417-2415-4e76-932a-ca07a25d6c64",
      "f0118d9e-2888-48e4-a172-116085da4279"
    ]
  }
}
```

**Это просто массив UUID-ов. Никакой спецструктуры.**

---

## Итоговая таблица решений

| Хочешь сделать | Решение | columnId | subtasks |
|--|--|--|--|
| Обычная задача на доске | POST /tasks | Да (обязательно) | - |
| Подзадача внутри родителя | POST /tasks + PUT родитель | Нет (опускай) | На родителе |
| Задача И на доске, И подзадача | POST /tasks + PUT родитель | Да | На родителе |

---

## Документация

Подробные документы созданы:
- `/docs/subtasks_structure_analysis.md` — полный анализ
- `/docs/subtasks_quick_fix.md` — быстрый фикс

Тестируй прямо через curl выше, API работает как описано.
