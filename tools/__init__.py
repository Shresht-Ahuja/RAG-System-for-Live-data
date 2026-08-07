"""Tool registry for the command-line agent.

Raw Gmail/GitHub retrieval is also exposed by ``mcp_server.py``.  The CLI uses
these bounded source agents so it can adapt its search strategy without an
unbounded multi-agent loop.
"""

from agents import run_github_agent, run_gmail_agent


async def _gmail_agent_tool(sub_question: str, **args) -> list[str]:
    return await run_gmail_agent(sub_question, args)


async def _github_agent_tool(sub_question: str, **args) -> list[str]:
    return await run_github_agent(sub_question, args)


AVAILABLE_TOOLS = {
    "gmail_agent": {
        "label": "Gmail",
        "description": (
            "gmail_agent(query: str, hours_back: int | None) -> read-only Gmail agent. "
            "It chooses up to two search queries, observes results, and retries only "
            "when the first search is thin. Set hours_back from any user time window."
        ),
        "fn": _gmail_agent_tool,
        "requires": [],
    },
    "github_agent": {
        "label": "GitHub",
        "description": (
            "github_agent(query: str, repo: str | None, hours_back: int | None) -> "
            "read-only GitHub agent. It decides whether recent activity, semantic "
            "codebase retrieval, or both are needed; after thin single-mode evidence, "
            "it tries the complementary mode once."
        ),
        "fn": _github_agent_tool,
        "requires": ["repo"],
    },
}
