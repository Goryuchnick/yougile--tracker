import requests
import json
import time
import os

API_KEY = os.environ.get("YOUGILE_API_KEY", "")
BASE_URL = "https://yougile.com/api-v2"
PROJECT_ID = "0018a3d5-9ef6-4742-8f53-12af244701ec" # Productivity

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

def create_test_task():
    print("Creating test task...")
    
    # Find a column to put it in
    boards_resp = requests.get(f"{BASE_URL}/boards", headers=headers, params={"projectId": PROJECT_ID, "limit": 1})
    if boards_resp.status_code != 200:
        print("Error fetching boards")
        return
    board_id = boards_resp.json()['content'][0]['id']
    
    cols_resp = requests.get(f"{BASE_URL}/columns", headers=headers, params={"boardId": board_id, "limit": 1})
    if cols_resp.status_code != 200:
        print("Error fetching columns")
        return
    column_id = cols_resp.json()['content'][0]['id']
    
    # Create task
    task_data = {
        "title": "Test Insight for KB",
        "mobileDescription": "This is a test task with a valuable insight about marketing optimization.",
        "description": "We found that using blue buttons increases conversion by 20%.\nThis is a key finding for future campaigns.",
        "columnId": column_id,
        "completed": True # Mark as completed immediately
    }
    
    resp = requests.post(f"{BASE_URL}/tasks", headers=headers, json=task_data)
    if resp.status_code == 201:
        print(f"Test task created: {resp.json()['id']}")
    else:
        print(f"Error creating task: {resp.status_code} {resp.text}")

if __name__ == "__main__":
    create_test_task()
