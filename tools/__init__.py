"""Active-plugin registry for the command-line agent."""

from agents import run_github_agent, run_gmail_agent, run_notion_agent, run_obsidian_agent


async def _gmail_agent_tool(sub_question: str, **args) -> list[str]:
    return await run_gmail_agent(sub_question, args)


async def _github_agent_tool(sub_question: str, **args) -> list[str]:
    return await run_github_agent(sub_question, args)


async def _notion_agent_tool(sub_question: str, **args) -> list[str]:
    return await run_notion_agent(sub_question, args)


async def _obsidian_agent_tool(sub_question: str, **args) -> list[str]:
    return await run_obsidian_agent(sub_question, args)


AVAILABLE_TOOLS = {
    "gmail_agent": {
        "label": "Gmail",
        "description": "gmail_agent(query: str, hours_back: int | None) -> read-only Gmail agent. It chooses at most two searches and retries only when evidence is thin.",
        "fn": _gmail_agent_tool,
        "requires": [],
    },
    "github_agent": {
        "label": "GitHub",
        "description": "github_agent(query: str, repo: str | None, hours_back: int | None) -> read-only GitHub agent. It chooses recent activity, semantic code retrieval, or both and makes at most one fallback.",
        "fn": _github_agent_tool,
        "requires": ["repo"],
    },
    "notion_agent": {
        "label": "Notion",
        "description": "notion_agent(query: str) -> read-only Notion agent. It searches pages accessible to NOTION_TOKEN, retrieves page text, and makes at most two search attempts.",
        "fn": _notion_agent_tool,
        "requires": [],
    },
    "obsidian_agent": {
        "label": "Obsidian",
        "description": "obsidian_agent(query: str) -> read-only agent. It searches Markdown notes in OBSIDIAN_VAULT_PATH and makes at most two search attempts.",
        "fn": _obsidian_agent_tool,
        "requires": [],
    },
}
