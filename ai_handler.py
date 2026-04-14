"""
AI handler — agentic search
============================
Claude receives the user's question and a `search_drive` tool it can call
multiple times with different search terms. This means:

  - Vague queries ("that onboarding thing") still find the right file
  - Claude tries synonyms/variations if the first search is empty
  - Claude can cross-reference results across searches before responding
  - No separate MCP server process needed — same intelligent behaviour

Model: claude-sonnet-4-6  (better reasoning for ambiguous queries)
       Swap to claude-haiku-4-5-20251001 if speed/cost is a priority.
"""

import json
import logging
import os

import anthropic

from drive_search import search_shared_drive

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None

MAX_SEARCH_ROUNDS = 4   # Claude can call search_drive up to this many times
CLAUDE_MODEL      = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5")


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


# ---------------------------------------------------------------------------
# Tool definition — what Claude is allowed to call
# ---------------------------------------------------------------------------

_TOOLS = [
    {
        "name": "search_drive",
        "description": (
            "Search the Metalios Google Workspace Shared Drive for files. "
            "You can call this tool multiple times with different search terms. "
            "Try shorter, broader terms if an initial search returns nothing. "
            "Try synonyms or alternate phrasings for vague questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Keywords to search for. "
                        "Examples: 'listing presentation', 'agent onboarding checklist', "
                        "'Q1 budget 2024', 'team member agreement'"
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": "How many results to fetch (1–10). Default 6.",
                    "default": 6,
                },
            },
            "required": ["query"],
        },
    }
]


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM = """\
You are a helpful file-finder assistant for the Metalios real estate team.
Your only job is to find files in the team's Google Workspace Shared Drive.

Rules:
1. Use the search_drive tool to find files. You can call it up to 4 times.
2. If the first search returns nothing or irrelevant results, try different
   keywords — shorter terms, synonyms, or the most distinctive word in the request.
3. Once you have good results, write a concise Slack response.

Slack formatting rules (mrkdwn):
- Link files as: <URL|File Name>
- Prefix each file with its type emoji: 📄 Doc  📊 Sheet  📋 Slides  📕 PDF  📎 File
- Bold the question echo: *"their question"*
- End with an italicised count: _Found X file(s) in the Shared Drive_
- Maximum 4 files in the response — pick the most relevant ones
- If nothing relevant is found after searching, say so clearly with
  a suggestion to use different keywords

Do NOT add preamble like "Sure!" or "I found the following…" — get straight to the files.\
"""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_response(question: str, _files: list = None) -> str:
    """
    Run an agentic search loop: Claude decides what to search for,
    calls search_drive as many times as it needs, and returns a
    Slack-formatted message.

    The `_files` parameter is accepted for compatibility but ignored —
    the agentic loop does its own searching.
    """
    messages = [
        {
            "role": "user",
            "content": (
                f'A Metalios team member asked: "{question}"\n\n'
                "Find the most relevant files in the Shared Drive and reply."
            ),
        }
    ]

    rounds = 0

    while rounds < MAX_SEARCH_ROUNDS:
        response = _get_client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=_SYSTEM,
            tools=_TOOLS,
            messages=messages,
        )

        # Claude is done — extract the final text response
        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text.strip()
            return "I searched the drive but couldn't put together a response. Please try again."

        # Claude wants to call a tool
        if response.stop_reason == "tool_use":
            tool_results = []

            for block in response.content:
                if block.type != "tool_use":
                    continue

                if block.name == "search_drive":
                    query       = block.input.get("query", question)
                    max_results = block.input.get("max_results", 6)
                    logger.info("Agentic Drive search [round %d]: %r", rounds + 1, query)

                    try:
                        files = search_shared_drive(query, max_results=max_results)
                        # Give Claude the raw file metadata so it can reason about relevance
                        result_content = json.dumps(
                            [
                                {
                                    "name":     f.get("name"),
                                    "type":     f.get("label"),
                                    "emoji":    f.get("emoji"),
                                    "link":     f.get("webViewLink"),
                                    "modified": (f.get("modifiedTime") or "")[:10],
                                }
                                for f in files
                            ]
                        )
                        if not files:
                            result_content = "[]  # No results — try different keywords"

                    except Exception as exc:
                        logger.error("Drive search error: %s", exc)
                        result_content = f"Error searching Drive: {exc}"

                    tool_results.append(
                        {
                            "type":        "tool_result",
                            "tool_use_id": block.id,
                            "content":     result_content,
                        }
                    )

            # Feed tool results back into the conversation
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user",      "content": tool_results})
            rounds += 1

        else:
            # Unexpected stop reason
            break

    return (
        f"I searched the drive several times for *\"{_escape(question)}\"* "
        "but couldn't find a confident match. "
        "Try rephrasing with the document's specific title or type."
    )


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
