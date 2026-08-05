"""
GitHub tool — searches recent commits, issues/PRs, and can intelligently
explore repo codebases for any question about the code.

Smart RAG approach for explore_codebase:
  1. Fetch the full recursive file tree from the repo
  2. Use an LLM to pick the 5-10 most relevant files for the user's question
  3. Fetch the contents of those files
  4. Return them as evidence for the final synthesizer

Setup required:
  1. Create a GitHub Personal Access Token (read-only, repo scope if private repos needed)
  2. Set it as GITHUB_TOKEN in your .env
"""

import os
import json
import base64
import httpx
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

GITHUB_API = "https://api.github.com"

# File extensions to skip when building the tree (binary / non-informative)
_SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".bmp", ".webp",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp3", ".mp4", ".wav", ".avi", ".mov",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".pyc", ".pyo", ".class", ".o", ".so", ".dll", ".exe",
    ".min.js", ".min.css", ".map",
    ".lock",
}

# Directories to skip entirely (noisy, auto-generated)
_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".nuxt", "vendor",
    ".idea", ".vscode", ".terraform",
    "coverage", ".nyc_output",
}

FILE_SELECTOR_MODEL = "llama-3.3-70b-versatile"

# Max chars to fetch per file (keeps total evidence within LLM context limits)
MAX_FILE_CHARS = 3000

# Max files to select
MAX_FILES = 10


def _get_repo(repo: str | None) -> str | None:
    return repo or os.environ.get("DEFAULT_GITHUB_REPO")


def _get_headers() -> dict:
    token = os.environ.get("GITHUB_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _get_groq_client() -> Groq:
    return Groq(api_key=os.environ.get("API_KEY"))


def _should_skip_path(path: str) -> bool:
    """Returns True if the file path should be excluded from the tree."""
    # Check directory components
    parts = path.split("/")
    for part in parts[:-1]:  # all directory segments
        if part in _SKIP_DIRS:
            return True

    # Check file extension
    lower = path.lower()
    for ext in _SKIP_EXTENSIONS:
        if lower.endswith(ext):
            return True

    return False


# ──────────────────────────────────────────────────────────────────────
#  Helper: fetch default branch name
# ──────────────────────────────────────────────────────────────────────

async def _fetch_default_branch(repo: str, client: httpx.AsyncClient) -> str | None:
    """Gets the default branch name (e.g. 'main' or 'master') for a repo."""
    resp = await client.get(f"{GITHUB_API}/repos/{repo}")
    if resp.status_code == 200:
        return resp.json().get("default_branch", "main")
    return None


# ──────────────────────────────────────────────────────────────────────
#  Helper: fetch full recursive file tree
# ──────────────────────────────────────────────────────────────────────

async def _fetch_repo_tree(repo: str, client: httpx.AsyncClient) -> list[str] | None:
    """
    Fetches the full recursive file tree using the Git Trees API.
    Returns a filtered list of file paths (no binaries, no node_modules, etc.)
    Returns None if the API call fails.
    """
    branch = await _fetch_default_branch(repo, client)
    if not branch:
        return None

    resp = await client.get(
        f"{GITHUB_API}/repos/{repo}/git/trees/{branch}",
        params={"recursive": "1"},
    )

    if resp.status_code != 200:
        return None

    data = resp.json()
    tree = data.get("tree", [])

    # Filter to blobs (files) only, skip binaries and noisy directories
    file_paths = []
    for item in tree:
        if item["type"] != "blob":
            continue
        path = item["path"]
        if not _should_skip_path(path):
            file_paths.append(path)

    return file_paths


# ──────────────────────────────────────────────────────────────────────
#  Helper: LLM-based smart file selection
# ──────────────────────────────────────────────────────────────────────

def _select_relevant_files(question: str, file_paths: list[str]) -> list[str]:
    """
    Uses the Groq LLM to pick the most relevant files from the repo tree,
    given the user's question. This is the 'smart' part of the RAG pipeline.

    Returns a list of file paths (5-10 files).
    """
    # Build a compact tree representation for the prompt
    tree_text = "\n".join(file_paths)

    prompt = f"""You are a code-aware file selector. Given a user's question about a codebase
and the full list of files in the repository, pick the {MAX_FILES} MOST RELEVANT files
that would help answer the question.

Think about:
- README files always have useful context for general questions
- Config files (package.json, requirements.txt, pyproject.toml, Dockerfile, docker-compose.yml,
  Makefile, setup.py, setup.cfg, Cargo.toml, go.mod) reveal technologies and dependencies
- Look for files whose names/paths semantically match the question topic
  (e.g. "authentication" → auth/, middleware/, passport, jwt, login, etc.)
- Prefer source code files over test files, unless the question is about testing
- For "how to run" questions, prioritize README, Makefile, Dockerfile, docker-compose.yml, scripts/

User's question: "{question}"

Repository files:
{tree_text}

Respond with ONLY a JSON array of file paths, no other text. Example:
["README.md", "src/auth/handler.py", "requirements.txt"]

Pick up to {MAX_FILES} files. Only pick files from the list above — never invent paths."""

    client = _get_groq_client()

    response = client.chat.completions.create(
        model=FILE_SELECTOR_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )

    raw = response.choices[0].message.content.strip()

    # Strip markdown fences if the model added them
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.replace("json", "", 1).strip()

    try:
        selected = json.loads(raw)
    except json.JSONDecodeError:
        print(f"[warn] file selector returned invalid JSON, falling back to heuristics. Raw:\n{raw}")
        selected = _heuristic_fallback(file_paths)

    # Safety: only keep paths that actually exist in the tree
    valid_set = set(file_paths)
    selected = [p for p in selected if p in valid_set]

    # If the LLM returned nothing valid, use heuristic fallback
    if not selected:
        selected = _heuristic_fallback(file_paths)

    return selected[:MAX_FILES]


def _heuristic_fallback(file_paths: list[str]) -> list[str]:
    """
    Fallback file selection when the LLM fails — picks common high-value files.
    """
    priority_names = [
        "README.md", "readme.md", "README.rst", "README",
        "package.json", "requirements.txt", "pyproject.toml",
        "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
        "Makefile", "setup.py", "setup.cfg",
        "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
        ".env.example", "config.yaml", "config.json",
    ]
    selected = []
    for name in priority_names:
        for path in file_paths:
            if path.endswith(name) and path not in selected:
                selected.append(path)
                break
        if len(selected) >= MAX_FILES:
            break

    # If still not enough, grab the first few source files
    if len(selected) < 5:
        for path in file_paths:
            if path not in selected:
                selected.append(path)
            if len(selected) >= MAX_FILES:
                break

    return selected


# ──────────────────────────────────────────────────────────────────────
#  Helper: fetch contents of selected files
# ──────────────────────────────────────────────────────────────────────

async def _fetch_file_contents(
    paths: list[str], repo: str, client: httpx.AsyncClient
) -> list[str]:
    """
    Fetches the raw content of each file path from GitHub.
    Returns a list of formatted strings: "=== path ===\n<content>"
    Caps each file at MAX_FILE_CHARS to stay within LLM limits.
    """
    summaries = []

    for path in paths:
        resp = await client.get(
            f"{GITHUB_API}/repos/{repo}/contents/{path}",
            headers={"Accept": "application/vnd.github.raw+json"},
        )

        if resp.status_code == 200:
            content = resp.text[:MAX_FILE_CHARS]
            truncated = " (truncated)" if len(resp.text) > MAX_FILE_CHARS else ""
            summaries.append(f"=== {path}{truncated} ===\n{content}")
        else:
            summaries.append(f"=== {path} ===\n[could not fetch: HTTP {resp.status_code}]")

    return summaries


# ──────────────────────────────────────────────────────────────────────
#  Public tool: search_github (unchanged — handles time-based queries)
# ──────────────────────────────────────────────────────────────────────

async def search_github(query: str, repo: str | None = None, hours_back: int | None = None) -> list[str]:
    """
    Fetches recent commits and issues/PRs for the repo. `hours_back` optionally
    restricts commits to that window (e.g. 3 for "past 3 hours", 48 for "2 days").
    If hours_back is None, returns the most recent activity regardless of age
    (useful for "explain this codebase" style questions with no time element).

    Returns short text summaries — the final LLM decides what's relevant to
    the user's actual question, this tool just surfaces real data.
    """
    repo = _get_repo(repo)
    if not repo:
        return ["[error] No GitHub repo configured for this session."]

    headers = _get_headers()
    summaries = []

    commit_params = {"per_page": 20}
    if hours_back:
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()
        commit_params["since"] = since

    async with httpx.AsyncClient(headers=headers, timeout=10) as client:
        # Recent commits — no substring filtering on message text, since a
        # natural-language query almost never appears verbatim in a commit
        # message. The final LLM does relevance judgment, not this tool.
        commits_resp = await client.get(f"{GITHUB_API}/repos/{repo}/commits", params=commit_params)
        if commits_resp.status_code == 200:
            commits = commits_resp.json()
            if not commits:
                summaries.append(f"No commits found in the requested time window for {repo}.")
            for commit in commits:
                message = commit["commit"]["message"]
                author = commit["commit"]["author"]["name"]
                date = commit["commit"]["author"]["date"]
                summaries.append(f"Commit by {author} ({date}): {message}")
        elif commits_resp.status_code == 404:
            summaries.append(f"[error] Repo '{repo}' not found or not accessible with current token.")
        elif commits_resp.status_code == 401 or commits_resp.status_code == 403:
            summaries.append("[error] GitHub token invalid, missing scope, or rate-limited.")

        # Recent issues/PRs
        issues_resp = await client.get(
            f"{GITHUB_API}/search/issues",
            params={"q": f"repo:{repo}", "sort": "updated", "per_page": 10},
        )
        if issues_resp.status_code == 200:
            for item in issues_resp.json().get("items", []):
                kind = "PR" if "pull_request" in item else "Issue"
                summaries.append(f"{kind} #{item['number']}: {item['title']} (state: {item['state']})")

    return summaries


# ──────────────────────────────────────────────────────────────────────
#  Public tool: explore_codebase (UPGRADED — smart RAG retrieval)
# ──────────────────────────────────────────────────────────────────────

async def explore_codebase(query: str, repo: str | None = None) -> list[str]:
    """
    Smart RAG retrieval for codebase understanding questions.

    Instead of always returning README + top-level listing, this tool now:
      1. Fetches the full recursive file tree
      2. Uses an LLM to pick the most relevant files for the question
      3. Fetches and returns the contents of those files

    Examples:
      "What is this project about?"       → README, project description
      "How does authentication work?"     → auth files, middleware, README
      "What database is used?"            → requirements.txt, config files, DB code
      "How do I run this project?"        → README (install section), Makefile, Dockerfile
      "What technologies are used?"       → package.json, requirements.txt, Dockerfile
    """
    repo = _get_repo(repo)
    if not repo:
        return ["[error] No GitHub repo configured for this session."]

    headers = _get_headers()
    summaries = []

    async with httpx.AsyncClient(headers=headers, timeout=15) as client:
        # Step 1: Fetch the full repo tree
        print(f"      [explore] Fetching repo tree for {repo}...")
        file_paths = await _fetch_repo_tree(repo, client)

        if file_paths is None or len(file_paths) == 0:
            # Fallback: if tree fetch fails, do the old behavior
            print("      [explore] Tree fetch failed, falling back to README + top-level listing")
            return await _fallback_explore(repo, client)

        print(f"      [explore] Found {len(file_paths)} files in repo")

        # Step 2: LLM picks the most relevant files
        print(f"      [explore] LLM selecting relevant files for: {query!r}")
        selected_paths = _select_relevant_files(query, file_paths)
        print(f"      [explore] Selected {len(selected_paths)} files: {selected_paths}")

        # Step 3: Fetch contents of selected files
        print(f"      [explore] Fetching file contents...")
        file_contents = await _fetch_file_contents(selected_paths, repo, client)
        summaries.extend(file_contents)

        # Also include the repo tree summary for structural context
        tree_summary = _build_tree_summary(file_paths)
        summaries.append(f"=== Repository structure (all {len(file_paths)} files) ===\n{tree_summary}")

    return summaries


def _build_tree_summary(file_paths: list[str]) -> str:
    """Builds a compact directory-tree-style summary from a flat list of paths."""
    # Group by top-level directory
    dirs: dict[str, list[str]] = {}
    root_files = []

    for path in file_paths:
        parts = path.split("/", 1)
        if len(parts) == 1:
            root_files.append(path)
        else:
            top_dir = parts[0]
            dirs.setdefault(top_dir, []).append(path)

    lines = []
    for f in root_files:
        lines.append(f)

    for d in sorted(dirs.keys()):
        count = len(dirs[d])
        # Show a few files from each directory
        sample = dirs[d][:3]
        sample_str = ", ".join(s.split("/", 1)[1] for s in sample)
        more = f" + {count - 3} more" if count > 3 else ""
        lines.append(f"{d}/ ({count} files: {sample_str}{more})")

    return "\n".join(lines)


async def _fallback_explore(repo: str, client: httpx.AsyncClient) -> list[str]:
    """Original explore_codebase behavior — used as fallback if tree API fails."""
    headers = _get_headers()
    summaries = []

    # README content
    readme_resp = await client.get(
        f"{GITHUB_API}/repos/{repo}/readme",
        headers={**headers, "Accept": "application/vnd.github.raw+json"},
    )
    if readme_resp.status_code == 200:
        content = readme_resp.text[:MAX_FILE_CHARS]
        summaries.append(f"README content:\n{content}")
    else:
        summaries.append("No README found in repo.")

    # Top-level structure
    contents_resp = await client.get(f"{GITHUB_API}/repos/{repo}/contents/")
    if contents_resp.status_code == 200:
        items = contents_resp.json()
        structure = ", ".join(f"{item['name']} ({item['type']})" for item in items)
        summaries.append(f"Top-level repo structure: {structure}")

    return summaries