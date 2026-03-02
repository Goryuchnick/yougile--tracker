import requests
import os
import time
import logging
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

YOUGILE_BASE_URL   = "https://yougile.com/api-v2"
YOUGILE_API_KEY    = os.environ.get("YOUGILE_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

FREE_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-235b-a22b:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "google/gemma-3-27b-it:free",
]

STICKER_PRIORITY_ID = "b0435d49-0237-47f7-88d6-c10de7adbc9d"
PRIORITY_STATES = {
    "High":   "8ced62e1d595",
    "Medium": "55e6b0a1cb68",
    "Low":    "414cda413f0a",
}

TARGET_PROJECT      = "Продуктивность"
TARGET_BOARD        = "Задачи лог"
TARGET_COLUMN_NAMES = ["Надо сделать", "Бэклог", "Входящие"]


def get_client() -> OpenAI:
    if not OPENROUTER_API_KEY:
        print("[FATAL] OPENROUTER_API_KEY не найден в переменных окружения.")
        exit(1)
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)


def analyze_priority(title: str, description: str) -> str | None:
    prompt = (
        f"Ты — опытный менеджер проектов. Оцени важность задачи для команды маркетинга и цифровой трансформации.\n\n"
        f"Задача: {title}\nОписание: {description}\n\n"
        "Критерии:\n"
        "- High: Срочно, блокирует других, влияет на прибыль или критические процессы.\n"
        "- Medium: Важно, но может подождать пару дней. Стандартная рабочая задача.\n"
        "- Low: Идея «на потом», минорное улучшение, не срочно.\n\n"
        "Верни ТОЛЬКО одно слово: High, Medium или Low. Без пояснений."
    )
    client = get_client()
    for model in FREE_MODELS:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=5,
                temperature=0,
            )
            priority = response.choices[0].message.content.strip()
            for p in ["High", "Medium", "Low"]:
                if p in priority:
                    return p
            return "Medium"
        except Exception as e:
            if "429" in str(e):
                logging.warning(f"429 на {model}, пробую следующую")
                time.sleep(2)
                continue
            else:
                print(f"Ошибка {model}: {e}")
                continue
    print("Все модели недоступны")
    return None


def run_prioritization(yougile_api_key: str, client=None, model: str = None) -> str:
    report = ["--- Запуск AI Приоритизации (OpenRouter) ---"]
    headers = {
        "Authorization": f"Bearer {yougile_api_key}",
        "Content-Type": "application/json",
    }

    report.append(f"Ищу проект «{TARGET_PROJECT}», доска «{TARGET_BOARD}»...")
    try:
        projects_resp = requests.get(
            f"{YOUGILE_BASE_URL}/projects", headers=headers,
            params={"limit": 50, "includeDeleted": "false"},
        )
        if projects_resp.status_code != 200:
            return f"Ошибка получения проектов: {projects_resp.status_code}"
    except Exception as e:
        return f"Ошибка подключения к YouGile: {e}"

    project_id = None
    for p in projects_resp.json().get("content", []):
        if p.get("title") == TARGET_PROJECT:
            project_id = p["id"]
            break
    if not project_id:
        return f"Проект «{TARGET_PROJECT}» не найден."

    boards_resp = requests.get(
        f"{YOUGILE_BASE_URL}/boards", headers=headers,
        params={"projectId": project_id, "limit": 50},
    )
    if boards_resp.status_code != 200:
        return f"Ошибка получения досок: {boards_resp.status_code}"

    board_id = None
    for b in boards_resp.json().get("content", []):
        if b.get("title") == TARGET_BOARD:
            board_id = b["id"]
            break
    if not board_id:
        return f"Доска «{TARGET_BOARD}» не найдена в проекте «{TARGET_PROJECT}»."

    columns_resp = requests.get(
        f"{YOUGILE_BASE_URL}/columns", headers=headers,
        params={"boardId": board_id, "limit": 50},
    )
    if columns_resp.status_code != 200:
        return f"Ошибка получения колонок: {columns_resp.status_code}"

    updated_count = 0
    for column in columns_resp.json().get("content", []):
        if column["title"] not in TARGET_COLUMN_NAMES:
            continue
        tasks_resp = requests.get(
            f"{YOUGILE_BASE_URL}/task-list", headers=headers,
            params={"columnId": column["id"], "limit": 50},
        )
        if tasks_resp.status_code != 200:
            continue
        for task in tasks_resp.json().get("content", []):
            if task.get("stickers", {}).get(STICKER_PRIORITY_ID):
                continue
            report.append(f"    [ANALYZE] {task['title']}")
            time.sleep(5)
            ai_priority = analyze_priority(
                task.get("title"), task.get("description", "")
            )
            if ai_priority and ai_priority in PRIORITY_STATES:
                state_id = PRIORITY_STATES[ai_priority]
                update_resp = requests.put(
                    f"{YOUGILE_BASE_URL}/tasks/{task['id']}",
                    headers=headers,
                    json={"stickers": {STICKER_PRIORITY_ID: state_id}},
                )
                if update_resp.status_code == 200:
                    report.append(f"      => Приоритет: {ai_priority}")
                    updated_count += 1
                else:
                    report.append(f"      => Ошибка: {update_resp.status_code}")
            else:
                report.append("      => Не удалось определить приоритет.")

    report.append(f"\nГотово! Обновлено задач: {updated_count}")
    return "\n".join(report)


def main():
    result = run_prioritization(YOUGILE_API_KEY)
    print(result)


if __name__ == "__main__":
    main()
