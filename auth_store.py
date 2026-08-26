"""Encrypted, per-user OAuth connection storage.

This module intentionally never falls back to .env provider tokens.  The web
application must receive an explicit per-user OAuth authorization first.
"""

import base64
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

import httpx
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

load_dotenv()
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "data/live_data_agent.db"))
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _fernet() -> Fernet:
    key = os.getenv("TOKEN_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY must be configured before starting the web app.")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY must be a valid Fernet key.") from exc


def initialize_store() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def upsert_user(user_id: str, email: str, name: str | None) -> None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            """INSERT INTO users (id, email, name, created_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET email=excluded.email, name=excluded.name""",
            (user_id, email, name, int(time.time())),
        )


def get_user(user_id: str) -> dict[str, str | None] | None:
    """Return non-sensitive user details for the signed-in user's dashboard."""
    with sqlite3.connect(DATABASE_PATH) as connection:
        row = connection.execute(
            "SELECT email, name FROM users WHERE id=?", (user_id,)
        ).fetchone()
    return {"email": row[0], "name": row[1]} if row else None


def save_connection(user_id: str, provider: str, credentials: dict[str, Any], metadata: dict[str, Any] | None = None) -> None:
    expires_at = credentials.get("expires_at")
    encrypted = _fernet().encrypt(json.dumps(credentials).encode())
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            """INSERT INTO connections (user_id, provider, encrypted_credentials, expires_at, metadata, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, provider) DO UPDATE SET encrypted_credentials=excluded.encrypted_credentials,
            expires_at=excluded.expires_at, metadata=excluded.metadata, updated_at=excluded.updated_at""",
            (user_id, provider, encrypted, expires_at, json.dumps(metadata or {}), int(time.time())),
        )


def get_connection(user_id: str, provider: str) -> dict[str, Any] | None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        row = connection.execute(
            "SELECT encrypted_credentials, metadata FROM connections WHERE user_id=? AND provider=?",
            (user_id, provider),
        ).fetchone()
    if not row:
        return None
    try:
        credentials = json.loads(_fernet().decrypt(row[0]).decode())
    except (InvalidToken, json.JSONDecodeError) as exc:
        raise RuntimeError("Stored OAuth credentials cannot be decrypted.") from exc
    credentials["metadata"] = json.loads(row[1])
    return credentials


def connection_status(user_id: str) -> dict[str, bool]:
    providers = ("gmail", "github", "notion")
    with sqlite3.connect(DATABASE_PATH) as connection:
        rows = connection.execute("SELECT provider FROM connections WHERE user_id=?", (user_id,)).fetchall()
    connected = {row[0] for row in rows}
    return {provider: provider in connected for provider in providers}


def _basic_auth(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode()
    return base64.b64encode(raw).decode()


async def get_access_token(user_id: str, provider: str) -> str | None:
    """Return a valid provider token, refreshing it when its expiry is near."""
    credentials = get_connection(user_id, provider)
    if not credentials:
        return None
    expires_at = credentials.get("expires_at")
    if not expires_at or expires_at > int(time.time()) + 60:
        return credentials.get("access_token")
    refresh_token = credentials.get("refresh_token")
    if not refresh_token:
        return None

    if provider == "gmail":
        token_url = "https://oauth2.googleapis.com/token"
        payload = {
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        headers = None
    elif provider == "notion":
        token_url = "https://api.notion.com/v1/oauth/token"
        payload = {"grant_type": "refresh_token", "refresh_token": refresh_token}
        headers = {
            "Authorization": f"Basic {_basic_auth(os.environ['NOTION_CLIENT_ID'], os.environ['NOTION_CLIENT_SECRET'])}",
            "Notion-Version": "2026-03-11",
        }
    else:
        return credentials.get("access_token")

    async with httpx.AsyncClient(timeout=12) as client:
        response = await client.post(token_url, data=payload if provider == "gmail" else None, json=payload if provider == "notion" else None, headers=headers)
    if response.status_code != 200:
        return None
    refreshed = response.json()
    credentials["access_token"] = refreshed["access_token"]
    credentials["refresh_token"] = refreshed.get("refresh_token", refresh_token)
    credentials["expires_at"] = int(time.time()) + int(refreshed.get("expires_in", 3600))
    credentials.pop("metadata", None)
    save_connection(user_id, provider, credentials)
    return credentials["access_token"]
