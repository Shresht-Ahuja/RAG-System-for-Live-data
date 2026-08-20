"""FastAPI product surface: Google sign-in plus user-owned source connections."""

import asyncio
import base64
import os
import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv

from auth_store import connection_status, get_access_token, initialize_store, save_connection, upsert_user
from agents import run_github_agent, run_gmail_agent, run_notion_agent
from aggregator import aggregate_evidence, synthesize_answer
from planner import create_plan
from tools.gmail_tool import search_emails
from tools.github_tool import search_github
from tools.notion_tool import search_notion

load_dotenv()

app = FastAPI(title="Live Data Agent")


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise HTTPException(503, f"Server OAuth configuration is missing {name}.")
    return value


def _base_url() -> str:
    return _required("APP_BASE_URL").rstrip("/")


def _redirect_uri(provider: str) -> str:
    return f"{_base_url()}/auth/callback/{provider}"


def _state(request: Request, provider: str) -> str:
    state = secrets.token_urlsafe(32)
    states = request.session.get("oauth_states", {})
    states[provider] = state
    request.session["oauth_states"] = states
    return state


def _check_state(request: Request, provider: str, state: str | None) -> None:
    expected = request.session.get("oauth_states", {}).pop(provider, None)
    if not expected or not state or not secrets.compare_digest(expected, state):
        raise HTTPException(400, "Invalid OAuth state. Please start the connection again.")


def _current_user(request: Request) -> str:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(401, "Sign in with Google before connecting sources.")
    return user_id


@app.on_event("startup")
def startup() -> None:
    _required("SESSION_SECRET")
    _required("TOKEN_ENCRYPTION_KEY")
    initialize_store()


app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "missing-session-secret"),
    https_only=os.getenv("APP_ENV", "development") == "production",
    same_site="lax",
)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return "<h1>Live Data Agent</h1><p><a href='/auth/login'>Sign in with Google and connect Gmail</a></p>"
    statuses = connection_status(user_id)
    rows = "".join(f"<li>{provider.title()}: {'connected' if is_connected else 'not connected'}</li>" for provider, is_connected in statuses.items())
    return (
        "<h1>Live Data Agent</h1><p>Signed in.</p>"
        f"<ul>{rows}</ul>"
        "<p><a href='/connect/github'>Connect GitHub</a> | <a href='/connect/notion'>Connect Notion</a> | <a href='/auth/logout'>Log out</a></p>"
        "<p>Obsidian is a local-vault source and is configured separately on the machine running the agent.</p>"
    )


@app.get("/auth/login")
async def login(request: Request):
    state = _state(request, "google")
    params = {
        "client_id": _required("GOOGLE_CLIENT_ID"), "redirect_uri": _redirect_uri("google"),
        "response_type": "code", "scope": "openid email profile https://www.googleapis.com/auth/gmail.readonly",
        "access_type": "offline", "prompt": "consent", "state": state,
    }
    return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))


@app.get("/connect/github")
async def connect_github(request: Request):
    _current_user(request)
    params = {
        "client_id": _required("GITHUB_CLIENT_ID"), "redirect_uri": _redirect_uri("github"),
        # OAuth Apps cannot grant read-only access to private repositories. Keep
        # the default limited to public repositories; use a GitHub App with
        # read-only Contents/Issues metadata permissions for private repos.
        "scope": os.getenv("GITHUB_OAUTH_SCOPE", "read:user public_repo"), "state": _state(request, "github"),
    }
    return RedirectResponse("https://github.com/login/oauth/authorize?" + urlencode(params))


@app.get("/connect/notion")
async def connect_notion(request: Request):
    _current_user(request)
    params = {"owner": "user", "client_id": _required("NOTION_CLIENT_ID"), "redirect_uri": _redirect_uri("notion"), "response_type": "code", "state": _state(request, "notion")}
    return RedirectResponse("https://api.notion.com/v1/oauth/authorize?" + urlencode(params))


@app.get("/auth/callback/google")
async def google_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    if error or not code:
        raise HTTPException(400, f"Google authorization was not completed: {error or 'missing code'}")
    _check_state(request, "google", state)
    data = {"code": code, "client_id": _required("GOOGLE_CLIENT_ID"), "client_secret": _required("GOOGLE_CLIENT_SECRET"), "redirect_uri": _redirect_uri("google"), "grant_type": "authorization_code"}
    async with httpx.AsyncClient(timeout=12) as client:
        token_response = await client.post("https://oauth2.googleapis.com/token", data=data)
        token_response.raise_for_status()
        token = token_response.json()
        userinfo = await client.get("https://openidconnect.googleapis.com/v1/userinfo", headers={"Authorization": f"Bearer {token['access_token']}"})
        userinfo.raise_for_status()
    profile = userinfo.json()
    user_id = f"google:{profile['sub']}"
    upsert_user(user_id, profile["email"], profile.get("name"))
    request.session["user_id"] = user_id
    save_connection(user_id, "gmail", {"access_token": token["access_token"], "refresh_token": token.get("refresh_token"), "expires_at": int(time.time()) + int(token.get("expires_in", 3600))}, {"email": profile["email"]})
    return RedirectResponse("/", status_code=303)


@app.get("/auth/callback/github")
async def github_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    user_id = _current_user(request)
    if error or not code:
        raise HTTPException(400, f"GitHub authorization was not completed: {error or 'missing code'}")
    _check_state(request, "github", state)
    payload = {"client_id": _required("GITHUB_CLIENT_ID"), "client_secret": _required("GITHUB_CLIENT_SECRET"), "code": code, "redirect_uri": _redirect_uri("github")}
    async with httpx.AsyncClient(timeout=12) as client:
        response = await client.post("https://github.com/login/oauth/access_token", data=payload, headers={"Accept": "application/json"})
        response.raise_for_status()
    token = response.json()
    if "access_token" not in token:
        raise HTTPException(400, "GitHub did not return an access token.")
    save_connection(user_id, "github", {"access_token": token["access_token"], "expires_at": None}, {"scope": token.get("scope", "")})
    return RedirectResponse("/", status_code=303)


@app.get("/auth/callback/notion")
async def notion_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    user_id = _current_user(request)
    if error or not code:
        raise HTTPException(400, f"Notion authorization was not completed: {error or 'missing code'}")
    _check_state(request, "notion", state)
    credentials = f"{_required('NOTION_CLIENT_ID')}:{_required('NOTION_CLIENT_SECRET')}".encode()
    headers = {"Authorization": "Basic " + base64.b64encode(credentials).decode(), "Notion-Version": "2026-03-11"}
    async with httpx.AsyncClient(timeout=12) as client:
        response = await client.post("https://api.notion.com/v1/oauth/token", headers=headers, json={"grant_type": "authorization_code", "code": code, "redirect_uri": _redirect_uri("notion")})
        response.raise_for_status()
    token = response.json()
    save_connection(user_id, "notion", {"access_token": token["access_token"], "refresh_token": token.get("refresh_token"), "expires_at": int(time.time()) + int(token.get("expires_in", 3600)) if token.get("expires_in") else None}, {"workspace_name": token.get("workspace_name"), "workspace_id": token.get("workspace_id")})
    return RedirectResponse("/", status_code=303)


@app.get("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


class SearchRequest(BaseModel):
    provider: str
    query: str
    repo: str | None = None
    hours_back: int | None = None


@app.post("/api/search")
async def search(request: Request, body: SearchRequest):
    user_id = _current_user(request)
    if body.provider not in {"gmail", "github", "notion"}:
        raise HTTPException(400, "Unsupported provider.")
    token = await get_access_token(user_id, body.provider)
    if not token:
        raise HTTPException(409, f"Connect {body.provider.title()} before searching.")
    if body.provider == "gmail":
        results = await search_emails(body.query, body.hours_back, access_token=token)
    elif body.provider == "github":
        results = await search_github(body.query, repo=body.repo, hours_back=body.hours_back, access_token=token)
    else:
        results = await search_notion(body.query, access_token=token)
    return {"provider": body.provider, "results": results}


class AskRequest(BaseModel):
    question: str
    repo: str | None = None


@app.post("/api/ask")
async def ask(request: Request, body: AskRequest):
    """Run the existing bounded agentic pipeline with only this user's sources."""
    user_id = _current_user(request)
    statuses = connection_status(user_id)
    active_tools = {}
    if statuses["gmail"]:
        active_tools["gmail_agent"] = {
            "description": "gmail_agent(query: str, hours_back: int | None) -> searches the signed-in user's Gmail read-only.",
        }
    if statuses["github"]:
        active_tools["github_agent"] = {
            "description": "github_agent(query: str, repo: str | None, hours_back: int | None) -> searches the signed-in user's authorized GitHub repositories read-only.",
        }
    if statuses["notion"]:
        active_tools["notion_agent"] = {
            "description": "notion_agent(query: str) -> searches Notion pages the signed-in user authorized, read-only.",
        }
    if not active_tools:
        raise HTTPException(409, "Connect at least one source before asking a question.")

    plan = create_plan(body.question, enabled_tools=active_tools)

    async def execute(step: dict):
        args = dict(step.get("args", {}))
        tool = step["tool"]
        provider = {"gmail_agent": "gmail", "github_agent": "github", "notion_agent": "notion"}[tool]
        token = await get_access_token(user_id, provider)
        if not token:
            return {"sub_question": step["sub_question"], "source": provider.title(), "results": [f"[error] {provider.title()} needs to be reconnected."]}
        args["access_token"] = token
        if tool == "github_agent":
            if not body.repo:
                return {"sub_question": step["sub_question"], "source": "GitHub", "results": ["[error] A GitHub repository is required for this question."]}
            args["repo"] = body.repo
            results = await run_github_agent(step["sub_question"], args)
        elif tool == "gmail_agent":
            results = await run_gmail_agent(step["sub_question"], args)
        else:
            results = await run_notion_agent(step["sub_question"], args)
        return {"sub_question": step["sub_question"], "source": provider.title(), "results": results}

    outputs = await asyncio.gather(*(execute(step) for step in plan))
    evidence = aggregate_evidence(outputs)
    return {"answer": synthesize_answer(body.question, evidence), "sources_used": [item["source"] for item in outputs]}
