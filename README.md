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
2. **Tools / Agents** — each connected source (GitHub, Gmail, Notion, Obsidian) is a self-contained, read-only agent. GitHub combines recent-activity search with semantic code retrieval (embeddings + vector similarity) so questions like *"how does auth work here?"* find the right files by meaning, not keyword matching.
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
        ├── github_tool.py      — commits, issues/PRs, semantic code search
        ├── notion_tool.py      — Notion page search (OAuth)
        └── obsidian_tool.py    — local Markdown vault search (no OAuth — local-first)
```

**Why Obsidian has no OAuth:** Obsidian is a local-first Markdown vault with no cloud API — the vault file path *is* the connection, not a login.

## Tech stack

| Layer | Tech |
|---|---|
| Frontend | React 19, TypeScript, Vite |
| Backend | Python, FastAPI, Uvicorn, Pydantic |
| Planner LLM | Groq (fast query decomposition) |
| Synthesis LLM | Anthropic Claude (final grounded answer) |
| Retrieval | Sentence-transformer embeddings + cosine similarity (GitHub semantic code search) |
| Auth | OAuth 2.0 (Google, GitHub, Notion) with encrypted per-user token storage |
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
GROQ_API_KEY=
ANTHROPIC_API_KEY=

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

Open the app, sign in, connect the sources you want (GitHub / Gmail / Notion), optionally point it at a local Obsidian vault, and start asking questions.

## Example queries

- *"I was absent for 2 days, tell me what happened"* — time-windowed catch-up across all connected sources
- *"I'm new to this codebase, explain it"* — semantic exploration of repo structure and README, not a time-based search
- *"What happened in the past 3 hours?"* — precise recent-activity lookup
- *"How does authentication work in this repo?"* — semantic code retrieval, not keyword search

## Evaluation

The system is evaluated across three layers:

- **Retrieval evaluation** — precision@k and chunk relevance for semantic search results
- **Trajectory evaluation** — did the planner select the correct tool(s) and reasonable arguments for a given question
- **Model / output evaluation** — faithfulness of the final answer to the retrieved evidence, and hallucination rate
