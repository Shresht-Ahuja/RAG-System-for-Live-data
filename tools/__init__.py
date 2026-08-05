"""
Tool Registry — this is the "plugin system" for the agent.

Each tool is registered here with metadata the Planner needs (name +
description) and the actual async function that executes it. To add a
new plugin (Jira, Notion, Slack, etc.), just add an entry to AVAILABLE_TOOLS
below — nothing else in the pipeline needs to change.
"""

from tools.gmail_tool import search_emails
from tools.github_tool import search_github, explore_codebase

# Each entry: name -> {label (shown to user), description (for the planner),
#                       fn (the actual tool), requires (session config keys needed)}
AVAILABLE_TOOLS = {
    "search_emails": {
        "label": "Gmail",
        "description": (
            "search_emails(query: str, hours_back: int | None) -> searches Gmail. "
            "Set hours_back based on the time window implied by the user's question "
            "(e.g. 'past 3 hours' -> 3, '2 days' -> 48). Omit/null if no time element."
        ),
        "fn": search_emails,
        "requires": [],  # uses OAuth token, no extra session config needed
    },
    "search_github": {
        "label": "GitHub (recent activity)",
        "description": (
            "search_github(query: str, repo: str | None, hours_back: int | None) -> "
            "returns recent commits and issues/PRs. Use this for 'what happened' / "
            "'what changed' style questions. Set hours_back the same way as search_emails."
        ),
        "fn": search_github,
        "requires": ["repo"],
    },
    "explore_codebase": {
        "label": "GitHub (codebase understanding)",
        "description": (
            "explore_codebase(query: str, repo: str | None) -> smart RAG retrieval "
            "for any question about the codebase. Pass the user's question/sub-question "
            "as 'query' — the tool uses an LLM to pick the most relevant files from the "
            "repo and fetches their contents. Works for 'explain this project', "
            "'how does auth work', 'what technologies are used', 'how do I run this', etc. "
            "NOT for time-based activity questions — use search_github for those."
        ),
        "fn": explore_codebase,
        "requires": ["repo"],
    },
}