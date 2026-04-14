"""
Google Drive search module
==========================
Searches a specific Google Workspace Shared Drive using a service account.

PREREQUISITE SETUP (one-time):
  1. Google Cloud Console → APIs & Services → Enable "Google Drive API"
  2. Create a Service Account (IAM & Admin → Service Accounts → Create)
  3. Download the JSON key → save as service_account.json next to this file
  4. In Google Drive, open your Shared Drive → Manage members
     → Add the service account email (e.g. bot@project.iam.gserviceaccount.com)
     with at least "Viewer" access
  5. Find your Shared Drive ID from the URL:
     drive.google.com/drive/folders/{THIS_IS_YOUR_DRIVE_ID}
     Put it in SHARED_DRIVE_ID in your .env file
"""

import os
import logging
from typing import Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2 import service_account

logger = logging.getLogger(__name__)

# Read-only access is all we need
_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Emoji + label for known MIME types
_MIME_META: dict[str, tuple[str, str]] = {
    "application/vnd.google-apps.document":     ("📄", "Google Doc"),
    "application/vnd.google-apps.spreadsheet":  ("📊", "Google Sheet"),
    "application/vnd.google-apps.presentation": ("📋", "Google Slides"),
    "application/vnd.google-apps.form":         ("📝", "Google Form"),
    "application/vnd.google-apps.folder":       ("📁", "Folder"),
    "application/pdf":                          ("📕", "PDF"),
    # Microsoft Office formats (common in Drive)
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document":     ("📄", "Word Doc"),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":           ("📊", "Excel Sheet"),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation":   ("📋", "PowerPoint"),
}


def _build_service():
    """Create an authenticated Drive v3 service using the service account."""
    key_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    creds = service_account.Credentials.from_service_account_file(
        key_file, scopes=_SCOPES
    )
    # cache_discovery=False avoids stale discovery doc issues in long-running processes
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def search_shared_drive(query: str, max_results: int = 8) -> list[dict]:
    """
    Full-text search across the configured Shared Drive.

    Parameters
    ----------
    query       : Natural language question or keyword string from the user.
    max_results : Maximum number of files to return (default 8, Claude picks top 4).

    Returns
    -------
    List of file dicts, each with keys:
        id, name, mimeType, webViewLink, modifiedTime,
        description (may be empty), emoji, label
    """
    drive_id = os.environ.get("SHARED_DRIVE_ID", "").strip()
    if not drive_id:
        raise ValueError(
            "SHARED_DRIVE_ID is not set. "
            "Add it to your .env file — see .env.example for instructions."
        )

    # Escape single quotes so the Drive query doesn't break
    safe_query = query.replace("\\", "\\\\").replace("'", "\\'")
    drive_filter = f"fullText contains '{safe_query}' and trashed = false"

    try:
        service = _build_service()
        response = (
            service.files()
            .list(
                q=drive_filter,
                # corpora="drive" + driveId scopes the search to the Shared Drive only
                corpora="drive",
                driveId=drive_id,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                pageSize=max_results,
                fields=(
                    "files("
                    "id, name, mimeType, webViewLink, "
                    "modifiedTime, description"
                    ")"
                ),
                # Most-recently-modified first — usually the most relevant version
                orderBy="modifiedTime desc",
            )
            .execute()
        )
    except HttpError as exc:
        logger.error("Google Drive API HTTP error: %s", exc)
        raise

    files = response.get("files", [])

    # Attach display helpers
    for f in files:
        emoji, label = _MIME_META.get(f.get("mimeType", ""), ("📎", "File"))
        f["emoji"] = emoji
        f["label"] = label

    logger.info("Drive search %r → %d result(s)", query, len(files))
    return files
