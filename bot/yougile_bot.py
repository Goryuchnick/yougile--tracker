import logging
import os
import requests
import json
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import google.generativeai as genai
import ai_prioritizer  # our refactored module

# --- Configuration ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

YOUGILE_BASE_URL = "https://yougile.com/api-v2"
YOUGILE_API_KEY = os.environ.get("YOUGILE_API_KEY")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- Gemini Setup ---
def get_gemini_model():
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not set.")
    genai.configure(api_key=GEMINI_API_KEY)
    return genai.GenerativeModel('gemini-2.0-flash')

# --- Helper: Create Task ---
def create_yougile_task(title, description):
    headers = {
        "Authorization": f"Bearer {YOUGILE_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # 1. Find Target Column
    target_columns = ["Входящие", "Inbox", "Бэклог", "Надо сделать"]
    target_column_id = None
    
    try:
        projects_resp = requests.get(f"{YOUGILE_BASE_URL}/projects", headers=headers, params={"limit": 50})
        if projects_resp.status_code == 200:
            for project in projects_resp.json().get('content', []):
                boards_resp = requests.get(f"{YOUGILE_BASE_URL}/boards", headers=headers, params={"projectId": project['id'], "limit": 20})
                if boards_resp.status_code != 200: continue
                for board in boards_resp.json().get('content', []):
                    cols_resp = requests.get(f"{YOUGILE_BASE_URL}/columns", headers=headers, params={"boardId": board['id'], "limit": 20})
                    if cols_resp.status_code != 200: continue
                    for col in cols_resp.json().get('content', []):
                        if col['title'] in target_columns:
                            target_column_id = col['id']
                            break
                    if target_column_id: break
                if target_column_id: break
    except Exception as e:
        return f"Error finding column: {e}"

    if not target_column_id:
        return "Target column not found."

    # 2. Create Task
    task_data = {
        "title": title,
        "description": description,
        "columnId": target_column_id
    }
    create_resp = requests.post(f"{YOUGILE_BASE_URL}/tasks", headers=headers, json=task_data)
    if create_resp.status_code == 201:
        new_task = create_resp.json()
        return f"Task created! ID: {new_task['id']}\nTitle: {title}"
    else:
        return f"Failed to create task: {create_resp.status_code} {create_resp.text}"

# --- Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я YouGile Bot (Powered by Gemini).\n"
        "1. Голосовое сообщение -> Создать задачу (мультимодально).\n"
        "2. /prioritize -> AI сортировка.\n"
        "3. /sync_kb -> База знаний."
    )

async def prioritize_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Gemini анализирует задачи...")
    try:
        loop = asyncio.get_event_loop()
        model = get_gemini_model()
        # Run synchronous prioritization logic
        result = await loop.run_in_executor(None, ai_prioritizer.run_prioritization, YOUGILE_API_KEY, model)
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def sync_kb_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Simulating KB Sync...")
    await asyncio.sleep(2)
    await update.message.reply_text("Synced.")

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("Слушаю... (Отправка аудио в Gemini) 🔼")
    
    voice_path = "voice.ogg"
    
    try:
        # 1. Download File
        voice_file = await update.message.voice.get_file()
        await voice_file.download_to_drive(voice_path)
        
        # 2. Process with Gemini
        model = get_gemini_model()
        
        # Uploading file to Gemini
        # Note: In a production bot, file management might need a different approach,
        # but for this script we will use the upload_file utility if available or pass data.
        # However, the simpler `generate_content` accepts parts.
        # But `google-generativeai` library usually expects a `File` object created via `genai.upload_file` for audio.
        
        # Let's try uploading the file
        uploaded_file = genai.upload_file(voice_path)
        
        prompt = """
        Listen to this voice message. 
        Extract a short 'title' for a task and a detailed 'description'.
        Return JSON: {"title": "...", "description": "..."}
        If the audio is not a task request, summarize it briefly as the description.
        """
        
        response = model.generate_content([prompt, uploaded_file])
        
        # Cleanup remote file if needed (optional for free tier limits)
        # uploaded_file.delete() 
        
        text_resp = response.text.strip()
        # Clean JSON markdown if present
        if text_resp.startswith("```json"):
            text_resp = text_resp.replace("```json", "").replace("```", "")
        
        try:
            task_info = json.loads(text_resp)
            title = task_info.get("title", "Voice Task")
            description = task_info.get("description", "No description")
        except:
            # Fallback if valid JSON not returned
            title = "Voice Task (Raw)"
            description = text_resp

        # 3. Create Task
        result = create_yougile_task(title, description)
        await context.bot.edit_message_text(f"{result}", chat_id=update.effective_chat.id, message_id=status_msg.message_id)

    except Exception as e:
        await context.bot.edit_message_text(f"Error: {e}", chat_id=update.effective_chat.id, message_id=status_msg.message_id)
    finally:
        if os.path.exists(voice_path):
            os.remove(voice_path)

if __name__ == '__main__':
    if not TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN missing.")
        exit(1)
    
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('prioritize', prioritize_command))
    application.add_handler(CommandHandler('sync_kb', sync_kb_command))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    
    print("Gemini Bot Started...")
    application.run_polling()
