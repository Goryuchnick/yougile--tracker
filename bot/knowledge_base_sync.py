import requests
import json
import os
import sys
import datetime
from openai import OpenAI

# Force UTF-8 output
sys.stdout.reconfigure(encoding='utf-8')

# Configuration
YOUGILE_API_KEY = os.environ.get("YOUGILE_API_KEY", "")
YOUGILE_BASE_URL = "https://yougile.com/api-v2"

# Project: Продуктивность
SOURCE_PROJECT_ID = "0018a3d5-9ef6-4742-8f53-12af244701ec"

# Target: Board "База Знаний", Column "Статьи"
TARGET_BOARD_ID = "7db00dfc-6b02-4620-8425-a2925c00fdf2"
TARGET_COLUMN_ID = "0e05cbe8-4cf3-4c99-859d-8eb788623808"

# Scan window (e.g., tasks completed in the last 24 hours)
HOURS_BACK = 24

def get_openai_client():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("[WARN] OPENAI_API_KEY не найден. KB sync пропускает LLM-анализ.")
        return None
    return OpenAI(api_key=api_key)

headers = {
    "Authorization": f"Bearer {YOUGILE_API_KEY}",
    "Content-Type": "application/json"
}

def get_completed_tasks_recurse():
    print(f"Scanning project {SOURCE_PROJECT_ID} for completed tasks...")
    
    # Calculate cutoff time
    cutoff_time = datetime.datetime.now() - datetime.timedelta(hours=HOURS_BACK)
    # YouGile timestamp is usually milliseconds? Or ISO?
    # Tasks usually have 'completedTimestamp' or similar if completed.
    # We will fetch all and filter in python for simplicity, or iterate "Done" columns.
    
    completed_tasks = []

    # 1. Get Boards
    boards_resp = requests.get(f"{YOUGILE_BASE_URL}/boards", headers=headers, params={"projectId": SOURCE_PROJECT_ID, "limit": 50})
    if boards_resp.status_code != 200:
        print(f"Error fetching boards: {boards_resp.status_code}")
        return []
        
    for board in boards_resp.json().get('content', []):
        if board['id'] == TARGET_BOARD_ID:
            continue # Skip the KB board itself
            
        print(f"  Scanning Board: {board['title']}")
        
        # 2. Get Columns
        cols_resp = requests.get(f"{YOUGILE_BASE_URL}/columns", headers=headers, params={"boardId": board['id'], "limit": 50})
        if cols_resp.status_code != 200: continue
        
        for col in cols_resp.json().get('content', []):
            # Optimization: Only scan columns that sound like "Done" or "Archive"? 
            # Or scan all because tasks can be marked completed anywhere.
            # print(f"    Column: {col['title']}")
            
            # 3. Get Tasks
            # Filter by completed=true
            params = {
                "columnId": col['id'],
                "limit": 50,
                "completed": "true" 
                # API might not support 'completed' filter directly on GET /tasks.
                # Documentation says GET /tasks supports: columnId, limit, offset, etc.
                # It does NOT list 'completed'. We must fetch and filter.
            }
            tasks_resp = requests.get(f"{YOUGILE_BASE_URL}/tasks", headers=headers, params=params)
            if tasks_resp.status_code != 200: continue
            
            for task in tasks_resp.json().get('content', []):
                # Check if completed
                if task.get('completed') is True:
                     # Check timestamp (updatedAt or completedTimestamp?)
                     # Let's assume we want recently updated
                     timestamp_ms = task.get('timestamp', 0)
                     task_time = datetime.datetime.fromtimestamp(timestamp_ms / 1000.0)
                     
                     if task_time > cutoff_time:
                         completed_tasks.append(task)

    return completed_tasks

def analyze_and_extract(client, task):
    if not client: return None
    
    print(f"    Analyzing: {task['title']}")
    
    prompt = f"""
    Analyze the following completed task from a project management system.
    Determine if it contains valuable knowledge, a reusable solution, or an important insight that should be saved to a Knowledge Base.
    
    Task Title: {task['title']}
    Description: {task.get('description', '')}
    
    If it is NOT useful (e.g. routine task, meeting, simple fix without explanation), return "NO".
    
    If it IS useful, return a Markdown formatted article with:
    - Title: (A clear, descriptive title)
    - Summary: (What was the problem and solution)
    - Key Takeaways: (Bullet points)
    
    Start the response with "YES" if useful, followed by the content.
    """
    
    try:
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a Knowledge Manager."},
                {"role": "user", "content": prompt}
            ]
        )
        content = completion.choices[0].message.content.strip()
        
        if content.startswith("NO") or "NO" == content:
            return None
            
        # Strip "YES" and valid markdown
        if content.startswith("YES"):
            content = content[3:].strip()
            
        return content
        
    except Exception as e:
        print(f"    LLM Error: {e}")
        return None

def create_kb_article(title, content):
    print(f"    -> Creating KB Article: {title}")
    
    data = {
        "title": title,
        "columnId": TARGET_COLUMN_ID,
        "description": content
    }
    
    resp = requests.post(f"{YOUGILE_BASE_URL}/tasks", headers=headers, json=data)
    if resp.status_code == 201:
        print("      Success!")
    else:
        print(f"      Error: {resp.status_code} {resp.text}")

def main():
    print("--- Knowledge Base Sync Started ---")
    
    client = get_openai_client()
    if not client:
        print("Skipping LLM analysis due to missing key.")
        return

    tasks = get_completed_tasks_recurse()
    print(f"Found {len(tasks)} recently completed tasks.")
    
    for task in tasks:
        # Optimization: Fetch comments if needed? 
        # For now, analyzing title + description.
        
        article_content = analyze_and_extract(client, task)
        
        if article_content:
            # Extract title from markdown or use task title
            lines = article_content.split('\n')
            title = task['title']
            for line in lines:
                if line.strip().startswith("# "):
                    title = line.strip()[2:]
                    break
                elif line.strip().startswith("Title: "):
                    title = line.strip()[7:]
                    break
            
            create_kb_article(title, article_content)
        else:
             print("    -> Not useful for KB.")

if __name__ == "__main__":
    main()
