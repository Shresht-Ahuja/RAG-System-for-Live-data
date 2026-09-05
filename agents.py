"""Bounded, read-only retrieval agents for Gmail and GitHub."""

import asyncio
import json
import os
from typing import Any

from groq import Groq
from dotenv import load_dotenv

load_dotenv()
AGENT_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
MAX_GMAIL_SEARCHES = 2
MAX_GITHUB_CALLS = 3


def _client() -> Groq:
    return Groq(api_key=os.environ.get("API_KEY"))


def _json_response(prompt: str, fallback: dict[str, Any]) -> dict[str, Any]:
    """Return a compact LLM decision or a safe local fallback."""
    try:
        response = _client().chat.completions.create(
            model=AGENT_MODEL,
            messages=[
                {"role": "system", "content": "Return only valid JSON. Keep the plan minimal."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        return data if isinstance(data, dict) else fallback
    except Exception as exc:
        print(f"[warn] agent strategy fallback: {exc}")
        return fallback


def _has_useful_results(results: list[str]) -> bool:
    if not results:
        return False
    text = "\n".join(results).lower()
    return (
        "no emails found" not in text
        and "no notion pages found" not in text
        and "no obsidian notes found" not in text
        and "[error]" not in text
        and "could not fetch" not in text
    )


def _gmail_queries(sub_question: str, initial_query: str) -> list[str]:
    decision = _json_response(
        f"""You are a Gmail retrieval agent. Turn this sub-question into at most
{MAX_GMAIL_SEARCHES} Gmail search queries. Start broad, then make the second query
more specific only if the first returns nothing useful. Use Gmail operators only
when clearly helpful. Time filtering is handled separately. Return
{{\"queries\": [\"...\"]}}.

Sub-question: {sub_question}
Initial query: {initial_query}""",
        {"queries": [initial_query]},
    )
    queries = decision.get("queries", [initial_query])
    if not isinstance(queries, list):
        queries = [initial_query]
    cleaned = [q.strip() for q in queries if isinstance(q, str) and q.strip()]
    return (cleaned or [initial_query])[:MAX_GMAIL_SEARCHES]


async def run_gmail_agent(sub_question: str, args: dict[str, Any]) -> list[str]:
    """Search Gmail adaptively, stopping as soon as evidence is useful."""
    # Lazy import avoids a package-initialization cycle with tools/__init__.py.
    from tools.gmail_tool import search_emails

    query = str(args.get("query", sub_question))
    hours_back = args.get("hours_back")
    all_results: list[str] = []
    for attempt, candidate in enumerate(_gmail_queries(sub_question, query), start=1):
        print(f"      [gmail agent] search {attempt}/{MAX_GMAIL_SEARCHES}: {candidate!r}")
        results = await search_emails(
            candidate, hours_back=hours_back, access_token=args.get("access_token")
        )
        all_results.extend(results)
        if _has_useful_results(results):
            break
    return all_results


def _github_actions(sub_question: str, args: dict[str, Any]) -> list[str]:
    fallback = ["activity"] if args.get("hours_back") else ["codebase"]
    decision = _json_response(
        f"""You are a GitHub retrieval agent. Choose one or both actions:
\"activity\" (recent commits/issues/PRs) and \"codebase\" (semantic codebase
retrieval). Use activity for changes/status/time windows, codebase for architecture
or implementation, and both only when necessary. Return {{\"actions\": [\"...\"]}}.

Sub-question: {sub_question}
Time window in hours (or null): {args.get("hours_back")}""",
        {"actions": fallback},
    )
    actions = decision.get("actions", fallback)
    if not isinstance(actions, list):
        actions = fallback
    selected = [a for a in actions if a in {"activity", "codebase"}]
    return list(dict.fromkeys(selected))[:2] or fallback


async def run_github_agent(sub_question: str, args: dict[str, Any]) -> list[str]:
    """Retrieve GitHub evidence and make at most one evidence-driven follow-up."""
    # Lazy import avoids a package-initialization cycle with tools/__init__.py.
    from tools.github_tool import explore_codebase, search_github

    query = str(args.get("query", sub_question))
    repo = args.get("repo")
    hours_back = args.get("hours_back")
    actions = _github_actions(sub_question, args)
    print(f"      [github agent] initial strategy: {', '.join(actions)}")
    calls = []
    if "activity" in actions:
        calls.append(
            search_github(query, repo=repo, hours_back=hours_back, access_token=args.get("access_token"))
        )
    if "codebase" in actions:
        calls.append(explore_codebase(query, repo=repo, access_token=args.get("access_token")))
    batches = await asyncio.gather(*calls)
    results = [item for batch in batches for item in batch]

    if not _has_useful_results(results) and len(actions) == 1 and len(calls) < MAX_GITHUB_CALLS:
        follow_up = "codebase" if actions[0] == "activity" else "activity"
        print(f"      [github agent] evidence was thin; follow-up strategy: {follow_up}")
        if follow_up == "codebase":
            results.extend(await explore_codebase(query, repo=repo, access_token=args.get("access_token")))
        else:
            results.extend(
                await search_github(query, repo=repo, hours_back=hours_back, access_token=args.get("access_token"))
            )
    return results


def _document_queries(source: str, sub_question: str, initial_query: str) -> list[str]:
    decision = _json_response(
        f"""You are a read-only {source} retrieval agent. Turn this sub-question into
at most two concise search queries. Start broad and make the second query more
specific only if the first produces no useful evidence. Return
{{\"queries\": [\"...\"]}}.

Sub-question: {sub_question}
Initial query: {initial_query}""",
        {"queries": [initial_query]},
    )
    queries = decision.get("queries", [initial_query])
    if not isinstance(queries, list):
        queries = [initial_query]
    cleaned = [item.strip() for item in queries if isinstance(item, str) and item.strip()]
    return (cleaned or [initial_query])[:2]


async def _run_document_agent(source: str, sub_question: str, args: dict[str, Any], search_fn) -> list[str]:
    query = str(args.get("query", sub_question))
    all_results: list[str] = []
    for attempt, candidate in enumerate(_document_queries(source, sub_question, query), start=1):
        print(f"      [{source.lower()} agent] search {attempt}/2: {candidate!r}")
        results = await search_fn(candidate)
        all_results.extend(results)
        if _has_useful_results(results):
            break
    return all_results


async def run_notion_agent(sub_question: str, args: dict[str, Any]) -> list[str]:
    from tools.notion_tool import search_notion
    async def search(query: str) -> list[str]:
        return await search_notion(query, access_token=args.get("access_token"))
    return await _run_document_agent("Notion", sub_question, args, search)


async def run_obsidian_agent(sub_question: str, args: dict[str, Any]) -> list[str]:
    from tools.obsidian_tool import search_obsidian
    return await _run_document_agent("Obsidian", sub_question, args, search_obsidian)
