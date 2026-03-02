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
- YouGile tasks: use `POST /task-list` (not deprecated `POST /tasks`)
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

## Key Files
- Bot: `bot/yougile_bot.py` (all handlers), `bot/ai_prioritizer.py`
- Feature plan: `docs/meeting_transcript_feature.md`
- API spec: `data/document (1).json` (OpenAPI)
