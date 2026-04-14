# Slack Drive Bot — Setup Guide

This bot lets Metalios team members find Google Drive files by asking natural
language questions in Slack. It uses Claude Sonnet 4.5 to intelligently interpret
vague or imprecise questions, running multiple Drive searches until it finds the
right files — even when team members can't quite describe what they're looking for.

Three things need to be configured: a Slack app, a Google service account, and
your environment variables.

---

## Step 1 — Create the Slack App

> **Shortcut:** Instead of the manual steps below, you can use the included `slack_manifest.yaml` to configure the app automatically.
> Go to **https://api.slack.com/apps → Create New App → From a manifest**, paste in the file contents, and Slack will configure all scopes and settings for you. Then skip to step 6 to install and grab your tokens.

1. Go to **https://api.slack.com/apps** and click **Create New App → From scratch**.
2. Name it (e.g. *Drive Bot*) and pick your Metalios workspace.

### Enable Socket Mode
3. In the left sidebar → **Socket Mode** → toggle **Enable Socket Mode** ON.
4. You'll be prompted to create an **App-Level Token**.
   - Name it anything (e.g. *socket-token*)
   - Add scope: `connections:write`
   - Click **Generate** — copy this token. It starts with `xapp-`.
   - Paste it into `.env` as `SLACK_APP_TOKEN`.

### Add Bot Scopes
5. Left sidebar → **OAuth & Permissions** → scroll to **Bot Token Scopes** → Add:
   - `app_mentions:read` — lets the bot receive @mention events
   - `chat:write` — lets the bot post messages
   - `reactions:write` — lets the bot add the 👀 and ✅ reactions (optional but nice)
6. Scroll up and click **Install to Workspace** → Authorize.
7. Copy the **Bot User OAuth Token** (starts with `xoxb-`).
   - Paste it into `.env` as `SLACK_BOT_TOKEN`.

### Enable Event Subscriptions
8. Left sidebar → **Event Subscriptions** → toggle **Enable Events** ON.
9. Under **Subscribe to bot events** → **Add Bot User Event** → add:
   - `app_mention`
10. Click **Save Changes**.

### Invite the bot to channels
11. In any Slack channel where you want the bot: type `/invite @Drive Bot` (or whatever you named it).

---

## Step 2 — Set Up Google Service Account

### Create the service account
1. Go to **https://console.cloud.google.com**
2. Select (or create) your project.
3. **APIs & Services → Library** → search for **Google Drive API** → Enable it.
4. **IAM & Admin → Service Accounts → Create Service Account**
   - Name it something like `slack-drive-bot`
   - Role: you can skip role assignment (Drive permissions are set on the Drive itself)
   - Click **Done**
5. Click on the new service account → **Keys** tab → **Add Key → Create new key → JSON**
   - Download the JSON file
   - Rename it `service_account.json`
   - Place it in the `slack_drive_bot/` folder (next to `bot.py`)
   - ⚠️  Add `service_account.json` to your `.gitignore` — never commit this file

### Share the Shared Drive with the service account
6. Open **Google Drive** in your browser.
7. In the left sidebar, find your **Shared Drive** (e.g. *Metalios Team Resources*).
8. Right-click it → **Manage members**
9. Add the service account email (it looks like `slack-drive-bot@your-project.iam.gserviceaccount.com`)
   - Give it **Viewer** access (read-only is all we need)
10. Click **Send**.

### Get your Shared Drive ID
11. In Google Drive, click on the Shared Drive so its contents are showing.
12. Look at the URL bar — it will look like:
    ```
    https://drive.google.com/drive/folders/0ABCdef1234567890
    ```
    The long string after `/folders/` is your **Shared Drive ID**.
13. Paste it into `.env` as `SHARED_DRIVE_ID`.

---

## Step 3 — Configure Environment Variables

```bash
cd slack_drive_bot
cp .env.example .env
# Edit .env with your real values (see comments inside the file)
```

Required values:

| Variable | Where to find it |
|---|---|
| `SLACK_BOT_TOKEN` | api.slack.com/apps → OAuth & Permissions → Bot User OAuth Token |
| `SLACK_APP_TOKEN` | api.slack.com/apps → Basic Information → App-Level Tokens |
| `ANTHROPIC_API_KEY` | console.anthropic.com/settings/keys |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Path to your downloaded JSON key (default: `service_account.json`) |
| `SHARED_DRIVE_ID` | Last segment of your Shared Drive URL |

Optional:

| Variable | Default | Notes |
|---|---|---|
| `CLAUDE_MODEL` | `claude-sonnet-4-5` | Swap to `claude-haiku-4-5-20251001` for faster/cheaper responses |

---

## Step 4 — Install Dependencies & Run

```bash
# Python 3.11+ recommended
cd slack_drive_bot

python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate

pip install -r requirements.txt

python bot.py
```

You should see:
```
INFO  Starting Slack Drive Bot (Socket Mode)…
INFO  Bolt app is running!
```

---

## Testing It

In any Slack channel where the bot has been invited, type:

```
@Drive Bot where is the listing presentation?
```

The bot will:
1. Add 👀 to your message (it's searching)
2. Claude interprets the question and runs Drive searches — trying different keywords if the first attempt comes up empty (up to 4 rounds)
3. Reply in-thread with the most relevant file links
4. Replace 👀 with ✅

It handles vague questions well — e.g. *"that onboarding thing"* or *"the Smith deal doc"* — because Claude picks its own search terms rather than doing a literal keyword match.

---

## Running in Production

For a long-lived deployment, run the bot as a background process or system service.

**Using `nohup` (quick and dirty):**
```bash
nohup python bot.py > bot.log 2>&1 &
```

**Using `systemd` (recommended on Linux):**
Create `/etc/systemd/system/slack-drive-bot.service`:
```ini
[Unit]
Description=Slack Drive Bot
After=network.target

[Service]
User=youruser
WorkingDirectory=/path/to/slack_drive_bot
EnvironmentFile=/path/to/slack_drive_bot/.env
ExecStart=/path/to/slack_drive_bot/.venv/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
Then: `sudo systemctl enable --now slack-drive-bot`

**Using Docker:** The bot has no web server so no port mapping is needed:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "bot.py"]
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Bot doesn't respond | Not invited to channel | `/invite @Drive Bot` |
| `SHARED_DRIVE_ID is not set` | Missing env var | Check `.env` |
| `HttpError 403` from Drive | Service account not shared on the drive | Re-check Step 2 |
| `HttpError 404` from Drive | Wrong Drive ID | Must be a Shared Drive ID, not a regular folder ID |
| Bot responds but finds nothing | File not in Shared Drive | Move/share the file into the Shared Drive |
| Slow responses | Sonnet running multiple search rounds | Normal for complex queries — expect 3–6s |
| `SLACK_APP_TOKEN` error | Wrong token type | Must start with `xapp-`, not `xoxb-` |

---

## File Structure

```
slack_drive_bot/
├── bot.py               ← Slack Socket Mode app & event handlers
├── drive_search.py      ← Google Drive API search (service account)
├── ai_handler.py        ← Claude Sonnet 4.5 — agentic search loop & response formatting
├── requirements.txt     ← Python dependencies
├── slack_manifest.yaml  ← Paste into api.slack.com to auto-configure the Slack app
├── .env.example         ← Copy to .env and fill in your values
├── .env                 ← Your secrets (never commit this)
├── service_account.json ← Google service account key (never commit this)
└── SETUP.md             ← This file
```
