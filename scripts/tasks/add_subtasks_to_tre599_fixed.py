# -*- coding: utf-8 -*-
"""
Добавляет ПОДЗАДАЧИ с чеклистами и дедлайнами ВНУТРИ существующей задачи TRE-599.
Создаёт подзадачи в той же колонке, что и родитель, затем привязывает их к задаче.

Использование: укажите API_KEY или положите ключ в yougile_key.txt.
Родительская задача ищется по idTaskProject == "TRE-599" (или задайте PARENT_TASK_ID вручную).
"""

import requests
import sys
from datetime import datetime

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_URL = "https://yougile.com/api-v2"

import os
API_KEY = os.environ.get("YOUGILE_API_KEY", "")

# Если известен UUID задачи TRE-599 — укажите сюда, иначе скрипт ищет по idTaskProject
PARENT_TASK_ID = None  # например "a1b2c3d4-..."

PARENT_TASK_KEY = "TRE-599"


def date_to_deadline_ms(year, month, day, hour=18, minute=0):
    dt = datetime(year, month, day, hour, minute, 0)
    return int(dt.timestamp() * 1000)


def make_checklist(items):
    return [{"title": t, "isCompleted": False} for t in items]


# Подзадачи: (title, deadline_ymd, checklist_items)
SUBTASKS = [
    ("1.1 Цели и события (Метрика/Analytics)", (2026, 2, 18), [
        "Добавить цель/событие «клик/переход по ссылке в Telegram»",
        "Убедиться, что конверсия считается в отчётах",
        "Проверить на тестовом переходе",
    ]),
    ("1.2 Созвон с аналитиками", (2026, 2, 20), [
        "Назначить дату/время созвона",
        "Проверить корректность внедрения событий",
        "Отдельно проверить кастомные скрипты для кнопок Tilda",
        "Зафиксировать итоги и доработки",
    ]),
    ("1.3 Honeypot в Tilda", (2026, 2, 17), [
        "Узнать в Tilda/документации про скрытые поля",
        "Проверить возможность скрытого/микро-чекбокса для отсева ботов",
        "Внедрить при наличии возможности",
        "Проверить: боты не проходят, люди — проходят",
    ]),
    ("1.4 Передача IP (вебхуки / Calltouch)", (2026, 2, 24), [
        "Изучить передачу IP через вебхуки (Tilda/CRM)",
        "Проверить интеграцию с Calltouch для получения IP",
        "Настроить передачу IP для банов фрода",
        "Протестировать на тестовой заявке",
    ]),
    ("1.5 Вёрстка статей на сайте", (2026, 2, 19), [
        "Проверить отображение HTML в статьях",
        "Согласовать с разработчиками список правок",
        "Убедиться, что вёрстка отображается корректно после правок",
    ]),
    ("2.1 Адаптивная логика лендинга (ПК - форма, моб - Telegram)", (2026, 2, 27), [
        "ПК: CTA ведёт на форму заявки",
        "Мобильная версия: CTA ведёт на кнопку/ссылку в Telegram-бот",
        "Проверить на разных разрешениях и устройствах",
        "Задеплоить и проверить в проде",
    ]),
    ("2.2 Лендинг под конкурентов", (2026, 3, 6), [
        "Определить список конкурентов и их офферы",
        "Сделать страницу, похожую на конкурентов, с лучшим офером",
        "Подключить к рекламным кампаниям по конкурентам",
    ]),
    ("2.3 A/B тест на брендовом трафике", (2026, 3, 6), [
        "Подготовить вариант «Брендовый сайт» и «Подробный лендинг»",
        "Настроить сплит и учёт конверсий",
        "Запустить тест, собрать статистику и принять решение",
    ]),
    ("3.1 Остановить рекламу в Telegram-каналах", (2026, 2, 14), [
        "Остановить кампании/размещения в Telegram-каналах",
        "Зафиксировать причину: фрод и низкое качество",
    ]),
    ("3.2 Перенос бюджета и расширение в Директе", (2026, 2, 27), [
        "Перенести бюджет из Telegram в Директ",
        "Расширить кампании: Бренд, Конкуренты, новая семантика B2B",
    ]),
    ("3.3 Галерея услуг (Яндекс.Бизнес + Alpina GPT фид)", (2026, 3, 6), [
        "Корп. библиотека: доступы в Яндекс.Бизнес, добавить услуги, включить в кампанию",
        "Alpina GPT: товарный фид, размножить услугу на 10+ позиций",
    ]),
    ("3.4 Баннер на поиске (МКБ)", (2026, 3, 13), [
        "Подготовить креативы: статика + HTML/анимация",
        "Настроить порядок: Бренд - Конкуренты - Общие ключи",
        "Запустить баннер по плану",
    ]),
    ("4.1 Материалы для семантики (B2B) — передать агентству", (2026, 2, 20), [
        "Собрать примеры формулировок (корп. обучение нейросетям, on-prem, облако)",
        "Передать материалы агентству",
    ]),
    ("4.2 Бюджет на SEO Alpina GPT ~55 тыс/мес", (2026, 2, 27), [
        "Согласовать бюджет (конкуренты, аудит, семантика, ссылки)",
        "Зафиксировать в договоре/допсоглашении",
    ]),
    ("4.3 Чек-лист AEO/GEO от Максима Григорова", (2026, 2, 27), [
        "Запросить чек-лист по AEO/GEO (оптимизация под AI-системы)",
        "Внедрить рекомендации в план работ по Alpina GPT",
    ]),
    ("5.1 Приостановить таргет ВКонтакте", (2026, 2, 14), [
        "Приостановить таргет ВКонтакте",
        "Фокус на контекст/SEO/ивенты",
    ]),
    ("5.2 Эстимейт по видео-продакшну (Shorts/Reels) ~200к за 10 роликов", (2026, 2, 24), [
        "Запросить расчёт у подрядчика",
        "Принять решение по бюджету и срокам",
    ]),
    ("5.3 Запросить стоимость ведения соцсетей (постинг)", (2026, 2, 24), [
        "Запросить у агентства расчёт ведения соцсетей",
        "Сравнить с текущим бюджетом и KPI",
    ]),
    ("5.4 Запросить варианты альтернативного лидгена у агентства", (2026, 2, 27), [
        "Запросить варианты: оплата за лид, обзвоны и т.д.",
        "Оценить и выбрать модель при необходимости",
    ]),
]


def find_parent_task(headers):
    """Ищет задачу с idTaskProject == PARENT_TASK_KEY или по PARENT_TASK_ID."""
    if PARENT_TASK_ID:
        r = requests.get(f"{BASE_URL}/tasks/{PARENT_TASK_ID}", headers=headers)
        if r.status_code == 200:
            t = r.json()
            return t["id"], t.get("columnId"), t.get("title", "")
        raise RuntimeError(f"Задача с ID {PARENT_TASK_ID} не найдена: {r.status_code}")
    offset = 0
    limit = 200
    while True:
        r = requests.get(
            f"{BASE_URL}/task-list",
            headers=headers,
            params={"limit": limit, "offset": offset, "includeDeleted": False},
        )
        if r.status_code != 200:
            raise RuntimeError(f"Ошибка task-list: {r.status_code} {r.text[:300]}")
        data = r.json()
        content = data.get("content", [])
        if not content:
            break
        for task in content:
            if task.get("idTaskProject") == PARENT_TASK_KEY:
                return task["id"], task.get("columnId"), task.get("title", "")
            if PARENT_TASK_KEY in (task.get("title") or ""):
                return task["id"], task.get("columnId"), task.get("title", "")
        paging = data.get("paging", {})
        if offset + len(content) >= paging.get("count", 0):
            break
        offset += limit
    raise RuntimeError(
        f'Задача с idTaskProject или названием "{PARENT_TASK_KEY}" не найдена. '
        'Задайте PARENT_TASK_ID в скрипте вручную (UUID задачи из YouGile).'
    )


def main():
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    print("Поиск родительской задачи TRE-599...")
    parent_id, _, parent_title = find_parent_task(headers)
    print(f"Найдена: {parent_title or PARENT_TASK_KEY} (id: {parent_id})")
    print(f"Колонка для подзадач: {column_id}\n")

    subtask_ids = []
    for title, ymd, checklist_items in SUBTASKS:
        deadline_ts = date_to_deadline_ms(*ymd)
        body = {
            "title": title,
            "description": f"Подзадача {PARENT_TASK_KEY}. Дедлайн: {ymd[2]:02d}.{ymd[1]:02d}.{ymd[0]}",
            "deadline": {"deadline": deadline_ts, "withTime": False},
            "checklists": [
                {"title": "Чеклист", "items": make_checklist(checklist_items)}
            ],
        }
        r = requests.post(f"{BASE_URL}/tasks", headers=headers, json=body)
        if r.status_code == 201:
            tid = r.json().get("id")
            subtask_ids.append(tid)
            try:
                print(f"  OK: {title}")
            except UnicodeEncodeError:
                print(f"  OK: {title.encode('ascii', 'replace').decode()}")
        else:
            print(f"  Ошибка {r.status_code}: {title} | {r.text[:150]}")

    if not subtask_ids:
        print("\nПодзадачи не созданы, привязка к родителю не выполняется.")
        return

    print(f"\nПривязка {len(subtask_ids)} подзадач к задаче {PARENT_TASK_KEY}...")
    r = requests.put(
        f"{BASE_URL}/tasks/{parent_id}",
        headers=headers,
        json={"subtasks": subtask_ids},
    )
    if r.status_code == 200:
        print("Готово. Подзадачи и чеклисты добавлены в задачу:")
        print(f"  https://yougile.com/team/ce859ce6c778/#{parent_id}")
    else:
        print(f"Ошибка привязки подзадач: {r.status_code}")
        print(r.text[:400])


if __name__ == "__main__":
    main()
