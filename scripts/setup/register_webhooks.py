# -*- coding: utf-8 -*-
"""Register YouGile webhooks for event logging."""
import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

YOUGILE_API_KEY = os.environ.get("YOUGILE_API_KEY", "")
YOUGILE_BASE_URL = "https://yougile.com/api-v2"
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")  # e.g. https://your-domain.com/webhook

EVENTS = [
    "task-created",
    "task-updated",
    "task-moved",
    "task-deleted",
    "task-restored",
    "chat_message-created",
]


def main():
    if not YOUGILE_API_KEY:
        print("[FATAL] YOUGILE_API_KEY not set")
        sys.exit(1)
    if not WEBHOOK_URL:
        print("[FATAL] WEBHOOK_URL not set (e.g. https://your-domain.com/webhook)")
        sys.exit(1)

    headers = {"Authorization": f"Bearer {YOUGILE_API_KEY}", "Content-Type": "application/json"}

    # List existing webhooks
    r = requests.get(f"{YOUGILE_BASE_URL}/webhooks", headers=headers)
    existing = r.json().get("content", []) if r.status_code == 200 else []
    existing_events = {w.get("event") for w in existing if not w.get("deleted")}
    print(f"Existing webhooks: {existing_events}")

    for event in EVENTS:
        if event in existing_events:
            print(f"  [SKIP] {event} already registered")
            continue
        r = requests.post(
            f"{YOUGILE_BASE_URL}/webhooks",
            headers=headers,
            json={"event": event, "url": WEBHOOK_URL},
        )
        if r.status_code in (200, 201):
            print(f"  [OK] {event} -> {WEBHOOK_URL}")
        else:
            print(f"  [ERR] {event}: {r.status_code} {r.text[:200]}")

    print("\nDone.")


if __name__ == "__main__":
    main()
