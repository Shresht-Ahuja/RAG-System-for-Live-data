# Agentic RAG for Live Data

An AI assistant that answers questions by reasoning across your **live, connected data sources** — GitHub, Gmail, Notion, and Obsidian — instead of a static, pre-indexed dataset.

Ask something like *"What happened in the last 3 hours?"* or *"I'm new to this codebase, explain it"*, and the system plans which sources to query, retrieves evidence from each in parallel, and synthesizes a single, cited, natural-language answer.

## How it works

```
User Question
     │
     ▼
Planner LLM (Groq)  ──  decomposes the question into sub-questions
     │                  and decides which tool(s) each one needs
     ▼
Tools run in parallel  ──  GitHub, Gmail, Notion, Obsidian
     │
     ▼
Evidence Aggregator  ──  merges results into one labeled, citable context
     │
     ▼
Final LLM  ──  synthesizes a cited answer
     │
     ▼
Response
```

1. **Planner** — an LLM reads the raw question, infers intent and any time window ("past 3 hours" → a precise lookback), and decides which tool(s) are relevant. It never sees or can call a tool the user hasn't connected.
2. **Tools / Agents** — each connected source (GitHub, Gmail, Notion, Obsidian) is a self-contained, read-only agent. For codebase questions, GitHub fetches the repository tree, uses an LLM to select the most relevant files, and retrieves their contents. This is live, LLM-guided retrieval; there is no persistent embedding index or vector database.
3. **Evidence Aggregator** — flattens and numbers every result across all sources into a single evidence list, so the final answer can cite exactly where each claim came from.
4. **Final LLM** — synthesizes a natural-language answer grounded only in the retrieved evidence, with inline citations (`[1]`, `[2]`, ...).

## Architecture

This is a full-stack app with two layers:

**Frontend** — React 19 + TypeScript + Vite SPA. Handles login, connected-service management, and the search/results UI.

**Backend** — Python + FastAPI + Uvicorn. Handles:
- OAuth authorization flows for Google, GitHub, and Notion (users connect their own accounts — no manual token pasting)
- Per-user encrypted storage of access/refresh tokens
- The planner → tools → aggregator → synthesis pipeline
- Async HTTP clients for calling each provider's API

```
Frontend (React + Vite)
        │  HTTPS
        ▼
Backend (FastAPI)
   ├── OAuth routes (Google / GitHub / Notion)
   ├── Session + per-user token storage
   ├── planner.py       — query decomposition + tool selection
   ├── aggregator.py     — evidence merging + final synthesis
   └── tools/
        ├── gmail_tool.py       — Gmail search (OAuth)
        ├── github_tool.py      — commits, issues/PRs, LLM-guided codebase retrieval
        ├── notion_tool.py      — Notion page search (OAuth)
        └── obsidian_tool.py    — local Markdown vault search (no OAuth — local-first)
```

**Why Obsidian has no OAuth:** Obsidian is a local-first Markdown vault with no cloud API — the vault file path *is* the connection, not a login. Obsidian is available through the local/CLI workflow, not as a web OAuth connection.

## Tech stack

| Layer | Tech |
|---|---|
| Frontend | React 19, TypeScript, Vite |
| Backend | Python, FastAPI, Uvicorn, Pydantic |
| Planner, agents, file selector, and synthesis LLM | Groq `openai/gpt-oss-120b` by default (configurable with `GROQ_MODEL`) |
| Retrieval | Live GitHub tree inspection, LLM-guided file selection, and direct file-content retrieval; no vector database |
| Auth | GitHub OAuth login, optional Google/Gmail and Notion connections, encrypted per-user token storage |
| Guest access | Anonymous public-GitHub-only sessions; guest sessions are not stored in SQLite |
| Integrations | Gmail API, GitHub API, Notion API, local filesystem (Obsidian) |

## Setup

### 1. Clone and install
```bash
git clone <your-repo-url>
cd <repo-name>

# backend
pip install -r requirements.txt

# frontend
cd frontend
npm install
```

### 2. Environment variables
Create a `.env` in the backend root:
```
API_KEY=your_groq_api_key
GROQ_MODEL=openai/gpt-oss-120b

APP_BASE_URL=http://localhost:5173
APP_ENV=development
ALLOWED_HOSTS=localhost,127.0.0.1
SESSION_SECRET=replace_with_a_long_random_value
TOKEN_ENCRYPTION_KEY=replace_with_a_fernet_key
DATABASE_PATH=data/live_data_agent.db

GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
NOTION_CLIENT_ID=
NOTION_CLIENT_SECRET=

OBSIDIAN_VAULT_PATH=   # local vault only, no OAuth
```

### 3. Run
```bash
# backend
uvicorn webapp:app --reload

# frontend
cd frontend
npm run dev
```

Open the app and either sign in with GitHub for personalized connections or continue as a guest to ask questions about public GitHub repositories. Authenticated users can optionally connect Gmail and Notion; Obsidian is configured locally.

## Example queries

- *"I was absent for 2 days, tell me what happened"* — time-windowed catch-up across all connected sources
- *"I'm new to this codebase, explain it"* — LLM-guided exploration of repo structure and source files, not a time-based search
- *"What happened in the past 3 hours?"* — precise recent-activity lookup
- *"How does authentication work in this repo?"* — targeted codebase retrieval based on the repository tree and file contents

## Evaluation

The system is evaluated across three layers:

- **Retrieval evaluation** — whether the selected files and live evidence cover the user’s question
- **Trajectory evaluation** — did the planner select the correct tool(s) and reasonable arguments for a given question
- **Model / output evaluation** — faithfulness of the final answer to the retrieved evidence, and hallucination rate
