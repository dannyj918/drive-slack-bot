# CLAUDE.md — drive-slack-bot

## Project Overview

An AI-powered Slack bot that lets the Metalios real estate team find files in their Google Workspace Shared Drive using natural language. Users ask vague or precise questions; Claude interprets the intent, searches the drive intelligently, and returns direct file links in Slack.

**Interaction modes:**
- `@mention` in a channel — bot replies in-thread
- Direct message — bot replies directly (no `@mention` needed)
- `/help <question>` slash command — ephemeral reply (only the asker sees it)
- AI Assistant tab — fires `assistant_thread_started` event

**Entry point:** `python bot.py`

---

## Architecture

Five source files with clear responsibilities:

```
bot.py             Slack event handlers, orchestration, entry point
drive_search.py    Google Drive API — authentication and file search
ai_handler.py      Anthropic Claude — agentic search loop with tool use
rag_retriever.py   Query-time RAG: embed question → semantic chunk search
rag_indexer.py     Index pipeline: parse Drive files → embed → store in Chroma
```

### Data flow

```
Slack event
  → bot.py: handle_app_mention / handle_dm / handle_help
    → _search_and_respond(question)
      → ai_handler.py: build_response(question)
        → Claude agentic loop (up to 4 rounds)
          → tool call: search(query)  ← single unified tool
            → drive_search.py: search_shared_drive(query)  ← file links
            → rag_retriever.py: search(query)              ← content chunks
              ← combined {files, content_chunks} payload
          ← Claude answers from content (with citation) or returns file links
      ← formatted response string
  → say() / respond() back to Slack

Separately (run on a schedule):
  rag_indexer.py
    → lists all Shared Drive files
    → exports/downloads and parses each (Google Docs → text, PDFs → pypdf)
    → chunks text and embeds via OpenAI text-embedding-3-small
    → upserts into local Chroma vector DB (./chroma_db/)
    → saves Drive Changes API token for incremental future runs
```

---

## Development Setup

**Prerequisites:** Python 3.11+, a `.env` file (copy from `.env.example`), a Google service account JSON key.

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # then fill in real values
python bot.py
```

Expected startup output:
```
INFO Starting Slack Drive Bot (Socket Mode)…
INFO Bolt app is running!
```

---

## Environment Variables

All secrets live in `.env` (never committed). See `.env.example` for the full template with source instructions.

| Variable | Required | Default | Description |
|---|---|---|---|
| `SLACK_BOT_TOKEN` | yes | — | Bot OAuth token (`xoxb-…`) |
| `SLACK_APP_TOKEN` | yes | — | App-level token for Socket Mode (`xapp-…`) |
| `ANTHROPIC_API_KEY` | yes | — | Anthropic console API key |
| `OPENAI_API_KEY` | yes (for RAG) | — | OpenAI key for `text-embedding-3-small` embeddings |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | yes | `service_account.json` | Path to service account JSON key |
| `SHARED_DRIVE_ID` | yes | — | Google Shared Drive ID (from the drive URL) |
| `CLAUDE_MODEL` | no | `claude-sonnet-4-5` | Swap to `claude-haiku-4-5-20251001` for speed/cost |
| `CHROMA_DB_PATH` | no | `./chroma_db` | Where Chroma persists the vector DB on disk |

The service account JSON file is gitignored — it must be placed manually.

**Embedding cost:** `text-embedding-3-small` costs $0.02/million tokens. A full initial index of 100–200 docs ≈ $0.01–0.05 total. Incremental syncs (changed files only) cost nearly nothing.

---

## Key Files Reference

### `rag_indexer.py`

| Lines | Symbol | Purpose |
|---|---|---|
| 60–65 | `_EXPORTABLE` | MIME types Drive can export as text/plain or text/csv |
| 78–88 | `_build_drive_service()` | Authenticated Drive v3 service (same pattern as `drive_search.py`) |
| 96–122 | `list_all_files()` | Paginates through all non-trashed Shared Drive files |
| 125–162 | `extract_text(file)` | Exports Google Workspace files; downloads + pypdf-parses PDFs |
| 165–179 | `chunk_text(text)` | Splits text into 500-word overlapping chunks |
| 182–193 | `embed_texts(texts)` | Batched OpenAI `text-embedding-3-small` embeddings |
| 196–228 | `index_file(file, collection)` | Full pipeline for one file; deletes stale chunks before upserting |
| 231–255 | `_save_token / _load_token / _get_start_token` | Drive Changes API token persistence (`changes_token.txt`) |
| 258–281 | `full_sync(collection)` | Indexes everything; saves start token |
| 284–330 | `incremental_sync(collection)` | Re-indexes only changed files using Changes API |

Run manually: `python rag_indexer.py` (incremental if `changes_token.txt` exists, full otherwise).
Force full re-index: `python rag_indexer.py --full`.

### `rag_retriever.py`

| Lines | Symbol | Purpose |
|---|---|---|
| 28–37 | `_get_openai_client()` | Lazy singleton OpenAI client |
| 40–57 | `_get_collection()` | Lazy-loads Chroma collection; returns `None` gracefully if not yet indexed |
| 60–96 | `search(query, n_results=5)` | Embeds query → Chroma semantic search → returns `[{text, file_name, file_link}]` |

Returns `[]` if the index hasn't been built yet — bot falls back to Drive file links only.

### `bot.py`

| Lines | Symbol | Purpose |
|---|---|---|
| 53–74 | `handle_help` | `/help` slash command; ephemeral response |
| 81–123 | `handle_app_mention` | `@mention` handler; adds 👀 → ✅ reactions |
| 126–136 | `_search_and_respond` | Orchestrator: calls `search_shared_drive` + `build_response` |
| 143–145 | `handle_assistant_thread_started` | AI assistant tab greeting |
| 152–195 | `handle_dm` | Direct message handler; ignores bot messages and subtypes |
| 202–205 | `__main__` | Starts `SocketModeHandler` |

Reactions are wrapped in `try/except` and silently swallowed — non-critical (bot.py:109–112, 117–121).

### `drive_search.py`

| Lines | Symbol | Purpose |
|---|---|---|
| 32–43 | `_MIME_META` | Dict mapping MIME type → `(emoji, label)` for 9 file types |
| 46–53 | `_build_service()` | Creates authenticated Drive v3 service from service account JSON |
| 56–118 | `search_shared_drive(query, max_results=8)` | Full-text search on the Shared Drive; returns list of file dicts |

Returned file dicts include: `id`, `name`, `mimeType`, `webViewLink`, `modifiedTime`, `description`, `emoji`, `label`.

Results are ordered by `modifiedTime desc`. Special characters in `query` are escaped before building the Drive API filter string (drive_search.py:79).

### `ai_handler.py`

| Lines | Symbol | Purpose |
|---|---|---|
| 28–29 | `MAX_SEARCH_ROUNDS`, `CLAUDE_MODEL` | Module-level constants; model is overridable via env var |
| 32–36 | `_get_client()` | Lazy singleton Anthropic client |
| 43–72 | `_TOOLS` | Single unified `search` tool for Claude tool use |
| 79–103 | `_SYSTEM` | System prompt — content vs file-link response rules, Slack mrkdwn formatting |
| 106–199 | `build_response(question, _files=None)` | Agentic loop: sends question → handles `tool_use` / `end_turn` |
| 202–203 | `_escape(text)` | HTML-escapes `&`, `<`, `>` for safe Slack mrkdwn embedding |

The `_files` parameter in `build_response` is accepted but ignored — the agentic loop does its own searching. `bot.py` passes files for historical compatibility.

**Agentic loop logic (ai_handler.py:127–198):**
1. Send user question to Claude with the single `search` tool available
2. `stop_reason == "end_turn"` → extract text block, return it
3. `stop_reason == "tool_use"` → call both `search_shared_drive` and `rag_retriever.search`, combine results into `{files, content_chunks}` JSON, append as `tool_result`, increment `rounds`
4. Repeat up to `MAX_SEARCH_ROUNDS` (4)
5. If loop exhausts without `end_turn`, return a fallback "couldn't find a confident match" message

---

## API Integrations

### Slack (Slack Bolt + Socket Mode)
- Uses `slack-bolt` with `SocketModeHandler` — no public HTTP endpoint needed
- Requires two tokens: `SLACK_BOT_TOKEN` (bot actions) + `SLACK_APP_TOKEN` (Socket Mode connection)
- Slack app config lives in `slack_manifest.yaml` — importable at api.slack.com/apps

### Google Drive
- Service account authentication with `drive.readonly` scope
- Searches a single Shared Drive (`SHARED_DRIVE_ID`) using Drive API v3 `fullText contains` filter
- `cache_discovery=False` in `_build_service()` prevents stale discovery docs in long-running processes

### Anthropic Claude
- Uses the `anthropic` SDK with tool use (function calling)
- Default model: `claude-sonnet-4-5`; configurable via `CLAUDE_MODEL`
- `max_tokens=1024` per request — sufficient for Slack responses
- Client is a lazy singleton (`_client` global in `ai_handler.py`)

---

## Coding Conventions

**Logging:** All modules use `logging.getLogger(__name__)`. `bot.py` configures `basicConfig` at startup. Use `logger.info/error`, never `print`.

**Error handling:**
- Drive API `HttpError` is caught and re-raised in `drive_search.py` (logged first)
- Drive search errors in the agentic loop are caught, logged, and surfaced to Claude as a string so the loop can continue
- Top-level handler in `_search_and_respond` catches everything and returns a user-friendly error string
- Reaction API calls are silently swallowed — they're non-critical

**Style:** Python 3.10+ type hints in function signatures. Module and function docstrings throughout. No linter is configured — follow PEP 8.

**Commit messages:** Semantic format — `type: description` (e.g. `feat:`, `fix:`, `chore:`, `docs:`).

---

## Common Development Tasks

**Build the RAG index for the first time:**
```bash
python rag_indexer.py
```
Downloads model, exports all Drive files, embeds and stores in `./chroma_db/`. Saves a `changes_token.txt` for future incremental runs.

**Refresh the index after Drive changes:**
```bash
python rag_indexer.py          # incremental (uses changes_token.txt)
python rag_indexer.py --full   # force full re-index
```

**Schedule automatic incremental syncs (cron every 30 min):**
```
*/30 * * * * cd /path/to/drive-slack-bot && .venv/bin/python rag_indexer.py
```

**Add support for a new file type in the RAG index:**
Edit `_EXPORTABLE` in `rag_indexer.py` to add a MIME type → export MIME mapping. For binary formats (like `.docx`), add a new branch in `extract_text()`.

**Add a new file type emoji/label:**
Edit `_MIME_META` in `drive_search.py:32–43`. Add a MIME type → `(emoji, label)` entry. The fallback is `("📎", "File")` (drive_search.py:113).

**Change the Claude model:**
Set `CLAUDE_MODEL` in `.env`, or edit the default at `ai_handler.py:29`. Haiku is faster/cheaper; Sonnet gives better reasoning for ambiguous queries.

**Increase the number of search rounds:**
Edit `MAX_SEARCH_ROUNDS` at `ai_handler.py:28`. Also update the system prompt at `ai_handler.py:84` to keep the stated limit consistent.

**Change the max files returned in a Slack response:**
Edit the system prompt at `ai_handler.py:93` ("Maximum 4 files"). The Drive API search limit (`max_results=8` in `drive_search.py:56`) is a separate ceiling.

**Add a new Slack event:**
Register a new handler with `@app.event("event_name")` in `bot.py` following the existing pattern. Update `slack_manifest.yaml` under `event_subscriptions.bot_events` if it's a new event subscription.

---

## Infrastructure Notes

- **No test suite** — no pytest, unittest, or test files exist
- **No CI/CD** — no `.github/workflows/` or other pipeline configuration
- **No linter/formatter** — no `.flake8`, `pylintrc`, `.prettierrc`, etc.
- **No Docker** — a minimal Dockerfile is documented in `SETUP.md` but not present in the repo
- **No Makefile or build scripts**

For production deployment, `SETUP.md` documents three options: `nohup`, `systemd` service, and a simple Docker container.
