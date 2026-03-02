---
name: weekly-report
description: Генерация еженедельного отчёта по задачам YouGile с фильтрацией по направлению и датам
argument-hint: [YYYY-MM-DD] [--direction Альпина|Welcome|Агентство|Личное] [--all-directions]
disable-model-invocation: true
allowed-tools: Bash(python *), Read
---

# Еженедельный отчёт YouGile

Сформируй отчёт по задачам за указанный период.

## Как работает
Скрипт сканирует системные сообщения в чатах задач YouGile за период:
- 🔀 Перемещения между колонками (из какой → в какую)
- ✅ Отметки о выполнении
- 💬 Комментарии пользователей

## Запуск

```bash
python scripts/utils/weekly_report.py $ARGUMENTS
```

Примеры:
- `/weekly-report 2026-02-23` — неделя с 23 фев, направление Альпина
- `/weekly-report 2026-02-23 2026-02-28 --direction Агентство`
- `/weekly-report --all-directions` — прошлая неделя, все направления

## После запуска
1. Покажи результат пользователю в структурированном виде
2. Отчёт автоматически сохраняется в `docs/reports/report_YYYY-MM-DD.md`
3. Если пользователь просит — предложи отправить отчёт в Telegram или сохранить в Obsidian
