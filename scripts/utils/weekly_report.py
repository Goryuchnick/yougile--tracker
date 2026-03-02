"""
Еженедельный отчёт по задачам с тегом "Направление" (Альпина/Welcome/Агентство/Личное).

Метод: сканирует системные события и комментарии в чатах задач за указанный период.
Учитывает реальные перемещения, отметки выполнения и комментарии — не дату создания.

Запуск:
    python weekly_report.py                       # прошлая неделя, Альпина
    python weekly_report.py 2026-02-23            # неделя с 23 фев, Альпина
    python weekly_report.py 2026-02-23 2026-02-28 # произвольный диапазон
    python weekly_report.py --direction Агентство
    python weekly_report.py --all-directions
"""

import os, sys, requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://yougile.com/api-v2"

DIRECTION_STICKER_ID = "54176f3d-77ff-4eb9-a70c-70caa96910e3"
DIRECTION_STATES = {
    "Альпина":   "8d4f534aec91",
    "Welcome":   "2a1cba107dfd",
    "Личное":    "413cd49fb4c4",
    "Агентство": "00db86f5a160",
}

def get_api_key():
    key = os.getenv("YOUGILE_API_KEY")
    if key:
        return key
    key_file = os.path.join(os.path.dirname(__file__), "../../data/yougile_key.txt")
    with open(key_file, encoding="utf-8") as f:
        return f.read().strip()

def make_headers(key):
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

def fetch_all(url, headers, params):
    items, offset = [], 0
    while True:
        r = requests.get(url, headers=headers, params={**params, "offset": offset})
        data = r.json()
        items.extend(data.get("content", []))
        if not data.get("paging", {}).get("next"):
            break
        offset += params.get("limit", 100)
    return items

def fetch_columns(headers):
    r = requests.get(f"{BASE_URL}/columns", headers=headers, params={"limit": 500})
    return {c["id"]: c.get("title", "?") for c in r.json().get("content", [])}

def date_range(start_str=None, end_str=None):
    if start_str:
        start = datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        today = datetime.now(timezone.utc)
        start = (today - timedelta(days=today.weekday() + 7)).replace(
            hour=0, minute=0, second=0, microsecond=0)
    if end_str:
        end = datetime.strptime(end_str, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc)
    else:
        end = start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000), start, end

def get_task_activity(task_id, headers, ts_from, ts_to):
    """Возвращает перемещения, завершения и комментарии задачи за период."""
    r = requests.get(
        f"{BASE_URL}/chats/{task_id}/messages", headers=headers,
        params={"since": ts_from, "includeSystem": "true", "limit": 100}
    )
    if r.status_code != 200:
        return [], [], []
    msgs = [m for m in r.json().get("content", []) if ts_from <= m.get("id", 0) <= ts_to]
    moves    = [m for m in msgs if m.get("properties", {}).get("move")]
    done     = [m for m in msgs if m.get("properties", {}).get("gtd")]
    comments = [m for m in msgs if not m.get("properties", {}).get("fromSystem")]
    return moves, done, comments

def generate_report(direction_name, state_id, ts_from, ts_to, headers, verbose=True):
    tasks = fetch_all(f"{BASE_URL}/task-list", headers, {
        "stickerId": DIRECTION_STICKER_ID, "stickerStateId": state_id, "limit": 100
    })
    columns = fetch_columns(headers)

    if verbose:
        print(f"  Сканирую {len(tasks)} задач [{direction_name}]...", flush=True)

    entries = []
    for i, task in enumerate(tasks):
        moves, done, comments = get_task_activity(task["id"], headers, ts_from, ts_to)
        if not (moves or done or comments):
            continue
        entries.append({"task": task, "moves": moves, "done": done, "comments": comments})
        if verbose and (i + 1) % 50 == 0:
            print(f"    {i+1}/{len(tasks)} ...", flush=True)

    lines = [f"\n## {direction_name} — {len(entries)} задач с активностью"]
    if not entries:
        lines.append("  Активности за период не найдено.")
        return "\n".join(lines)

    # Группируем: закрытые → перемещённые → с комментариями
    closed   = [e for e in entries if e["done"]]
    moved    = [e for e in entries if e["moves"] and not e["done"]]
    commented= [e for e in entries if e["comments"] and not e["moves"] and not e["done"]]

    def fmt_entry(entry):
        t = entry["task"]
        tid = t.get("idTaskProject", t.get("idTaskCommon", "?"))
        col = columns.get(t.get("columnId", ""), "?")
        status = "Выполнена" if t.get("completed") else ("Архив" if t.get("archived") else "В работе")
        block = [f"\n[{tid}] {t['title']}  →  {col} ({status})"]

        for m in entry["done"]:
            ts = datetime.fromtimestamp(m["id"] / 1000).strftime("%d.%m %H:%M")
            block.append(f"  ✅ {ts}  Отмечена выполненной")

        for m in entry["moves"]:
            ts = datetime.fromtimestamp(m["id"] / 1000).strftime("%d.%m %H:%M")
            fr = columns.get(m["properties"].get("from", ""), "?")
            to = columns.get(m["properties"].get("to", ""), "?")
            block.append(f"  🔀 {ts}  {fr} → {to}")

        for m in entry["comments"]:
            ts = datetime.fromtimestamp(m["id"] / 1000).strftime("%d.%m %H:%M")
            text = m.get("text", "")[:150].replace("\n", " ")
            block.append(f"  💬 {ts}  {text}")

        return "\n".join(block)

    if closed:
        lines.append(f"\n### ✅ Выполнены ({len(closed)})")
        for e in closed:
            lines.append(fmt_entry(e))
    if moved:
        lines.append(f"\n### 🔀 Перемещены ({len(moved)})")
        for e in moved:
            lines.append(fmt_entry(e))
    if commented:
        lines.append(f"\n### 💬 Комментарии / активность ({len(commented)})")
        for e in commented:
            lines.append(fmt_entry(e))

    return "\n".join(lines)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Еженедельный отчёт YouGile по направлениям")
    parser.add_argument("start_date", nargs="?", help="Начало периода YYYY-MM-DD")
    parser.add_argument("end_date",   nargs="?", help="Конец периода YYYY-MM-DD (опционально)")
    parser.add_argument("--direction", default="Альпина", choices=list(DIRECTION_STATES.keys()))
    parser.add_argument("--all-directions", action="store_true")
    args = parser.parse_args()

    api_key = get_api_key()
    headers = make_headers(api_key)
    ts_from, ts_to, start_dt, end_dt = date_range(args.start_date, args.end_date)

    report_lines = [
        f"# Еженедельный отчёт YouGile",
        f"Период: {start_dt.strftime('%d.%m.%Y')} — {end_dt.strftime('%d.%m.%Y')}",
        f"Сформирован: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        f"Метод: события в чатах задач (перемещения, выполнения, комментарии)",
    ]

    directions = DIRECTION_STATES if args.all_directions else {args.direction: DIRECTION_STATES[args.direction]}

    for name, state_id in directions.items():
        section = generate_report(name, state_id, ts_from, ts_to, headers)
        report_lines.append(section)

    report = "\n".join(report_lines)
    print("\n" + "=" * 60)
    print(report)

    out_dir = os.path.join(os.path.dirname(__file__), "../../docs/reports")
    os.makedirs(out_dir, exist_ok=True)
    fname = f"report_{start_dt.strftime('%Y-%m-%d')}.md"
    fpath = os.path.join(out_dir, fname)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\nОтчёт сохранён: {fpath}")

if __name__ == "__main__":
    main()
