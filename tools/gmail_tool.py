"""
Gmail tool — read-only search over the user's inbox.

Setup required before this works:
  1. Enable Gmail API in Google Cloud Console
  2. Create OAuth 2.0 credentials (Desktop app), save as credentials.json in project root
  3. First run will open a browser to authorize; a token.json will be saved for future runs

Scope used is READ-ONLY on purpose — this tool should never be able to send/delete mail.
"""

import os
import base64
from datetime import datetime, timedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _get_gmail_service():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token_file:
            token_file.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _extract_body(payload) -> str:
    """Pulls plain text body out of a Gmail message payload (handles multipart)."""
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain":
                data = part["body"].get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
        # fallback: recurse into nested parts
        for part in payload["parts"]:
            text = _extract_body(part)
            if text:
                return text
    else:
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
    return ""


async def search_emails(query: str, hours_back: int | None = None) -> list[str]:
    """
    Searches Gmail using the given query, optionally restricted to the last
    `hours_back` hours. If hours_back is None, searches without a time filter
    (useful for queries with no time element).

    Gmail's search syntax only supports day-level granularity, so for precise
    windows like "past 3 hours" we fetch a slightly wider day-level window
    then filter precisely using each message's actual timestamp.

    Returns a list of short summaries: "From: X | Subject: Y | Snippet: Z"
    """
    service = _get_gmail_service()

    gmail_query = query
    cutoff_ms = None

    if hours_back:
        cutoff_time = datetime.now() - timedelta(hours=hours_back)
        cutoff_ms = int(cutoff_time.timestamp() * 1000)
        # widen to at least 1 day for Gmail's day-level query filter, we'll
        # filter precisely below using the real timestamp
        after_date = (cutoff_time - timedelta(days=1)).strftime("%Y/%m/%d")
        gmail_query = f"{query} after:{after_date}".strip()

    results = service.users().messages().list(
        userId="me", q=gmail_query, maxResults=15
    ).execute()

    messages = results.get("messages", [])
    summaries = []

    for msg_ref in messages:
        msg = service.users().messages().get(
            userId="me", id=msg_ref["id"], format="full"
        ).execute()

        if cutoff_ms and int(msg.get("internalDate", 0)) < cutoff_ms:
            continue  # older than the precise window requested

        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        sender = headers.get("From", "unknown")
        subject = headers.get("Subject", "(no subject)")
        snippet = msg.get("snippet", "")

        summaries.append(f"From: {sender} | Subject: {subject} | {snippet}")

    if not summaries:
        return ["No emails found matching that query/time window."]

    return summaries