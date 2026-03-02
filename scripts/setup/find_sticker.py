import requests
import json
import sys
import os

# Базовый URL API
BASE_URL = "https://yougile.com/api-v2"
API_KEY = os.environ.get("YOUGILE_API_KEY", "")

def list_stickers(api_key):
    print("--- Поиск стикера 'Приоритет' ---")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    url = f"{BASE_URL}/string-stickers"
    params = {"limit": 1000}
    
    try:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code == 200:
            stickers = response.json().get('content', [])
            found_stickers = []
            
            for sticker in stickers:
                name = sticker.get('name', '')
                
                # Проверяем имя стикера
                if "приоритет" in name.lower() or "priority" in name.lower():
                     print(f"НАЙДЕН СТИКЕР: {name}")
                     found_stickers.append(sticker)
            
            # Сохраняем найденное в файл
            with open("found_priority_sticker.json", "w", encoding="utf-8") as f:
                json.dump(found_stickers, f, indent=4, ensure_ascii=False)
            print("Результат сохранен в found_priority_sticker.json")
            
        else:
            print(f"Ошибка получения стикеров: {response.status_code}")
            print(response.text)

    except Exception as e:
        print(f"Ошибка: {e}")

if __name__ == "__main__":
    # Fix stdout encoding for Windows
    sys.stdout.reconfigure(encoding='utf-8')
    list_stickers(API_KEY)
