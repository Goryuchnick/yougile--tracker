# Быстрый фикс: Подзадачи в YouGile API v2

## TLDR: Главная проблема и решение

**Проблема:** Подзадачи создаются с `columnId`, поэтому они видны И на доске, И внутри родителя (как дубли).

**Решение:** Не передавайте `columnId` при создании подзадачи.

---

## Что нужно изменить в add_subtasks_to_tre599.py

### Строка 186: Удалить columnId

**Было:**
```python
body = {
    "title": title,
    "columnId": column_id,  # ← УДАЛИТЬ ЭТУ СТРОКУ
    "description": f"Подзадача {PARENT_TASK_KEY}...",
    "deadline": {"deadline": deadline_ts, "withTime": False},
    ...
}
```

**Стало:**
```python
body = {
    "title": title,
    # columnId НЕ ПЕРЕДАЁМ — подзадача будет только внутри родителя
    "description": f"Подзадача {PARENT_TASK_KEY}...",
    "deadline": {"deadline": deadline_ts, "withTime": False},
    ...
}
```

### Строка 177-179: Убрать вывод о columnId

**Было:**
```python
parent_id, column_id, parent_title = find_parent_task(headers)
print(f"Найдена: {parent_title or PARENT_TASK_KEY} (id: {parent_id})")
print(f"Колонка для подзадач: {column_id}\n")
```

**Стало:**
```python
parent_id, _, parent_title = find_parent_task(headers)
print(f"Найдена: {parent_title or PARENT_TASK_KEY} (id: {parent_id})")
print("Создание подзадач БЕЗ columnId (видны только внутри родителя)\n")
```

---

## Почему это работает

| Сценарий | columnId | Результат |
|----------|----------|-----------|
| **С columnId** | Передан | Задача видна на доске И внутри родителя (дубль) |
| **БЕЗ columnId** | Не передан | Задача видна ТОЛЬКО внутри родителя (правильно) |

Для обычной задачи требуется `columnId`. Для подзадачи это необязательно.

---

## Тестирование

Создай подзадачу вручную:

```bash
curl -X POST "https://yougile.com/api-v2/tasks" \
  -H "Authorization: Bearer YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Test Subtask",
    "description": "Тест",
    "deadline": {"deadline": 1743638400000}
  }'
```

Ответ содержит ID. Теперь привяжи к родителю (parent_id):

```bash
curl -X PUT "https://yougile.com/api-v2/tasks/{parent_id}" \
  -H "Authorization: Bearer YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "subtasks": ["uuid-подзадачи"]
  }'
```

Всё. Подзадача видна только внутри родителя.

---

## Резюме из OpenAPI spec

**OpenAPI: document (1).json, lines 5496-5794**

CreateTaskDto содержит оба поля опциональными:
- `columnId` (string, optional) — ID колонки, где видна задача на доске
- `subtasks` (array, optional) — массив ID-ов для привязки к родителю

Логика:
- Если есть `columnId` → задача видна на доске
- Если в `subtasks` другой задачи указан ID → видна как подзадача
- Если и то, и другое → видна в обоих местах

**Для чистых подзадач:** опускай `columnId`.
