"""Read-only MCP server for this project's live-data retrieval tools."""

from mcp.server.fastmcp import FastMCP

from tools.gmail_tool import search_emails
from tools.github_tool import explore_codebase, search_github
from tools.notion_tool import search_notion
from tools.obsidian_tool import search_obsidian

mcp = FastMCP("Live Data RAG")


@mcp.tool()
async def gmail_search_messages(query: str, hours_back: int | None = None) -> list[str]:
    """Read-only Gmail search, optionally limited to the last N hours."""
    return await search_emails(query, hours_back)


@mcp.tool()
async def github_search_activity(repo: str, query: str, hours_back: int | None = None) -> list[str]:
    """Read-only search of a repository's commits, issues, and pull requests."""
    return await search_github(query, repo, hours_back)


@mcp.tool()
async def github_retrieve_code_context(repo: str, query: str) -> list[str]:
    """Read-only semantic retrieval of files relevant to a repository question."""
    return await explore_codebase(query, repo)


@mcp.tool()
async def notion_search_pages(query: str) -> list[str]:
    """Read-only search of pages accessible to the configured Notion token."""
    return await search_notion(query)


@mcp.tool()
async def obsidian_search_notes(query: str) -> list[str]:
    """Read-only search of Markdown notes in OBSIDIAN_VAULT_PATH."""
    return await search_obsidian(query)


if __name__ == "__main__":
    mcp.run(transport="stdio")
