import requests
import json
import sys
import os

# Force UTF-8 for output
sys.stdout.reconfigure(encoding='utf-8')

API_KEY = os.environ.get("YOUGILE_API_KEY", "")
BASE_URL = "https://yougile.com/api-v2"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

def get_structure():
    print("Fetching projects...")
    projects_resp = requests.get(f"{BASE_URL}/projects", headers=headers, params={"includeDeleted": "false", "limit": 50})
    if projects_resp.status_code != 200:
        print(f"Error fetching projects: {projects_resp.status_code}")
        return

    projects = projects_resp.json().get('content', [])
    
    structure = {}

    for project in projects:
        p_id = project['id']
        p_title = project['title']
        print(f"Project: {p_title} ({p_id})")
        
        structure[p_title] = {"id": p_id, "boards": {}}
        
        boards_resp = requests.get(f"{BASE_URL}/boards", headers=headers, params={"projectId": p_id, "limit": 50})
        boards = boards_resp.json().get('content', [])
        
        for board in boards:
            b_id = board['id']
            b_title = board['title']
            print(f"  Board: {b_title} ({b_id})")
            
            structure[p_title]["boards"][b_title] = {"id": b_id, "columns": {}}
            
            columns_resp = requests.get(f"{BASE_URL}/columns", headers=headers, params={"boardId": b_id, "limit": 50})
            columns = columns_resp.json().get('content', [])
            
            for column in columns:
                c_id = column['id']
                c_title = column['title']
                print(f"    Column: {c_title} ({c_id})")
                structure[p_title]["boards"][b_title]["columns"][c_title] = c_id

    # Save to file
    with open("structure.json", "w", encoding="utf-8") as f:
        json.dump(structure, f, indent=4, ensure_ascii=False)
    print("Structure saved to structure.json")

if __name__ == "__main__":
    get_structure()
