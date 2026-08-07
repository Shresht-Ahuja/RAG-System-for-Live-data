"""
Agentic RAG Pipeline over live data (Gmail, GitHub, ...)

Flow:
  User Question
     -> Planner LLM (decomposes into sub-questions + picks tools)
     -> Execute tools in parallel
     -> Evidence Aggregator (merges + tags sources)
     -> Final LLM (synthesizes a cited natural language answer)

Plugin system:
  Every data source is a "plugin" registered in tools/__init__.py's
  AVAILABLE_TOOLS. At the start of each session, the user picks which
  plugins are active (e.g. GitHub only, or GitHub + Gmail). The planner
  only ever sees the tools that were activated, so it can't try to call
  a source the user didn't turn on.
"""

import asyncio
import os
import sys

# Ensure UTF-8 output formatting on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from planner import create_plan
from aggregator import aggregate_evidence, synthesize_answer
from tools import AVAILABLE_TOOLS


async def run_tool(step: dict, active_tools: dict) -> dict:
    """Executes a single planned step and tags the result with its source."""
    tool_name = step["tool"]
    args = step.get("args", {})
    tool_info = active_tools.get(tool_name)

    if tool_info is None:
        return {
            "sub_question": step["sub_question"],
            "tool": tool_name,
            "source": tool_name,
            "results": [],
            "error": f"Unknown or inactive tool '{tool_name}'",
        }

    try:
        results = await tool_info["fn"](sub_question=step["sub_question"], **args)
    except Exception as e:
        results = []
        print(f"[warn] tool '{tool_name}' failed: {e}")

    return {
        "sub_question": step["sub_question"],
        "tool": tool_name,
        "source": tool_info["label"],
        "results": results,
    }


async def run_pipeline(user_question: str, active_tools: dict, memory: list[str] | None = None) -> str:
    memory = memory or []

    # 1. Planner LLM decomposes the question into sub-questions + tool calls
    #    (only sees tools that are actually active this session)
    print(f"\n[1/4] Planning for: {user_question!r}")
    plan = create_plan(user_question, enabled_tools=active_tools, memory=memory)
    print(f"      -> {len(plan)} sub-question(s) planned:")
    for step in plan:
        print(f"         - ({step['tool']}) {step['sub_question']}")

    # 2. Execute all tool calls in parallel
    print("[2/4] Executing tools in parallel...")
    tool_outputs = await asyncio.gather(*(run_tool(step, active_tools) for step in plan))

    # 3. Aggregate evidence from all sources into a single labeled context
    print("[3/4] Aggregating evidence...")
    evidence = aggregate_evidence(tool_outputs)

    # 4. Final LLM synthesizes a natural-language, cited answer
    print("[4/4] Synthesizing final answer...\n")
    answer = synthesize_answer(user_question, evidence)

    return answer


def _normalize_repo(repo_input: str) -> str:
    """Accepts either 'owner/repo' or a full GitHub URL and returns 'owner/repo'."""
    repo_input = repo_input.rstrip("/")
    if repo_input.startswith("http"):
        parts = repo_input.split("github.com/")
        if len(parts) == 2:
            return parts[1]
    return repo_input


def select_plugins() -> dict:
    """
    Lets the user choose which source plugins are active for this session.
    Returns a filtered dict of {tool_name: tool_info} — this is what gets
    passed to the planner and used as the tool registry for execution.
    """
    print("Available sources:")
    tool_names = list(AVAILABLE_TOOLS.keys())
    for i, name in enumerate(tool_names, start=1):
        print(f"  {i}. {AVAILABLE_TOOLS[name]['label']}")

    choice = input(
        "\nWhich sources do you want to use? "
        "(comma-separated numbers, or 'all') [default: all]: "
    ).strip().lower()

    if not choice or choice == "all":
        selected_names = tool_names
    else:
        indices = [int(x.strip()) - 1 for x in choice.split(",") if x.strip().isdigit()]
        selected_names = [tool_names[i] for i in indices if 0 <= i < len(tool_names)]

    if not selected_names:
        print("No valid sources selected, defaulting to all.")
        selected_names = tool_names

    active_tools = {name: AVAILABLE_TOOLS[name] for name in selected_names}
    print(f"\n✓ Active sources: {', '.join(t['label'] for t in active_tools.values())}")
    return active_tools


def setup_session(active_tools: dict) -> dict:
    """
    Interactive setup — collects only what each active plugin actually needs
    to run (e.g. GitHub needs a repo). No fixed "how many days" question:
    the user just says what they want, and the planner infers timing/intent
    from the phrasing itself.
    """
    print("=" * 60)
    print("Live Data Agent — Session Setup")
    print("=" * 60)

    config = {}

    if "github_agent" in active_tools:
        repo_input = input("GitHub repo (e.g. yourusername/project-x or full URL): ").strip()
        repo = _normalize_repo(repo_input)
        os.environ["DEFAULT_GITHUB_REPO"] = repo
        config["repo"] = repo
        print(f"✓ GitHub repo set to: {repo}")

    if "gmail_agent" in active_tools:
        print("✓ Gmail: already connected (OAuth complete)")

    if "notion_agent" in active_tools:
        status = "configured" if os.environ.get("NOTION_TOKEN") else "NOTION_TOKEN is missing"
        print(f"Notion: {status}")

    if "obsidian_agent" in active_tools:
        vault = os.environ.get("OBSIDIAN_VAULT_PATH")
        status = vault if vault and os.path.isdir(vault) else "OBSIDIAN_VAULT_PATH is missing or invalid"
        print(f"Obsidian: {status}")

    print()
    return config


if __name__ == "__main__":
    active_tools = select_plugins()
    session = setup_session(active_tools)

    print("Ready to help — ask me anything about your connected sources.")

    user_question = input("> ").strip()

    final_answer = asyncio.run(run_pipeline(user_question, active_tools))

    print("\n" + "=" * 60)
    print("FINAL ANSWER")
    print("=" * 60)
    print(final_answer)
