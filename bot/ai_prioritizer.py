import requests
import os
import json
import google.generativeai as genai

# Конфигурация
YOUGILE_BASE_URL = "https://yougile.com/api-v2"
YOUGILE_API_KEY = os.environ.get("YOUGILE_API_KEY", "")

# Sticker IDs (from found_priority_sticker.json)
STICKER_PRIORITY_ID = "b0435d49-0237-47f7-88d6-c10de7adbc9d"
PRIORITY_STATES = {
    "High": "8ced62e1d595",   # Важно
    "Medium": "55e6b0a1cb68", # Нормально
    "Low": "414cda413f0a"     # Не важно
}

TARGET_COLUMN_NAMES = ["Надо сделать", "Бэклог", "Входящие"]

def get_gemini_model():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("\n[!] GEMINI_API_KEY не найден в переменных окружения.")
        api_key = input("Пожалуйста, введите ваш Gemini API Key: ").strip()
        if not api_key:
            print("API Key обязателен для работы скрипта.")
            exit(1)
    
    genai.configure(api_key=api_key)
    return genai.GenerativeModel('gemini-2.0-flash')

def analyze_priority(model, title, description):
    """
    Анализирует задачу с помощью Gemini и возвращает Low, Medium или High.
    """
    prompt = f"""
    Ты - опытный менеджер проектов. Оцени важность задачи для команды маркетинга и цифровой трансформации.
    
    Задача: {title}
    Описание: {description}
    
    Критерии:
    - High: Срочно, блокирует других, влияет на прибыль или критические процессы.
    - Medium: Важно, но может подождать пару дней. Стандартная рабочая задача.
    - Low: Идея "на потом", минорное улучшение, не срочно.
    
    Верни ТОЛЬКО одно слово: High, Medium или Low. Не добавляй никаких пояснений.
    """
    
    try:
        response = model.generate_content(prompt)
        priority = response.text.strip()
        
        # Очистка от лишних символов
        for p in ["High", "Medium", "Low"]:
            if p in priority:
                return p
        return "Medium" # Fallback
    except Exception as e:
        print(f"Ошибка Gemini: {e}")
        return None

def run_prioritization(yougile_api_key, gemini_model):
    report = []
    report.append("--- Запуск AI Приоритизации (Gemini) ---")
    
    headers = {
        "Authorization": f"Bearer {yougile_api_key}",
        "Content-Type": "application/json"
    }
    
    report.append("Сканирование проектов...")
    try:
        projects_resp = requests.get(f"{YOUGILE_BASE_URL}/projects", headers=headers, params={"limit": 50, "includeDeleted": "false"})
        if projects_resp.status_code != 200:
            return f"Ошибка получения проектов: {projects_resp.status_code}"
    except Exception as e:
        return f"Ошибка подключения к YouGile: {e}"

    projects = projects_resp.json().get('content', [])
    updated_count = 0
    
    for project in projects:
        boards_resp = requests.get(f"{YOUGILE_BASE_URL}/boards", headers=headers, params={"projectId": project['id'], "limit": 50})
        if boards_resp.status_code != 200: continue
        
        for board in boards_resp.json().get('content', []):
            columns_resp = requests.get(f"{YOUGILE_BASE_URL}/columns", headers=headers, params={"boardId": board['id'], "limit": 50})
            if columns_resp.status_code != 200: continue
            
            for column in columns_resp.json().get('content', []):
                if column['title'] in TARGET_COLUMN_NAMES:
                    tasks_resp = requests.get(f"{YOUGILE_BASE_URL}/tasks", headers=headers, params={"columnId": column['id'], "limit": 50})
                    if tasks_resp.status_code != 200: continue
                    
                    for task in tasks_resp.json().get('content', []):
                        stickers = task.get('stickers', {})
                        if stickers.get(STICKER_PRIORITY_ID):
                            continue
                            
                        report.append(f"    [ANALYZE] {task['title']}")
                        
                        ai_priority = analyze_priority(gemini_model, task.get('title'), task.get('description', ''))
                        
                        if ai_priority and ai_priority in PRIORITY_STATES:
                            state_id = PRIORITY_STATES[ai_priority]
                            
                            update_data = {"stickers": {STICKER_PRIORITY_ID: state_id}}
                            update_url = f"{YOUGILE_BASE_URL}/tasks/{task['id']}"
                            update_resp = requests.put(update_url, headers=headers, json=update_data)
                            
                            if update_resp.status_code == 200:
                                report.append(f"      => Приоритет установлен: {ai_priority}")
                                updated_count += 1
                            else:
                                report.append(f"      => Ошибка обновления: {update_resp.status_code}")
                        else:
                            report.append("      => Не удалось определить приоритет.")
    
    report.append(f"\nГотово! Обновлено задач: {updated_count}")
    return "\n".join(report)

def main():
    model = get_gemini_model()
    result = run_prioritization(YOUGILE_API_KEY, model)
    print(result)

if __name__ == "__main__":
    main()
