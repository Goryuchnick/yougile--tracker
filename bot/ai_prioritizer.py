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

MODELS = [
    "qwen/qwen2.5-7b-instruct",                # компактная для короткой классификации
    "mistralai/mistral-nemo",                  # дешёвый fallback
    "google/gemini-3.1-flash-lite-preview",    # надёжный fallback
]

STICKER_PRIORITY_ID = "b0435d49-0237-47f7-88d6-c10de7adbc9d"
PRIORITY_STATES = {
    "High":   "8ced62e1d595",
    "Medium": "55e6b0a1cb68",
    "Low":    "414cda413f0a",
}

TARGET_PROJECT      = "Продуктивность"
TARGET_BOARD        = "Задачи лог"
TARGET_COLUMN_NAMES = ["Надо сделать", "Бэклог", "Входящие", "В работе"]


def get_client() -> OpenAI:
    if not OPENROUTER_API_KEY:
        print("[FATAL] OPENROUTER_API_KEY не задан.")
        exit(1)
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)


def analyze_priority(title: str, description: str) -> str | None:
    prompt = (
        f"Оцени приоритет задачи. Ответь одним словом: High, Medium или Low.\n\n"
        f"Задача: {title}\nОписание: {description[:300]}\n\n"
        "High = срочно, блокирует других. Medium = стандартная. Low = не срочно."
    )
    client = get_client()
    for model in MODELS:
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=5,
                temperature=0,
                timeout=15,
            )
            content = resp.choices[0].message.content
            if not content:
                logging.warning(f"Пустой ответ от {model}")
                continue
            priority = content.strip()
            for p in ["High", "Medium", "Low"]:
                if p in priority:
                    return p
            return "Medium"
        except Exception as e:
            if "429" in str(e):
                logging.warning(f"429 на {model}, следующая")
                time.sleep(1)
            else:
                logging.error(f"Ошибка {model}: {e}")
            continue
    logging.error("Все модели недоступны")
    return None


def run_prioritization(yougile_api_key: str, client=None, model: str = None) -> str:
    report = ["--- AI Приоритизация ---"]
    headers = {"Authorization": f"Bearer {yougile_api_key}", "Content-Type": "application/json"}

    try:
        r = requests.get(f"{YOUGILE_BASE_URL}/projects", headers=headers, params={"limit": 50})
        if r.status_code != 200:
            return f"Ошибка: {r.status_code}"
    except Exception as e:
        return f"Ошибка подключения: {e}"

    project_id = None
    for p in r.json().get("content", []):
        if p.get("title") == TARGET_PROJECT:
            project_id = p["id"]
            break
    if not project_id:
        return f"Проект «{TARGET_PROJECT}» не найден."

    r = requests.get(f"{YOUGILE_BASE_URL}/boards", headers=headers, params={"projectId": project_id, "limit": 50})
    if r.status_code != 200:
        return f"Ошибка досок: {r.status_code}"

    board_id = None
    for b in r.json().get("content", []):
        if b.get("title") == TARGET_BOARD:
            board_id = b["id"]
            break
    if not board_id:
        return f"Доска «{TARGET_BOARD}» не найдена."

    r = requests.get(f"{YOUGILE_BASE_URL}/columns", headers=headers, params={"boardId": board_id, "limit": 50})
    if r.status_code != 200:
        return f"Ошибка колонок: {r.status_code}"

    updated = 0
    MAX_TASKS = 10  # Лимит задач за один запуск
    processed = 0
    for col in r.json().get("content", []):
        if col["title"] not in TARGET_COLUMN_NAMES:
            continue
        tr = requests.get(f"{YOUGILE_BASE_URL}/task-list", headers=headers, params={"columnId": col["id"], "limit": 50})
        if tr.status_code != 200:
            continue
        for task in tr.json().get("content", []):
            if task.get("stickers", {}).get(STICKER_PRIORITY_ID):
                continue
            if processed >= MAX_TASKS:
                report.append(f"\n⏸ Лимит {MAX_TASKS} задач. Остальные — в следующий раз.")
                break
            report.append(f"  [{task['title'][:50]}]")
            time.sleep(1)
            priority = analyze_priority(task.get("title"), task.get("description", ""))
            if priority and priority in PRIORITY_STATES:
                ur = requests.put(
                    f"{YOUGILE_BASE_URL}/tasks/{task['id']}", headers=headers,
                    json={"stickers": {STICKER_PRIORITY_ID: PRIORITY_STATES[priority]}},
                )
                if ur.status_code == 200:
                    report.append(f"    => {priority}")
                    updated += 1
                else:
                    report.append(f"    => Ошибка: {ur.status_code}")
            else:
                report.append("    => Не определён")
            processed += 1
        if processed >= MAX_TASKS:
            break

    report.append(f"\nОбновлено: {updated}")
    return "\n".join(report)


def main():
    result = run_prioritization(YOUGILE_API_KEY)
    print(result)


if __name__ == "__main__":
    main()
