---
name: bot-update
description: Доработка Telegram-бота — добавление команды, хэндлера или фичи в yougile_bot.py
argument-hint: [описание что нужно добавить/изменить]
allowed-tools: Read, Edit, Write, Bash
---

# Доработка Telegram-бота

## Что нужно сделать

`$ARGUMENTS`

## Процесс

1. Прочитай `bot/yougile_bot.py` целиком
2. Пойми архитектуру — какие хэндлеры уже есть, как устроено хранение состояния
3. Реализуй изменение минимально и точно:
   - Добавь хэндлер/функцию
   - Зарегистрируй в `if __name__ == "__main__":`
   - Не трогай несвязанный код
4. Проверь синтаксис: `python -m py_compile bot/yougile_bot.py`
5. Выведи краткое описание изменений

## Ключевые правила

- SDK: `from google import genai` (НЕ `google.generativeai`)
- Модель: `gemini-2.5-flash-lite-preview-06-17`
- Синхронный код в `run_in_executor`, никогда не блокируй event loop
- Секреты только через `os.environ.get()`
- Тексты и ошибки — на русском языке
- Файлы после использования удалять в `finally`
