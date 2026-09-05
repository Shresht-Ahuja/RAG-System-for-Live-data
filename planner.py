"""
Planner LLM — decomposes a user question into an execution plan of
sub-questions, each mapped to a tool + arguments, so they can be run
in parallel.

Uses Groq (fast + cheap) since the planner just needs to reason about
*what* to search, not generate the final answer.
"""

import os
import json
from datetime import datetime
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

client = Groq(api_key=os.environ.get("API_KEY"))

PLANNER_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")


def create_plan(user_question: str, enabled_tools: dict, memory: list[str] | None = None) -> list[dict]:
    """
    Calls the planner LLM and returns a list of steps: [{sub_question, tool, args}, ...]

    enabled_tools: dict of {tool_name: {"description": "...", ...}} — only tools
    the user actually activated for this session. This is what makes the plugin
    system work: the planner literally cannot plan a call to a tool that isn't
    in this dict, because it's not shown one exists.

    The planner also infers the *intent* and *time window* directly from the
    question's phrasing — no separate "how many days" prompt needed. Examples:
      "I was absent for 2 days, tell me what happened" -> hours_back=48
      "what happened in the past 3 hours"               -> hours_back=3
      "I'm new to this codebase, explain this"          -> no time window, use
                                                             explore_codebase instead
    """
    memory = memory or []
    memory_context = ""
    if memory:
        memory_context = "\n\nRelevant context from memory:\n" + "\n".join(f"- {m}" for m in memory)

    tools_block = "\n".join(f"- {info['description']}" for info in enabled_tools.values())
    tool_names = list(enabled_tools.keys())
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    system_prompt = f"""You are a planning agent. The current date/time is {now_str}.

Given a user's message, figure out their actual intent and break it down into
a small set of focused sub-questions, each answerable by exactly one tool call.

Available tools (ONLY these — no others exist for this session):
{tools_block}

How to think about intent:
- If the user references a time period ("absent for 2 days", "past 3 hours",
  "since yesterday"), convert it to an hours_back integer and pass it to
  time-aware tools (search_emails, search_github).
- If the user wants to understand anything about the codebase — how it works,
  what technologies it uses, how to run it, how a specific feature is implemented,
  etc. — use explore_codebase. ALWAYS pass the user's question (or your
  sub-question) as the 'query' argument so the tool can pick the right files.
- If no time period is mentioned and the question isn't about understanding
  the codebase, default to hours_back=72 (3 days) as a reasonable
  general catch-up window.

Respond with ONLY a JSON array, no other text, in this exact format:
[
  {{"sub_question": "...", "tool": "{tool_names[0]}", "args": {{...}}}}
]

Rules:
- Only use tools listed above — never invent a tool name.
- Keep the plan minimal — don't create sub-questions that aren't needed to answer the question.
- Each sub_question should be specific enough to be a good search query on its own.
"""

    response = client.chat.completions.create(
        model=PLANNER_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"{user_question}{memory_context}"},
        ],
        temperature=0,
    )

    raw = response.choices[0].message.content.strip()

    # Strip markdown code fences if the model added them anyway
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.replace("json", "", 1).strip()

    try:
        plan = json.loads(raw)
    except json.JSONDecodeError:
        print(f"[warn] planner returned invalid JSON, falling back to single-step plan. Raw:\n{raw}")
        fallback_tool = tool_names[0]
        plan = [{
            "sub_question": user_question,
            "tool": fallback_tool,
            "args": {"query": user_question},
        }]

    # Safety filter: drop any step that hallucinated a tool not actually enabled
    plan = [step for step in plan if step.get("tool") in enabled_tools]

    return plan
