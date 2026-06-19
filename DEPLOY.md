# Cloud Deployment Guide

Deploy the Slack Drive Bot to a cloud host so it runs 24/7 without your laptop.
The bot uses **Slack Socket Mode** — no public URL, HTTPS certificate, or port
mapping required. It just needs a always-on process with your API keys.

---

## What you need before deploying

Complete the one-time setup in [SETUP.md](SETUP.md) first:

1. **Slack app** — Socket Mode enabled, tokens (`xoxb-…`, `xapp-…`)
2. **Google service account** — JSON key, Shared Drive shared with the account
3. **API keys** — Anthropic + OpenAI (for RAG embeddings)

You will **not** commit secrets. Paste them into your cloud host's secret/env UI.

---

## Recommended: Fly.io

Fly.io is a good fit: always-on machines, persistent volumes for the RAG index,
and secrets as environment variables. Cost is roughly **$3–5/month** for a
small always-on machine with a 1 GB volume.

### 1. Install the Fly CLI

```bash
# macOS
brew install flyctl

# Linux / WSL
curl -L https://fly.io/install.sh | sh
```

Sign up and log in:

```bash
fly auth signup   # or fly auth login
```

### 2. Clone the repo and create the app

```bash
git clone <your-repo-url>
cd drive-slack-bot

# Pick a unique app name (or edit app = "..." in fly.toml first)
fly launch --no-deploy
```

When prompted:

- **Use existing fly.toml?** → Yes
- **Deploy now?** → No (set secrets first)

### 3. Create a persistent volume

The RAG vector DB and Drive changes token must survive restarts:

```bash
fly volumes create drive_bot_data --region iad --size 1
```

(`iad` = US East; match your `primary_region` in `fly.toml`.)

### 4. Set secrets

Copy your service account JSON to the clipboard, then set all secrets:

```bash
fly secrets set \
  SLACK_BOT_TOKEN="xoxb-..." \
  SLACK_APP_TOKEN="xapp-..." \
  ANTHROPIC_API_KEY="sk-ant-..." \
  OPENAI_API_KEY="sk-..." \
  SHARED_DRIVE_ID="your-drive-id" \
  GOOGLE_SERVICE_ACCOUNT_JSON='{"type":"service_account",...}'
```

`GOOGLE_SERVICE_ACCOUNT_JSON` is the entire JSON key file as one line — no file
upload needed.

Optional:

```bash
fly secrets set CLAUDE_MODEL="claude-sonnet-4-5"
```

### 5. Deploy

```bash
fly deploy
```

Check logs:

```bash
fly logs
```

You should see:

```
INFO Starting Slack Drive Bot (Socket Mode)…
INFO Bolt app is running!
```

### 6. Build the RAG index (first time)

SSH into the machine and run a full index:

```bash
fly ssh console -C "python rag_indexer.py --full"
```

This can take several minutes depending on drive size. The data is stored on
the persistent volume at `/data/chroma_db`.

### 7. Schedule incremental RAG syncs

Re-index changed files every 30 minutes with a [scheduled Fly Machine](https://fly.io/docs/launch/scheduled-machines/):

```bash
fly machine run . \
  --schedule "*/30 * * * *" \
  --rm \
  --env CHROMA_DB_PATH=/data/chroma_db \
  --env CHANGES_TOKEN_FILE=/data/changes_token.txt \
  --mount source=drive_bot_data,destination=/data \
  -- python rag_indexer.py
```

Re-apply the same secrets (scheduled machines inherit app secrets automatically
on Fly). Alternatively, use a free [cron-job.org](https://cron-job.org) job that
runs `fly ssh console -C "python rag_indexer.py"` — less elegant but works.

### 8. Test in Slack

```
@Drive Bot where is the listing presentation?
```

### Fly.io cheat sheet

| Task | Command |
|---|---|
| View logs | `fly logs` |
| Restart | `fly apps restart` |
| SSH shell | `fly ssh console` |
| Update secrets | `fly secrets set KEY=value` |
| Redeploy after code change | `fly deploy` |
| Check status | `fly status` |

---

## Alternative: Railway

[Railway](https://railway.app) has a simple UI and supports Docker deploys.

1. **New Project → Deploy from GitHub** — connect this repo.
2. Railway auto-detects the `Dockerfile`.
3. Add a **Volume** mounted at `/data` (Settings → Volumes).
4. Set environment variables in **Variables** (same list as Fly secrets above).
   - For `GOOGLE_SERVICE_ACCOUNT_JSON`, paste the full JSON as the value.
   - Set `CHROMA_DB_PATH=/data/chroma_db` and `CHANGES_TOKEN_FILE=/data/changes_token.txt`.
5. Deploy. Railway keeps the service running.
6. Open a **one-off shell** (or use Railway CLI) to run `python rag_indexer.py --full`.
7. For cron: use Railway's **Cron Jobs** feature (if on a paid plan) or an
   external scheduler hitting a one-off command.

---

## Alternative: Render

[Render](https://render.com) **Background Workers** suit long-running processes.

1. **New → Background Worker** → connect repo.
2. **Environment:** Docker (uses included `Dockerfile`).
3. Add a **Persistent Disk** mounted at `/data`.
4. Set env vars (same as above).
5. Deploy.
6. Use Render **Cron Jobs** (separate service) to run `python rag_indexer.py`
   on a schedule, sharing the same disk mount.

---

## Alternative: Any VPS (DigitalOcean, Hetzner, etc.)

If you prefer a $4–6/month VM:

```bash
# On the server
git clone <repo> && cd drive-slack-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in values
# upload service_account.json via scp

# systemd (see SETUP.md)
sudo systemctl enable --now slack-drive-bot

# RAG index + cron
python rag_indexer.py --full
crontab -e
# */30 * * * * cd /path/to/drive-slack-bot && .venv/bin/python rag_indexer.py
```

This is still "cloud" — just you manage the VM instead of a PaaS.

---

## Environment variables reference (cloud)

| Variable | Required | Notes |
|---|---|---|
| `SLACK_BOT_TOKEN` | yes | `xoxb-…` |
| `SLACK_APP_TOKEN` | yes | `xapp-…` |
| `ANTHROPIC_API_KEY` | yes | |
| `OPENAI_API_KEY` | yes | For RAG embeddings |
| `SHARED_DRIVE_ID` | yes | |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | yes* | Full JSON string — preferred in cloud |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | yes* | File path — use on VPS with mounted file |
| `CHROMA_DB_PATH` | no | Default `./chroma_db`; use `/data/chroma_db` with a volume |
| `CHANGES_TOKEN_FILE` | no | Default `changes_token.txt`; use `/data/changes_token.txt` with a volume |
| `CLAUDE_MODEL` | no | Default `claude-sonnet-4-5` |

\* Provide one of the two Google credential options.

---

## What NOT to use

| Platform | Why |
|---|---|
| **Vercel / Netlify / Cloudflare Workers** | Serverless — no persistent WebSocket for Socket Mode |
| **AWS Lambda / GCP Cloud Functions** | Same — functions time out; Socket Mode needs always-on |
| **Cloud Run (scale to zero)** | WebSocket drops when instance sleeps — set `min-instances=1` if you insist |

---

## Updating the bot

After pushing code changes:

| Host | Command |
|---|---|
| Fly.io | `fly deploy` |
| Railway | Auto-deploys on git push (if enabled) |
| Render | Auto-deploys on git push |
| VPS | `git pull && sudo systemctl restart slack-drive-bot` |

---

## Troubleshooting (cloud)

| Symptom | Fix |
|---|---|
| Bot starts then crashes | `fly logs` / host logs — usually a missing env var |
| `SHARED_DRIVE_ID is not set` | Add the secret in your host's env UI |
| RAG returns nothing | Run `rag_indexer.py --full`; check volume is mounted at `/data` |
| Index resets on redeploy | Volume not attached — verify mount in host settings |
| `GOOGLE_SERVICE_ACCOUNT_JSON` parse error | Must be valid JSON on one line; escape quotes if needed |

---

## Cost summary (approximate)

| Option | Monthly cost |
|---|---|
| Fly.io (512 MB + 1 GB volume) | ~$3–5 |
| Railway (hobby + volume) | ~$5 |
| Render (background worker + disk) | ~$7 |
| VPS (Hetzner/DigitalOcean) | ~$4–6 |

The bot itself has no heavy compute. API costs (Anthropic + OpenAI embeddings)
are usage-based and typically low for a small team.
