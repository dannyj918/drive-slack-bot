"""
conversation.py — Slack thread history → Claude message format
"""

import logging

logger = logging.getLogger(__name__)

_bot_user_id: str | None = None


def _get_bot_user_id(client) -> str:
    global _bot_user_id
    if _bot_user_id is None:
        _bot_user_id = client.auth_test()["user_id"]
    return _bot_user_id


def fetch_thread_history(client, channel: str, thread_ts: str) -> list[dict]:
    """
    Fetch prior messages from a Slack thread and return them as a Claude
    messages list (oldest first, excluding the most recent message which
    will be passed as the current question).
    """
    try:
        result = client.conversations_replies(channel=channel, ts=thread_ts)
        messages = result.get("messages", [])
    except Exception as exc:
        logger.error("Failed to fetch thread history: %s", exc)
        return []

    bot_id = _get_bot_user_id(client)
    history = []

    for msg in messages[:-1]:  # exclude the current (last) message
        text = msg.get("text", "").strip()
        if not text:
            continue

        if msg.get("user") == bot_id:
            # Skip transient status messages
            if "Searching the drive" in text:
                continue
            history.append({"role": "assistant", "content": text})
        else:
            history.append({"role": "user", "content": text})

    return history
