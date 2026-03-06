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

## Key Files
- Bot: `bot/yougile_bot.py` (all handlers), `bot/ai_prioritizer.py`
- Event log: `bot/event_log.py` (SQLite + FastAPI webhooks)
- Feature plan: `docs/meeting_transcript_feature.md`
- API spec: `data/document (1).json` (OpenAPI)
