import requests
import os

API_KEY = os.environ.get("YOUGILE_API_KEY", "") 

# Базовый URL
BASE_URL = "https://yougile.com/api-v2"

def create_simple_task():
    print("--- Создание задачи в YouGile ---")
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    # Шаг 1: Находим первый проект и первую колонку в нем
    print("Ищем куда добавить задачу...")
    projects_resp = requests.get(f"{BASE_URL}/projects", headers=headers, params={"limit": 1})
    
    if projects_resp.status_code != 200:
        print(f"Ошибка доступа к проектам: {projects_resp.status_code}")
        return

    projects = projects_resp.json().get('content', [])
    if not projects:
        print("Нет проектов для добавления задачи.")
        return

    project_id = projects[0]['id']
    project_title = projects[0]['title']
    print(f"Нашли проект: {project_title}")

    # Ищем доски в проекте
    # Чтобы найти колонку, сначала нужно найти доску
    boards_resp = requests.get(f"{BASE_URL}/boards", headers=headers, params={"projectId": project_id, "limit": 1})
    boards = boards_resp.json().get('content', [])
    
    if not boards:
        print(f"В проекте '{project_title}' нет досок.")
        return

    board_id = boards[0]['id']
    board_title = boards[0]['title']
    print(f"Нашли доску: {board_title}")

    # Ищем колонки на доске
    columns_resp = requests.get(f"{BASE_URL}/columns", headers=headers, params={"boardId": board_id, "limit": 1})
    columns = columns_resp.json().get('content', [])

    if not columns:
        print(f"На доске '{board_title}' нет колонок.")
        return

    column_id = columns[0]['id']
    column_title = columns[0]['title']
    print(f"Нашли колонку: {column_title}")

    # Шаг 2: Создаем задачу в найденной колонке
    task_title = input("Введите название задачи: ").strip()
    if not task_title:
        task_title = "Тестовая задача через API"

    print(f"Создаем задачу '{task_title}' в колонке '{column_title}'...")

    task_data = {
        "title": task_title,
        "columnId": column_id,
        "description": "Эта задача создана автоматически через Python скрипт."
    }

    create_resp = requests.post(f"{BASE_URL}/tasks", headers=headers, json=task_data)

    if create_resp.status_code == 201:
        new_task = create_resp.json()
        print(f"Успех! Задача создана. ID: {new_task.get('id')}")
    else:
        print(f"Ошибка создания задачи: {create_resp.status_code}")
        print(create_resp.text)

if __name__ == "__main__":
    if API_KEY == "ВАШ_КЛЮЧ_СЮДА":
        print("ОШИБКА: Вы не вставили API ключ в скрипт!")
        print("Отредактируйте файл и вставьте ключ в переменную API_KEY на 4-й строке.")
    else:
        create_simple_task()
