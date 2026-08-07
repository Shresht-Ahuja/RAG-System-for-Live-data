"""Read-only search over a local or mounted Obsidian Markdown vault."""

import os
import re
from pathlib import Path

MAX_NOTES = 8
MAX_NOTE_CHARS = 4000
SKIP_DIRS = {".obsidian", ".git", ".trash", "node_modules"}


def _terms(query: str) -> list[str]:
    return [term.lower() for term in re.findall(r"[A-Za-z0-9_'-]{3,}", query)]


async def search_obsidian(query: str) -> list[str]:
    """Return relevant Markdown notes from OBSIDIAN_VAULT_PATH."""
    vault_value = os.environ.get("OBSIDIAN_VAULT_PATH")
    if not vault_value:
        return ["[error] OBSIDIAN_VAULT_PATH is not configured."]
    vault = Path(vault_value).expanduser().resolve()
    if not vault.is_dir():
        return ["[error] OBSIDIAN_VAULT_PATH does not point to a readable directory."]
    terms = _terms(query)
    candidates: list[tuple[int, Path, str]] = []
    for path in vault.rglob("*.md"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            if path.stat().st_size > 1_000_000:
                continue
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        haystack, filename = content.lower(), path.stem.lower()
        score = sum(haystack.count(term) + (5 if term in filename else 0) for term in terms)
        if score:
            candidates.append((score, path, content))
    if not candidates:
        return ["No Obsidian notes found matching that query."]
    candidates.sort(key=lambda item: (-item[0], str(item[1]).lower()))
    return [f"=== Obsidian: {path.relative_to(vault)} ===\n{content[:MAX_NOTE_CHARS]}" for _, path, content in candidates[:MAX_NOTES]]
