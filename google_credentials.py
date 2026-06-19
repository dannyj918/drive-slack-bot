"""
Google service account credentials
==================================
Loads credentials from a JSON key file or an inline JSON env var (for cloud deploy).
"""

import json
import os

from google.oauth2 import service_account

_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def get_drive_credentials():
    """
    Return Drive API credentials from GOOGLE_SERVICE_ACCOUNT_JSON or
    GOOGLE_SERVICE_ACCOUNT_FILE (default: service_account.json).
    """
    json_blob = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if json_blob:
        info = json.loads(json_blob)
        return service_account.Credentials.from_service_account_info(
            info, scopes=_SCOPES
        )

    key_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    return service_account.Credentials.from_service_account_file(
        key_file, scopes=_SCOPES
    )
