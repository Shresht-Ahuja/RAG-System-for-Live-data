"""Read-only Notion retrieval using the public REST API."""

import os
from typing import Any

import httpx

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"
MAX_RESULTS = 8
MAX_BLOCKS_PER_PAGE = 20


def _headers() -> dict[str, str]:
    token = os.environ.get("NOTION_TOKEN")
    return {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION, "Content-Type": "application/json"}


def _plain_text(value: Any) -> str:
    if isinstance(value, list):
        return "".join(_plain_text(item) for item in value)
    if isinstance(value, dict):
        return str(value.get("plain_text") or value.get("content") or "")
    return ""


def _page_title(page: dict[str, Any]) -> str:
    for prop in page.get("properties", {}).values():
        title = _plain_text(prop.get("title", []))
        if title:
            return title
    return page.get("url", "Untitled Notion page")


def _block_text(block: dict[str, Any]) -> str:
    payload = block.get(block.get("type", ""), {})
    return _plain_text(payload.get("rich_text", [])) if isinstance(payload, dict) else ""


async def search_notion(query: str) -> list[str]:
    """Search accessible Notion pages and return short, read-only excerpts."""
    if not os.environ.get("NOTION_TOKEN"):
        return ["[error] NOTION_TOKEN is not configured."]
    async with httpx.AsyncClient(headers=_headers(), timeout=12) as client:
        response = await client.post(f"{NOTION_API}/search", json={"query": query, "page_size": MAX_RESULTS})
        if response.status_code == 401:
            return ["[error] Notion token is invalid or unauthorized."]
        if response.status_code == 429:
            return ["[error] Notion rate limit reached; retry later."]
        if response.status_code != 200:
            return [f"[error] Notion search failed: HTTP {response.status_code}."]
        pages = [item for item in response.json().get("results", []) if item.get("object") == "page"]
        if not pages:
            return ["No Notion pages found matching that query."]
        excerpts: list[str] = []
        for page in pages:
            blocks_response = await client.get(f"{NOTION_API}/blocks/{page['id']}/children", params={"page_size": MAX_BLOCKS_PER_PAGE})
            text = ""
            if blocks_response.status_code == 200:
                text = "\n".join(item for block in blocks_response.json().get("results", []) if (item := _block_text(block)))
            excerpts.append(f"=== Notion: {_page_title(page)} ===\n{text[:4000] or '[No readable text blocks returned]'}")
    return excerpts
