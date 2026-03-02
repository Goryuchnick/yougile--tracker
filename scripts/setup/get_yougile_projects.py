import requests
import os

BASE_URL = "https://yougile.com/api-v2"
API_KEY = os.environ.get("YOUGILE_API_KEY", "")

def get_projects(api_key):
    """
    Получает список проектов из YouGile используя API ключ.
    """
    print("\n--- Список проектов ---")
    url = f"{BASE_URL}/projects"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    params = {
        "includeDeleted": "false",
        "limit": 50
    }

    try:
        response = requests.get(url, headers=headers, params=params)
        
        if response.status_code == 200:
            data = response.json()
            if 'content' in data:
                print(f"Всего проектов: {data.get('paging', {}).get('count', len(data['content']))}")
                print("-" * 40)
                for project in data['content']:
                    title = project.get('title', 'Без названия')
                    project_id = project.get('id', 'Нет ID')
                    print(f"ID: {project_id} | Название: {title}")
                print("-" * 40)
            else:
                print("Не удалось найти проекты в ответе.")
        else:
            print(f"Ошибка получения проектов {response.status_code}: {response.text}")
            
    except Exception as e:
        print(f"Ошибка при получении проектов: {e}")

if __name__ == "__main__":
    get_projects(API_KEY)
