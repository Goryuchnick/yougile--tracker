@echo off
title YouGileBot
REM Загружаем переменные из .env файла (создайте его рядом с этим скриптом)
REM set TELEGRAM_BOT_TOKEN=your_token_here
REM set GEMINI_API_KEY=your_key_here
REM set YOUGILE_API_KEY=your_key_here
cd /d "d:\Programmes projects\yougile api"
python bot\yougile_bot.py
pause
