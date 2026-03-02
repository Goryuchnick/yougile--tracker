import requests
import os

BASE_URL = "https://yougile.com/api-v2"
API_KEY = os.environ.get("YOUGILE_API_KEY", "")

# IDs стикера и состояния, которые мы нашли
STICKER_ID = "54176f3d-77ff-4eb9-a70c-70caa96910e3" # Стикер "Направление"
STICKER_STATE_ID = "8d4f534aec91"               # Состояние "Альпина"
TARGET_COLUMN_NAME = "Надо сделать"
TASK_TITLE = "тест"

def create_task_with_sticker(api_key):
    print(f"--- Создание задачи '{TASK_TITLE}' в колонке '{TARGET_COLUMN_NAME}' cо стикером 'Альпина' ---")
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # 1. Ищем колонку "Надо сделать"
    print("Ищем нужную колонку...")
    target_column_id = None
    
    # Получаем проекты
    projects_resp = requests.get(f"{BASE_URL}/projects", headers=headers, params={"limit": 50, "includeDeleted": "false"})
    if projects_resp.status_code != 200:
        print(f"Ошибка получения проектов: {projects_resp.status_code}")
        return

    projects = projects_resp.json().get('content', [])
    
    for project in projects:
        project_id = project['id']
        print(f"Проверяем проект: {project.get('title')}...")
        
        # Получаем доски проекта
        boards_resp = requests.get(f"{BASE_URL}/boards", headers=headers, params={"projectId": project_id, "limit": 50})
        if boards_resp.status_code != 200: continue
        
        boards = boards_resp.json().get('content', [])
        
        for board in boards:
            board_id = board['id']
            # Получаем колонки доски
            columns_resp = requests.get(f"{BASE_URL}/columns", headers=headers, params={"boardId": board_id, "limit": 50})
            if columns_resp.status_code != 200: continue
            
            columns = columns_resp.json().get('content', [])
            
            for column in columns:
                if column.get('title', '').lower() == TARGET_COLUMN_NAME.lower():
                    target_column_id = column['id']
                    project_title = project.get('title')
                    board_title = board.get('title')
                    print(f"\nНАЙДЕНА КОЛОНКА!")
                    print(f"Проект: {project_title}")
                    print(f"Доска: {board_title}")
                    print(f"Колонка: {column.get('title')} (ID: {target_column_id})")
                    break
            
            if target_column_id: break
        if target_column_id: break
    
    if not target_column_id:
        print(f"\nОШИБКА: Колонка с названием '{TARGET_COLUMN_NAME}' не найдена ни в одном проекте.")
        return

    # 2. Создаем задачу со стикером
    print(f"\nСоздаем задачу...")
    
    task_data = {
        "title": TASK_TITLE,
        "columnId": target_column_id,
        "description": "Автоматически созданная задача со стикером Альпина",
        "stickers": {
            STICKER_ID: STICKER_STATE_ID
        }
    }
    
    create_resp = requests.post(f"{BASE_URL}/tasks", headers=headers, json=task_data)
    
    if create_resp.status_code == 201:
        new_task = create_resp.json()
        print(f"УСПЕХ! Задача создана.")
        print(f"ID: {new_task.get('id')}")
        print(f"Ссылка (примерная): https://ru.yougile.com/team/{new_task.get('id')}") # Ссылка может отличаться в зависимости от домена
    else:
        print(f"Ошибка создания задачи: {create_resp.status_code}")
        print(create_resp.text)

if __name__ == "__main__":
    create_task_with_sticker(API_KEY)
