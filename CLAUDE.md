# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

An AI-powered Slack bot for the Metalios real estate team that searches a Google Workspace Shared Drive using natural language. Claude interprets user intent, searches the drive (Drive API + RAG), and returns direct file links in Slack.

**Entry point:** `python bot.py`

**Interaction modes:**
- `@mention` in a channel — bot replies in-thread
- Direct message — bot replies directly (no `@mention` needed)
- `/help <question>` slash command — ephemeral reply
- AI Assistant tab — fires `assistant_thread_started` event

---

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in real values
python bot.py
```

**RAG index (required for content search):**
```bash
python rag_indexer.py          # incremental if changes_token.txt exists, else full
python rag_indexer.py --full   # force full re-index
```

---

## Environment Variables

All secrets in `.env` (gitignored). See `.env.example` for the template.

| Variable | Required | Default | Description |
|---|---|---|---|
| `SLACK_BOT_TOKEN` | yes | — | Bot OAuth token (`xoxb-…`) |
| `SLACK_APP_TOKEN` | yes | — | App-level token for Socket Mode (`xapp-…`) |
| `ANTHROPIC_API_KEY` | yes | — | Anthropic API key |
| `OPENAI_API_KEY` | yes (RAG) | — | OpenAI key for `text-embedding-3-small` embeddings |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | yes | `service_account.json` | Path to service account JSON |
| `SHARED_DRIVE_ID` | yes | — | Google Shared Drive ID |
| `CLAUDE_MODEL` | no | `claude-sonnet-4-5` | Override the Claude model |
| `CHROMA_DB_PATH` | no | `./chroma_db` | Chroma vector DB path |

The service account JSON is gitignored — place it manually.

---

## Architecture

```
bot.py             Slack event handlers, orchestration, entry point
drive_search.py    Google Drive API — auth and file search
ai_handler.py      Anthropic Claude — agentic search loop with tool use
rag_retriever.py   Query-time RAG: embed question → semantic chunk search
rag_indexer.py     Index pipeline: parse Drive files → embed → store in Chroma
```

### Request data flow

```
Slack event → bot.py → _search_and_respond(question)
  → ai_handler.build_response(question)
      → Claude agentic loop (up to MAX_SEARCH_ROUNDS = 4)
          → tool call: search(query)
              → drive_search.search_shared_drive()   ← file links
              → rag_retriever.search()               ← content chunks
              ← combined {files, content_chunks} JSON payload
      ← Claude answers from content (with citation) or returns file links
  → say() / respond() back to Slack
```

If RAG index hasn't been built yet, `rag_retriever.search()` returns `[]` and the bot falls back to Drive file links only.

### RAG index pipeline (run separately on a schedule)

```
rag_indexer.py
  → list all Shared Drive files
  → export/download and parse (Google Docs → text, PDFs → pypdf)
  → chunk (500-word overlapping) → embed via OpenAI text-embedding-3-small
  → upsert into Chroma (./chroma_db/)
  → save Drive Changes API token (changes_token.txt) for incremental runs
```

---

## Key Behavioral Notes

**`ai_handler.py` agentic loop:**
1. Send question to Claude with the single `search` tool available
2. `stop_reason == "end_turn"` → return the text block
3. `stop_reason == "tool_use"` → call both `search_shared_drive` and `rag_retriever.search`, combine into `{files, content_chunks}` JSON as `tool_result`, increment round counter
4. Repeat up to `MAX_SEARCH_ROUNDS` (4); fall back to a "couldn't find" message if exhausted
5. `_files` parameter in `build_response` is accepted but ignored (historical compatibility with `bot.py`)

**`bot.py` reactions:** `👀` on receipt, `✅` on success — both calls silently swallowed, non-critical.

**`drive_search.py`:** Results ordered by `modifiedTime desc`. Query special chars are escaped before the Drive API `fullText contains` filter. `cache_discovery=False` prevents stale discovery docs.

**System prompt** (`ai_handler._SYSTEM`): Controls content-vs-file-link response rules, max 4 files in response, and Slack mrkdwn formatting. If you change `MAX_SEARCH_ROUNDS`, update the stated limit in the system prompt too.

---

## API Integrations

**Slack:** `slack-bolt` + `SocketModeHandler` — no public HTTP endpoint needed. App config in `slack_manifest.yaml` (importable at api.slack.com/apps).

**Google Drive:** Service account with `drive.readonly` scope. Single Shared Drive searched via Drive API v3.

**Anthropic Claude:** Tool use (function calling) with a single `search` tool. Default model `claude-sonnet-4-5`; Haiku is faster/cheaper for simple lookups.

---

## Coding Conventions

- **Logging:** `logging.getLogger(__name__)` in every module; `bot.py` owns `basicConfig`. Use `logger.info/error`, never `print`.
- **Error handling:** Drive `HttpError` is caught and re-raised (logged first). Agentic loop catches search errors and surfaces them as strings to Claude so the loop can continue. `_search_and_respond` is the top-level catch-all.
- **Style:** Python 3.10+ type hints, PEP 8. No linter configured.
- **Commits:** Semantic — `feat:`, `fix:`, `chore:`, `docs:`, etc.

---

## Infrastructure Notes

- No test suite, CI/CD, linter, Docker, or Makefile in the repo
- `SETUP.md` documents production deployment: `nohup`, `systemd`, or Docker
- Slack app manifest: `slack_manifest.yaml`
