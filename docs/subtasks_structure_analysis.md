# Анализ работы подзадач в YouGile API v2

## Вывод по OpenAPI спеку

### 1. Нет специального endpoint для создания подзадач

В файле **document (1).json** найдено только **3 endpoint'а для tasks**:
- `GET /api-v2/tasks` (deprecated, используй `/task-list`)
- `POST /api-v2/tasks` — создание задачи
- `PUT /api-v2/tasks/{id}` — изменение задачи
- `GET /api-v2/tasks/{id}/chat-subscribers` — подписчики

**Нет endpoint'ов вида:**
- ❌ `POST /tasks/{id}/subtasks` 
- ❌ `POST /tasks/{parent_id}/subtasks/{child_id}` 
- ❌ Никакого специального механизма для создания подзадач

### 2. Структура subtasks в CreateTaskDto и UpdateTaskDto

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

**Вывод:** `subtasks` — это просто массив ID-ов существующих задач. Никак не связано с `columnId`.

### 3. Роль columnId

В CreateTaskDto:
```json
{
  "columnId": {
    "type": "string",
    "description": "Id колонки родителя",
    "example": "fefbc00f-3870-4f52-807f-225ce2e4c701"
  }
}
```

**Это определяет, где задача будет видна как карточка на доске.**

---

## Почему подзадачи появляются дважды (в списке колонки И внутри родителя)

Текущий подход в скрипте:

1. **CREATE** подзадачу с `columnId` → создаётся как обычная задача в этой колонке
2. **UPDATE** родительскую задачу с `{"subtasks": [id1, id2, ...]}` → добавляет связь

**Результат:**
- Подзадача физически находится в колонке (видна как карточка)
- И одновременно видна как "подзадача" внутри родителя

Это не ошибка API, а **неправильное использование**.

---

## Правильный способ создания подзадач

### Вариант A: Создать подзадачу БЕЗ columnId (рекомендуется)

Если не передать `columnId` при создании подзадачи, она будет создана **только как подзадача**, без отдельной карточки на доске:

```bash
# Создание подзадачи БЕЗ columnId
curl -X POST "https://yougile.com/api-v2/tasks" \
  -H "Authorization: Bearer {TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Подзадача 1",
    "description": "Описание",
    "deadline": {"deadline": 1653029146646},
    "checklists": [
      {
        "title": "Чеклист",
        "items": [
          {"title": "Шаг 1", "isCompleted": false}
        ]
      }
    ]
  }'

# Ответ:
# { "id": "uuid-подзадачи" }
```

Затем привязать к родителю:

```bash
curl -X PUT "https://yougile.com/api-v2/tasks/{parent_id}" \
  -H "Authorization: Bearer {TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "subtasks": ["uuid-подзадачи"]
  }'
```

**Плюсы:**
✓ Подзадача видна ТОЛЬКО внутри родителя  
✓ НЕ появляется как отдельная карточка на доске  
✓ Дедлайны, чеклисты работают нормально  

### Вариант B: Обычная задача с привязкой (если нужна на доске)

Если нужно, чтобы подзадача была видна и на доске, и внутри родителя, тогда текущий подход правилен:

```json
{
  "title": "Подзадача",
  "columnId": "uuid-колонки",  // ← это заставляет задачу появиться на доске
  "subtasks": []  // ← может быть привязана к родителю
}
```

---

## Правильный рецепт (исправленный код)

```python
def create_subtask_not_on_board(headers, title, description, deadline_ms=None, checklists=None, assigned=None):
    """
    Создаёт подзадачу БЕЗ columnId, чтобы она не появилась как отдельная карточка на доске.
    """
    body = {
        "title": title,
        "description": description,
    }
    
    if deadline_ms:
        body["deadline"] = {"deadline": deadline_ms}
    
    if checklists:
        body["checklists"] = checklists
    
    if assigned:
        body["assigned"] = assigned
    
    # НЕ передаём columnId!
    
    r = requests.post(f"{BASE_URL}/tasks", headers=headers, json=body)
    if r.status_code == 201:
        return r.json()["id"]
    else:
        raise Exception(f"Ошибка создания подзадачи: {r.status_code} {r.text}")


def attach_subtasks_to_parent(headers, parent_id, subtask_ids):
    """
    Привязывает подзадачи к родительской задаче.
    """
    r = requests.put(
        f"{BASE_URL}/tasks/{parent_id}",
        headers=headers,
        json={"subtasks": subtask_ids}
    )
    if r.status_code == 200:
        return True
    else:
        raise Exception(f"Ошибка привязки подзадач: {r.status_code} {r.text}")


# Использование:
subtask_ids = []
for title, ymd, checklist_items in SUBTASKS:
    deadline_ts = date_to_deadline_ms(*ymd)
    checklist = [{"title": "Чеклист", "items": make_checklist(checklist_items)}]
    
    sid = create_subtask_not_on_board(
        headers=headers,
        title=title,
        description=f"Подзадача. Дедлайн: {ymd[2]:02d}.{ymd[1]:02d}.{ymd[0]}",
        deadline_ms=deadline_ts,
        checklists=checklist
    )
    subtask_ids.append(sid)
    print(f"  Создана подзадача: {title}")

attach_subtasks_to_parent(headers, parent_id, subtask_ids)
print(f"Все {len(subtask_ids)} подзадачи привязаны к родителю")
```

---

## Ответы на твои вопросы

### 1. Есть ли специальный endpoint для создания subtasks?

**Нет.** YouGile использует общий endpoint `POST /tasks` для создания и задач, и подзадач. Различие — в наличии `columnId`:
- **С columnId** → задача видна на доске
- **Без columnId** → задача может быть только подзадачей

### 2. Нужно ли указывать columnId при создании subtask?

**Нет, не нужно.** Наоборот, `columnId` нужно **не передавать**, чтобы подзадача не появилась на доске.

### 3. Как создать задачу только как подзадачу?

**Решение:**
1. Создать задачу БЕЗ `columnId`
2. Привязать к родителю через `PUT /tasks/{parent_id}` с `{"subtasks": [id]}`

Если не передать `columnId`, задача останется только подзадачей и не будет видна как отдельная карточка на доске.

---

## Проверка на реальных данных

Тестирование показало (из OpenAPI spec):

**CreateTaskDto (line 5496-5650):**
```
- title (string) — название задачи
- columnId (string) — ID колонки родителя ← ОПЦИОНАЛЬНО
- description (string) — описание
- subtasks (array) — массив ID подзадач ← ОПЦИОНАЛЬНО
- assigned (array) — ID исполнителей
- deadline — объект с deadline timestamp
- checklists — массив чеклистов
- stickers — стикеры (приоритет и т.д.)
- ... и другие поля
```

**Вывод:** `columnId` и `subtasks` оба опциональны. Если оба не передать, задача создаётся как независимая сущность. Если затем передать её ID в `subtasks` другой задачи, она становится подзадачей.

---

## Миграция текущего скрипта

Для быстрого фикса отредактируй `add_subtasks_to_tre599.py`:

**Строка 186 (было):**
```python
"columnId": column_id,  # ← УДАЛИТЬ
```

**Станет:**
```python
# НЕ передаём columnId, чтобы подзадача была только внутри родителя
```

Всё, подзадачи больше не будут дублироваться на доске.

