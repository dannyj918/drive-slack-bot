"""
slack_files.py — Download image attachments from Slack message events.
"""

from __future__ import annotations

import base64
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

# Anthropic vision-supported MIME types
_SUPPORTED_IMAGE_TYPES = frozenset({
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
})

# Anthropic rejects images larger than 5 MB
_MAX_IMAGE_BYTES = 5 * 1024 * 1024

# Cap how many images we send per message
_MAX_IMAGES = 3


def extract_image_files(event: dict) -> list[dict]:
    """
    Return image file metadata dicts from a Slack message event.

    Looks at event["files"] (file_share and messages with attachments).
    """
    files = event.get("files") or []
    images = []
    for f in files:
        mime = (f.get("mimetype") or "").lower()
        if mime in _SUPPORTED_IMAGE_TYPES:
            images.append(f)
        elif mime.startswith("image/"):
            logger.info("Skipping unsupported image type %s (%s)", mime, f.get("name"))
    return images[:_MAX_IMAGES]


def download_slack_images(event: dict, token: str) -> list[dict]:
    """
    Download image attachments from a Slack event.

    Returns a list of dicts:
        {"media_type": str, "data": str (base64), "name": str}
    """
    images = []
    for f in extract_image_files(event):
        url = f.get("url_private_download") or f.get("url_private")
        name = f.get("name") or f.get("id") or "image"
        mime = (f.get("mimetype") or "image/jpeg").lower()
        size = f.get("size") or 0

        if not url:
            logger.warning("Slack file %r has no download URL — skipping", name)
            continue

        if size and size > _MAX_IMAGE_BYTES:
            logger.warning(
                "Slack image %r is %d bytes (max %d) — skipping",
                name, size, _MAX_IMAGE_BYTES,
            )
            continue

        try:
            raw = _download(url, token)
        except Exception as exc:
            logger.error("Failed to download Slack image %r: %s", name, exc)
            continue

        if len(raw) > _MAX_IMAGE_BYTES:
            logger.warning(
                "Downloaded image %r is %d bytes (max %d) — skipping",
                name, len(raw), _MAX_IMAGE_BYTES,
            )
            continue

        images.append({
            "media_type": mime,
            "data": base64.standard_b64encode(raw).decode("ascii"),
            "name": name,
        })
        logger.info("Downloaded Slack image %r (%d bytes, %s)", name, len(raw), mime)

    return images


def _download(url: str, token: str) -> bytes:
    """Fetch a Slack private file URL using the bot token."""
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} downloading Slack file") from exc
