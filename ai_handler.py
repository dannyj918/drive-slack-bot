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

import rag_retriever
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
        "name": "search",
        "description": (
            "Search the Metalios Google Workspace Shared Drive. "
            "Returns matching file links AND relevant content excerpts from those files. "
            "Call this whenever the user asks to find a file OR wants to know what a document says. "
            "You can call this tool multiple times with different search terms — "
            "try shorter, broader terms or synonyms if the first search returns nothing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Keywords to search for. "
                        "Examples: 'earnest money process', 'listing presentation', "
                        "'agent onboarding checklist', 'vendor contacts'"
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": "How many file results to fetch (1–10). Default 6.",
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
You are a helpful assistant for the Metalios real estate team.
Your job is to find files in the team's Google Workspace Shared Drive and answer questions about them.

Rules:
1. Use the search tool to find files and content. You can call it up to 4 times.
2. If the first search returns nothing or irrelevant results, try different
   keywords — shorter terms, synonyms, or the most distinctive word in the request.
3. The search tool returns two things:
   - "files": matching files with links (use these when the user wants to find/open a document)
   - "content_chunks": relevant text excerpts from documents (use these to answer questions directly)
4. If content_chunks are returned, use them to answer the question, then cite the source file.
5. If only file links are returned (no content), share the link so the user can open it.
6. Always include the source file link when answering from content.

Slack formatting rules (mrkdwn — strictly follow Slack syntax, NOT markdown):
- Use *single asterisks* for bold, NEVER **double asterisks**
- Use _underscores_ for italics
- Use bullet lists with a leading • character (Unicode bullet), or start lines with - followed by a space
- Link files as: <URL|File Name>
- Prefix each file with its type emoji: 📄 Doc  📊 Sheet  📋 Slides  📕 PDF  📎 File
- Bold the question echo: *"their question"*
- When answering from content: end with _Source: <URL|File Name>_
- When returning file links: end with _Found X file(s) in the Shared Drive_
- Maximum 4 files in the response — pick the most relevant ones
- If nothing relevant is found after searching, say so clearly with
  a suggestion to use different keywords

Do NOT add preamble like "Sure!" or "I found the following…" — get straight to the answer or files.\
"""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_response(question: str, _files: list = None, history: list = None) -> str:
    """
    Run an agentic search loop: Claude decides what to search for,
    calls search_drive as many times as it needs, and returns a
    Slack-formatted message.

    The `_files` parameter is accepted for compatibility but ignored —
    the agentic loop does its own searching.
    """
    messages = (history or []) + [
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

                if block.name == "search":
                    query       = block.input.get("query", question)
                    max_results = block.input.get("max_results", 6)
                    logger.info("Agentic search [round %d]: %r", rounds + 1, query)

                    # Drive file search — returns file links and metadata
                    try:
                        files = search_shared_drive(query, max_results=max_results)
                        file_results = [
                            {
                                "name":     f.get("name"),
                                "type":     f.get("label"),
                                "emoji":    f.get("emoji"),
                                "link":     f.get("webViewLink"),
                                "modified": (f.get("modifiedTime") or "")[:10],
                            }
                            for f in files
                        ]
                    except Exception as exc:
                        logger.error("Drive search error: %s", exc)
                        file_results = []

                    # RAG knowledge base search — returns content chunks with source links
                    try:
                        chunks = rag_retriever.search(query)
                    except Exception as exc:
                        logger.error("RAG search error: %s", exc)
                        chunks = []

                    combined = {"files": file_results, "content_chunks": chunks}
                    if not file_results and not chunks:
                        combined["note"] = "No results — try different keywords"

                    result_content = json.dumps(combined)

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

    # Rounds exhausted — give Claude one final turn to answer from accumulated results
    try:
        final = _get_client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=_SYSTEM,
            tools=_TOOLS,
            tool_choice={"type": "none"},
            messages=messages,
        )
        for block in final.content:
            if hasattr(block, "text"):
                return block.text.strip()
    except Exception as exc:
        logger.error("Final synthesis call failed: %s", exc)

    return (
        f"I searched the drive several times for *\"{_escape(question)}\"* "
        "but couldn't find a confident match. "
        "Try rephrasing with the document's specific title or type."
    )


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
