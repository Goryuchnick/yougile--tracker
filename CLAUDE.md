# YouGile AI Automation Suite

## Project
YouGile automation: Telegram bot, AI task prioritization, weekly reports, meeting transcripts → tasks.

## Stack
- Python 3.12 + venv
- YouGile API v2: `https://yougile.com/api-v2`, Bearer token auth
- Gemini: `google-genai` SDK — `from google import genai` — models: `gemini-2.5-flash-lite` (chat/text), `gemini-2.5-flash` (audio)
- python-telegram-bot >= 20.0 (async)
- Deploy: Coolify on VPS (8GB RAM, 3 cores)

## Structure
```
bot/             — Telegram bot, AI prioritizer
scripts/tasks/   — Task creation scripts
scripts/setup/   — API discovery/setup
scripts/utils/   — Reports, export
data/            — JSON data, stickers, API spec
docs/            — Plans and feature docs
```

## Commands
- `pip install -r requirements.txt`
- `python bot/yougile_bot.py`
- `python scripts/utils/weekly_report.py`

## Rules
- Secrets via `.env` / `os.getenv()` only — never hardcode
- UI language and AI prompts: Russian
- YouGile tasks: `GET /task-list` for listing, `POST /tasks` for creating (POST /task-list does NOT exist)
- Sync functions (requests, Gemini) → always wrap in `run_in_executor`

## Agents & Skills — MANDATORY USE

**Always use the appropriate agent or skill before writing code yourself.**

| Task | Use |
|------|-----|
| Bot changes (handlers, commands, features) | Agent `tg-bot-dev` or skill `/bot-update` |
| YouGile API, scripts, integrations | Agent `yougile-dev` |
| API exploration, endpoint testing | Agent `api-explorer` |
| Deploy, server, Coolify | Agent `deployer` |
| Weekly report | Skill `/weekly-report` |
| Meeting transcript processing | Skill `/transcript` |
| Task creation scripts | Skill `/create-tasks` |
| Deploy flow | Skill `/deploy` |

Agents: `@.claude/agents/` — `tg-bot-dev`, `yougile-dev`, `deployer`, `api-explorer`
Skills: `@.claude/skills/` — `/bot-update`, `/weekly-report`, `/deploy`, `/transcript`, `/create-tasks`

## AI Analysis Features
- Reports: all report types get AI summary as a second message (`ai_report_summary`)
- Active tasks: AI highlights overdue, near-deadline, stale tasks (`ai_active_analysis`)
- Workload: created vs completed, backlog trend, per-assignee load (`get_workload_report` + `ai_workload_analysis`)

## Mini App (Telegram WebApp)
- Dashboard: `bot/webapp/index.html` — visual task overview
- API: `GET /api/dashboard` in `bot/event_log.py` — aggregated data
- Static: served via FastAPI StaticFiles at `/app/`
- URL: `https://yougile-webhook.147.45.184.108.sslip.io/app`
- Opens via "Дашборд" button (WebAppInfo in ReplyKeyboard)

## Key Files
- Bot: `bot/yougile_bot.py` (all handlers), `bot/ai_prioritizer.py`
- Event log + Mini App API: `bot/event_log.py` (SQLite + FastAPI + dashboard)
- Mini App frontend: `bot/webapp/index.html`
- Feature plan: `docs/meeting_transcript_feature.md`
- API spec: `data/document (1).json` (OpenAPI)
