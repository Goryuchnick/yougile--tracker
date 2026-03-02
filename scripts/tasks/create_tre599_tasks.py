# -*- coding: utf-8 -*-
"""
Скрипт создаёт в YouGile задачи по разбивке TRE-599 (аналитика, лендинги, Директ, Alpina GPT, контент/SMM)
с чеклистами и дедлайнами. Базовая дата: 13 февраля 2026.

Перед запуском: укажите API_KEY или положите ключ в yougile_key.txt.
Колонка по умолчанию: "Надо сделать" (можно задать COLUMN_NAME внизу скрипта).
"""

import requests
import sys
import os
from datetime import datetime
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_dotenv()

BASE_URL = "https://yougile.com/api-v2"
API_KEY = os.environ.get("YOUGILE_API_KEY", "")

TARGET_COLUMN_NAME = "Надо сделать"


def date_to_deadline_ms(year, month, day, hour=18, minute=0):
    """Дата → timestamp в миллисекундах для стикера Дэдлайн (конец дня по Москве условно)."""
    dt = datetime(year, month, day, hour, minute, 0)
    return int(dt.timestamp() * 1000)


def find_column_id(headers):
    """Ищет колонку с названием TARGET_COLUMN_NAME во всех проектах/досках."""
    projects_resp = requests.get(
        f"{BASE_URL}/projects", headers=headers, params={"limit": 50, "includeDeleted": "false"}
    )
    if projects_resp.status_code != 200:
        raise RuntimeError(f"Ошибка проектов: {projects_resp.status_code}")
    projects = projects_resp.json().get("content", [])
    for project in projects:
        boards_resp = requests.get(
            f"{BASE_URL}/boards", headers=headers, params={"projectId": project["id"], "limit": 50}
        )
        if boards_resp.status_code != 200:
            continue
        for board in boards_resp.json().get("content", []):
            columns_resp = requests.get(
                f"{BASE_URL}/columns", headers=headers, params={"boardId": board["id"], "limit": 50}
            )
            if columns_resp.status_code != 200:
                continue
            for col in columns_resp.json().get("content", []):
                if col.get("title", "").strip().lower() == TARGET_COLUMN_NAME.lower():
                    return col["id"], project.get("title"), board.get("title")
    raise RuntimeError(f'Колонка "{TARGET_COLUMN_NAME}" не найдена.')


def make_checklist(items):
    """Формирует чеклист для API: [ {"title": "...", "isCompleted": False}, ... ] """
    return [{"title": t, "isCompleted": False} for t in items]


# Все подзадачи: (title, deadline_ymd, checklist_items)
# ymd = (year, month, day)
TASKS = [
    # --- 1. Аналитика и техчасть ---
    (
        "1.1 Цели и события (Метрика/Analytics)",
        (2026, 2, 18),
        [
            "Добавить цель/событие «клик/переход по ссылке в Telegram»",
            "Убедиться, что конверсия считается в отчётах",
            "Проверить на тестовом переходе",
        ],
    ),
    (
        "1.2 Созвон с аналитиками",
        (2026, 2, 20),
        [
            "Назначить дату/время созвона",
            "Проверить корректность внедрения событий",
            "Отдельно проверить кастомные скрипты для кнопок Tilda",
            "Зафиксировать итоги и доработки",
        ],
    ),
    (
        "1.3 Honeypot в Tilda",
        (2026, 2, 17),
        [
            "Узнать в Tilda/документации про скрытые поля",
            "Проверить возможность скрытого/микро-чекбокса для отсева ботов",
            "Внедрить при наличии возможности",
            "Проверить: боты не проходят, люди — проходят",
        ],
    ),
    (
        "1.4 Передача IP (вебхуки / Calltouch)",
        (2026, 2, 24),
        [
            "Изучить передачу IP через вебхуки (Tilda/CRM)",
            "Проверить интеграцию с Calltouch для получения IP",
            "Настроить передачу IP для банов фрода",
            "Протестировать на тестовой заявке",
        ],
    ),
    (
        "1.5 Вёрстка статей на сайте",
        (2026, 2, 19),
        [
            "Проверить отображение HTML в статьях",
            "Согласовать с разработчиками список правок",
            "Убедиться, что вёрстка отображается корректно после правок",
        ],
    ),
    # --- 2. Лендинги ---
    (
        "2.1 Адаптивная логика лендинга (ПК → форма, моб → Telegram)",
        (2026, 2, 27),
        [
            "ПК: CTA ведёт на форму заявки",
            "Мобильная версия: CTA ведёт на кнопку/ссылку в Telegram-бот",
            "Проверить на разных разрешениях и устройствах",
            "Задеплоить и проверить в проде",
        ],
    ),
    (
        "2.2 Лендинг под конкурентов",
        (2026, 3, 6),
        [
            "Определить список конкурентов и их офферы",
            "Сделать страницу, похожую на конкурентов, с лучшим офером",
            "Подключить к рекламным кампаниям по конкурентам",
        ],
    ),
    (
        "2.3 A/B тест на брендовом трафике",
        (2026, 3, 6),
        [
            "Подготовить вариант «Брендовый сайт» и «Подробный лендинг»",
            "Настроить сплит и учёт конверсий",
            "Запустить тест, собрать статистику и принять решение",
        ],
    ),
    # --- 3. Директ ---
    (
        "3.1 Остановить рекламу в Telegram-каналах",
        (2026, 2, 14),
        [
            "Остановить кампании/размещения в Telegram-каналах",
            "Зафиксировать причину: фрод и низкое качество",
        ],
    ),
    (
        "3.2 Перенос бюджета и расширение в Директе",
        (2026, 2, 27),
        [
            "Перенести бюджет из Telegram в Директ",
            "Расширить кампании: Бренд, Конкуренты, новая семантика B2B",
        ],
    ),
    (
        "3.3 Галерея услуг (Яндекс.Бизнес + Alpina GPT фид)",
        (2026, 3, 6),
        [
            "Корп. библиотека: доступы в Яндекс.Бизнес, добавить услуги, включить в кампанию",
            "Alpina GPT: товарный фид, размножить услугу на 10+ позиций",
        ],
    ),
    (
        "3.4 Баннер на поиске (МКБ)",
        (2026, 3, 13),
        [
            "Подготовить креативы: статика + HTML/анимация",
            "Настроить порядок: Бренд → Конкуренты → Общие ключи",
            "Запустить баннер по плану",
        ],
    ),
    # --- 4. Alpina GPT SEO ---
    (
        "4.1 Материалы для семантики (B2B) — передать агентству",
        (2026, 2, 20),
        [
            "Собрать примеры формулировок (корп. обучение нейросетям, on-prem, облако)",
            "Передать материалы агентству",
        ],
    ),
    (
        "4.2 Бюджет на SEO Alpina GPT ~55 тыс/мес",
        (2026, 2, 27),
        [
            "Согласовать бюджет (конкуренты, аудит, семантика, ссылки)",
            "Зафиксировать в договоре/допсоглашении",
        ],
    ),
    (
        "4.3 Чек-лист AEO/GEO от Максима Григорова",
        (2026, 2, 27),
        [
            "Запросить чек-лист по AEO/GEO (оптимизация под AI-системы)",
            "Внедрить рекомендации в план работ по Alpina GPT",
        ],
    ),
    # --- 5. Контент, SMM, таргет ---
    (
        "5.1 Приостановить таргет ВКонтакте",
        (2026, 2, 14),
        [
            "Приостановить таргет ВКонтакте",
            "Фокус на контекст/SEO/ивенты",
        ],
    ),
    (
        "5.2 Эстимейт по видео-продакшну (Shorts/Reels) ~200к за 10 роликов",
        (2026, 2, 24),
        [
            "Запросить расчёт у подрядчика",
            "Принять решение по бюджету и срокам",
        ],
    ),
    (
        "5.3 Запросить стоимость ведения соцсетей (постинг)",
        (2026, 2, 24),
        [
            "Запросить у агентства расчёт ведения соцсетей",
            "Сравнить с текущим бюджетом и KPI",
        ],
    ),
    (
        "5.4 Запросить варианты альтернативного лидгена у агентства",
        (2026, 2, 27),
        [
            "Запросить варианты: оплата за лид, обзвоны и т.д.",
            "Оценить и выбрать модель при необходимости",
        ],
    ),
]


def main():
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    print("Поиск колонки для задач...")
    column_id, project_title, board_title = find_column_id(headers)
    print(f"Колонка: «{TARGET_COLUMN_NAME}» в проекте «{project_title}», доска «{board_title}»\n")

    created = []
    for title, ymd, checklist_items in TASKS:
        deadline_ts = date_to_deadline_ms(*ymd)
        task_body = {
            "title": title,
            "columnId": column_id,
            "description": f"TRE-599. Дедлайн: {ymd[2]:02d}.{ymd[1]:02d}.{ymd[0]}",
            "deadline": {"deadline": deadline_ts, "withTime": False},
            "checklists": [
                {
                    "title": "Чеклист",
                    "items": make_checklist(checklist_items),
                }
            ],
        }
        r = requests.post(f"{BASE_URL}/tasks", headers=headers, json=task_body)
        if r.status_code == 201:
            task_id = r.json().get("id")
            created.append((title, task_id))
            try:
                print(f"  OK: {title}")
            except UnicodeEncodeError:
                print(f"  OK: {title.encode('ascii', 'replace').decode()}")
        else:
            print(f"  Ошибка {r.status_code}: {title}")
            print(f"     {r.text[:200]}")

    print(f"\nСоздано задач: {len(created)}")
    if created:
        print("Ссылки (подставьте свой домен/team при необходимости):")
        for t, tid in created:
            print(f"  https://yougile.com/team/ce859ce6c778/#{tid}")


if __name__ == "__main__":
    main()
