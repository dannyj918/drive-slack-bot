"""
Slack Drive Bot
===============
AI assistant that answers natural language questions by searching
the Metalios Google Workspace Shared Drive.

Usage:
  In any channel — @mention the bot:
    @bot Where's the Q1 budget template?
    @bot Find the listing presentation deck

  In a DM — just message it directly (no @mention needed):
    Where's the listing presentation?
    Find the agent onboarding checklist

The bot searches the Shared Drive and replies in-thread with
direct links to matching files.

Run:
  python bot.py
"""

import os
import re
import logging

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from dotenv import load_dotenv

from drive_search import search_shared_drive
from ai_handler import build_response, build_web_response, SearchResult
from conversation import fetch_thread_history

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Slack app
# ---------------------------------------------------------------------------

app = App(token=os.environ["SLACK_BOT_TOKEN"])


# ---------------------------------------------------------------------------
# Slash command: /help — ephemeral, only visible to the person who typed it
# ---------------------------------------------------------------------------

@app.command("/help")
def handle_help(ack, respond, command, client):
    ack()
    question = command.get("text", "").strip()
    user_id  = command["user_id"]

    if not question:
        respond(
            response_type="ephemeral",
            text=(
                "🔍 *What are you looking for?*\n"
                "Type `/help` followed by your question — I'll DM you the results so you can follow up:\n\n"
                "> `/help find the Q1 budget template`\n"
                "> `/help listing presentation deck`\n"
                "> `/help agent onboarding checklist`"
            ),
        )
        return

    # Acknowledge in-channel (ephemeral), then send the real result to the user's DM
    # so they can continue the conversation by replying there.
    respond(response_type="ephemeral", text="🔍 Searching the drive… I'll send the results to your DMs.")
    result = _search_and_respond(question)

    dm = client.conversations_open(users=user_id)
    dm_channel = dm["channel"]["id"]

    if result.no_results:
        client.chat_postMessage(
            channel=dm_channel,
            blocks=_web_search_offer_blocks(question),
            text=result.text,
            mrkdwn=True,
        )
    else:
        client.chat_postMessage(
            channel=dm_channel,
            text=f"Results for `/help {question}`:\n\n{result.text}\n\n_Reply here to follow up._",
            mrkdwn=True,
        )


# ---------------------------------------------------------------------------
# Event: app_mention — fires whenever someone @mentions the bot
# ---------------------------------------------------------------------------

@app.event("app_mention")
def handle_app_mention(event, say, client):
    channel   = event["channel"]
    event_ts  = event["ts"]
    # Reply inside an existing thread if the mention is already in one,
    # otherwise start a new thread anchored to the mention.
    thread_ts = event.get("thread_ts", event_ts)
    user_id   = event["user"]

    # Strip the @bot mention(s) to get the raw question
    question = re.sub(r"<@[A-Z0-9]+>", "", event["text"]).strip()

    if not question:
        say(
            text=(
                "Hey! 👋 Ask me to find something in the Shared Drive — for example:\n"
                "• _Find the Q1 budget template_\n"
                "• _Where's the listing presentation deck?_\n"
                "• _Show me the agent onboarding checklist_"
            ),
            channel=channel,
            thread_ts=thread_ts,
        )
        return

    logger.info("Drive search | user=%s | query=%r", user_id, question)

    # Show the 👀 reaction so the user knows we're working on it
    try:
        client.reactions_add(channel=channel, name="eyes", timestamp=event_ts)
    except Exception:
        pass  # Non-critical — carry on if reactions aren't enabled

    result = _search_and_respond(question)

    # Swap 👀 for ✅
    try:
        client.reactions_remove(channel=channel, name="eyes", timestamp=event_ts)
        client.reactions_add(channel=channel, name="white_check_mark", timestamp=event_ts)
    except Exception:
        pass

    if result.no_results:
        say(blocks=_web_search_offer_blocks(question), text=result.text, channel=channel, thread_ts=thread_ts)
    else:
        say(text=result.text, channel=channel, thread_ts=thread_ts)


def _search_and_respond(question: str, history: list = None) -> SearchResult:
    """Run the Drive search and format a Slack-ready response."""
    try:
        files = search_shared_drive(question)
        return build_response(question, files, history=history)
    except Exception as e:
        logger.error("Error during drive search: %s", e, exc_info=True)
        return SearchResult(
            "⚠️ Sorry, I hit an error searching the drive. "
            "Please try again in a moment, or contact your workspace admin."
        )


def _web_search_offer_blocks(question: str) -> list[dict]:
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"I searched the Shared Drive for *\"{question[:200]}\"* "
                    "but couldn't find a confident match.\n\n"
                    "Would you like me to search the web instead?"
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Search the web"},
                    "style": "primary",
                    "action_id": "web_search_confirm",
                    "value": question[:1900],
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "No thanks"},
                    "action_id": "web_search_decline",
                    "value": "dismiss",
                },
            ],
        },
    ]


# ---------------------------------------------------------------------------
# Event: assistant_thread_started — fires when a user opens the AI assistant tab
# ---------------------------------------------------------------------------

@app.event("assistant_thread_started")
def handle_assistant_thread_started(event, say):
    say("👋 Hi! Ask me anything — I'll search the Metalios Shared Drive and find what you need.")


# ---------------------------------------------------------------------------
# Event: message in DMs — fires when someone messages the bot directly
# ---------------------------------------------------------------------------

@app.event("message")
def handle_dm(event, say, client):
    # Only handle direct messages (channel_type "im"), ignore everything else
    if event.get("channel_type") != "im":
        return

    # Ignore bot messages and message edits/deletes
    if event.get("bot_id") or event.get("subtype"):
        return

    question = event.get("text", "").strip()
    user_id  = event["user"]
    channel  = event["channel"]
    event_ts = event["ts"]

    if not question:
        say(
            text=(
                "Hey! 👋 Just ask me what you're looking for — for example:\n"
                "• _Find the Q1 budget template_\n"
                "• _Where's the listing presentation deck?_\n"
                "• _Show me the agent onboarding checklist_"
            ),
            channel=channel,
        )
        return

    thread_ts = event.get("thread_ts")
    history   = fetch_thread_history(client, channel, thread_ts) if thread_ts else None

    logger.info("DM Drive search | user=%s | query=%r | thread=%s", user_id, question, bool(thread_ts))

    try:
        client.reactions_add(channel=channel, name="eyes", timestamp=event_ts)
    except Exception:
        pass

    result = _search_and_respond(question, history=history)

    try:
        client.reactions_remove(channel=channel, name="eyes", timestamp=event_ts)
        client.reactions_add(channel=channel, name="white_check_mark", timestamp=event_ts)
    except Exception:
        pass

    thread_kwargs = {"thread_ts": thread_ts} if thread_ts else {}
    if result.no_results:
        say(blocks=_web_search_offer_blocks(question), text=result.text, channel=channel, **thread_kwargs)
    else:
        say(text=result.text, channel=channel, **thread_kwargs)


# ---------------------------------------------------------------------------
# Actions: web search offer buttons
# ---------------------------------------------------------------------------

@app.action("web_search_confirm")
def handle_web_search_confirm(ack, body, client):
    ack()
    question   = body["actions"][0]["value"]
    channel    = body["container"]["channel_id"]
    message_ts = body["container"]["message_ts"]
    thread_ts  = body["container"].get("thread_ts", message_ts)

    client.chat_update(channel=channel, ts=message_ts, text="Searching the web\u2026", blocks=[])

    response_text = build_web_response(question)
    client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=response_text, mrkdwn=True)


@app.action("web_search_decline")
def handle_web_search_decline(ack, body, client):
    ack()
    channel    = body["container"]["channel_id"]
    message_ts = body["container"]["message_ts"]
    original   = next(
        (b["text"]["text"] for b in body["message"].get("blocks", []) if b["type"] == "section"),
        body["message"].get("text", ""),
    )
    client.chat_update(
        channel=channel,
        ts=message_ts,
        text=original,
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": original}}],
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("Starting Slack Drive Bot (Socket Mode)…")
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
