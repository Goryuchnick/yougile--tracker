import sys
import os
import requests
import json
# Force UTF-8 for output
sys.stdout.reconfigure(encoding='utf-8')

API_KEY = os.environ.get("YOUGILE_API_KEY", "")
BASE_URL = "https://yougile.com/api-v2"

# Project: Продуктивность
PROJECT_ID = "0018a3d5-9ef6-4742-8f53-12af244701ec" 

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

def setup_kb():
    print(f"Checking for 'База Знаний' board in project {PROJECT_ID}...")
    
    # 1. Check boards
    boards_resp = requests.get(f"{BASE_URL}/boards", headers=headers, params={"projectId": PROJECT_ID, "limit": 100})
    if boards_resp.status_code != 200:
        print(f"Error fetching boards: {boards_resp.status_code}")
        return

    boards = boards_resp.json().get('content', [])
    kb_board = None
    
    for board in boards:
        if board['title'].strip() == "База Знаний":
            kb_board = board
            break
    
    if kb_board:
        print(f"Found existing board: {kb_board['title']} ({kb_board['id']})")
    else:
        print("Creating new board 'База Знаний'...")
        create_resp = requests.post(f"{BASE_URL}/boards", headers=headers, json={"title": "База Знаний", "projectId": PROJECT_ID})
        print(f"Create response: {create_resp.status_code} {create_resp.text}")
        if create_resp.status_code == 201:
            kb_board = create_resp.json()
            # Handle potential missing title in response
            if 'title' not in kb_board:
                kb_board['title'] = "База Знаний"
            print(f"Created board: {kb_board.get('title')} ({kb_board.get('id')})")
        else:
            print(f"Error creating board: {create_resp.status_code} {create_resp.text}")
            return

    # 2. Check columns
    print(f"Checking columns in board {kb_board['id']}...")
    columns_resp = requests.get(f"{BASE_URL}/columns", headers=headers, params={"boardId": kb_board['id'], "limit": 100})
    columns = columns_resp.json().get('content', [])
    
    kb_column = None
    target_column_name = "Статьи"
    
    for column in columns:
        if column['title'].strip() == target_column_name:
            kb_column = column
            break
            
    if kb_column:
        print(f"Found existing column: {kb_column['title']} ({kb_column['id']})")
    else:
        print(f"Creating new column '{target_column_name}'...")
        create_col_resp = requests.post(f"{BASE_URL}/columns", headers=headers, json={"title": target_column_name, "boardId": kb_board['id']})
        if create_col_resp.status_code == 201:
            kb_column = create_col_resp.json()
            print(f"Created column: {kb_column['title']} ({kb_column['id']})")
        else:
            print(f"Error creating column: {create_col_resp.status_code} {create_col_resp.text}")
            return

    print("\n--- RESULTS ---")
    print(f"BOARD_ID = '{kb_board['id']}'")
    print(f"COLUMN_ID = '{kb_column['id']}'")

if __name__ == "__main__":
    setup_kb()
