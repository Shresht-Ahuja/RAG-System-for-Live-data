"""FastAPI product surface: GitHub/Google sign-in plus user-owned connections."""

import asyncio
import base64
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from dotenv import load_dotenv

from auth_store import connection_status, get_access_token, get_user, initialize_store, save_connection, upsert_user
from agents import run_github_agent, run_gmail_agent, run_notion_agent
from aggregator import aggregate_evidence, synthesize_answer
from planner import create_plan
from tools.gmail_tool import search_emails
from tools.github_tool import search_github
from tools.notion_tool import search_notion

load_dotenv()

app = FastAPI(title="Live Data Agent")
PROJECT_ROOT = Path(__file__).resolve().parent
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise HTTPException(503, f"Server OAuth configuration is missing {name}.")
    return value


def _oauth_credential(name: str) -> str:
    """Reject copied example values before they produce opaque provider errors."""
    value = _required(name)
    normalized = value.strip().lower()
    if normalized.startswith(("your_", "your-", "replace_with_", "replace-with-")):
        raise HTTPException(503, f"{name} is still a placeholder. Add the real value from your OAuth app to .env.")
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
        if request.session.get("guest"):
            raise HTTPException(403, "Guest mode is limited to public GitHub repository questions.")
        raise HTTPException(401, "Sign in before connecting sources.")
    return user_id


def _is_guest(request: Request) -> bool:
    return bool(request.session.get("guest")) and not bool(request.session.get("user_id"))


@app.on_event("startup")
def startup() -> None:
    _required("SESSION_SECRET")
    _required("TOKEN_ENCRYPTION_KEY")
    if os.getenv("APP_ENV", "development") == "production":
        if not _base_url().startswith("https://"):
            raise RuntimeError("APP_BASE_URL must use https:// when APP_ENV=production.")
        if not os.getenv("ALLOWED_HOSTS"):
            raise RuntimeError("ALLOWED_HOSTS must list the production domain when APP_ENV=production.")
        if not (FRONTEND_DIST / "index.html").is_file():
            raise RuntimeError("Production frontend is missing. Run the Vite build before starting the app.")
    initialize_store()


app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "missing-session-secret"),
    https_only=os.getenv("APP_ENV", "development") == "production",
    same_site="lax",
)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(","),
)
if os.getenv("APP_ENV", "development") == "production":
    app.add_middleware(HTTPSRedirectMiddleware)

app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets", check_dir=False), name="assets")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home(request: Request):
    index = FRONTEND_DIST / "index.html"
    if index.is_file():
        return FileResponse(index)
    return "<h1>Live Data Agent API</h1><p>Start the React dashboard with <code>npm run dev</code> inside frontend.</p>"


@app.get("/auth/login")
async def login(request: Request):
    if _is_guest(request):
        raise HTTPException(403, "Guest mode cannot connect Gmail. Sign in with GitHub first.")
    state = _state(request, "google")
    params = {
        "client_id": _required("GOOGLE_CLIENT_ID"), "redirect_uri": _redirect_uri("google"),
        "response_type": "code", "scope": "openid email profile https://www.googleapis.com/auth/gmail.readonly",
        "access_type": "offline", "prompt": "consent", "state": state,
    }
    return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))


@app.get("/auth/guest")
async def guest_login(request: Request):
    """Start an anonymous, public-repository-only session."""
    request.session.clear()
    request.session["guest"] = True
    return RedirectResponse("/", status_code=303)


@app.get("/auth/login/github")
async def login_github(request: Request):
    """Start GitHub OAuth as the primary sign-in path.

    The same callback is also used when an already signed-in user links GitHub.
    """
    params = {
        "client_id": _oauth_credential("GITHUB_CLIENT_ID"), "redirect_uri": _redirect_uri("github"),
        # Login only needs profile and email read access. Public repository
        # metadata remains available through GitHub's public API without a
        # repository permission; private/org access is not requested.
        "scope": os.getenv("GITHUB_OAUTH_SCOPE", "read:user user:email"),
        "state": _state(request, "github"),
    }
    return RedirectResponse("https://github.com/login/oauth/authorize?" + urlencode(params))


@app.get("/connect/github")
async def connect_github(request: Request):
    _current_user(request)
    params = {
        "client_id": _oauth_credential("GITHUB_CLIENT_ID"), "redirect_uri": _redirect_uri("github"),
        # Keep linking least-privilege: no repository or organization scope.
        "scope": os.getenv("GITHUB_OAUTH_SCOPE", "read:user user:email"), "state": _state(request, "github"),
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
    # If the user is already signed in with GitHub, this is an account-linking
    # action. Otherwise Google creates the initial application identity.
    user_id = request.session.get("user_id") or f"google:{profile['sub']}"
    if not get_user(user_id):
        upsert_user(user_id, profile["email"], profile.get("name"))
    request.session.pop("guest", None)
    request.session["user_id"] = user_id
    save_connection(user_id, "gmail", {"access_token": token["access_token"], "refresh_token": token.get("refresh_token"), "expires_at": int(time.time()) + int(token.get("expires_in", 3600))}, {"email": profile["email"]})
    return RedirectResponse("/", status_code=303)


@app.get("/auth/callback/github")
async def github_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    if error or not code:
        raise HTTPException(400, f"GitHub authorization was not completed: {error or 'missing code'}")
    _check_state(request, "github", state)
    payload = {"client_id": _oauth_credential("GITHUB_CLIENT_ID"), "client_secret": _oauth_credential("GITHUB_CLIENT_SECRET"), "code": code, "redirect_uri": _redirect_uri("github")}
    async with httpx.AsyncClient(timeout=12) as client:
        response = await client.post("https://github.com/login/oauth/access_token", data=payload, headers={"Accept": "application/json"})
        response.raise_for_status()
    token = response.json()
    if "access_token" not in token:
        raise HTTPException(400, "GitHub did not return an access token.")
    github_headers = {"Authorization": f"Bearer {token['access_token']}", "Accept": "application/vnd.github+json"}
    async with httpx.AsyncClient(timeout=12) as client:
        profile_response = await client.get("https://api.github.com/user", headers=github_headers)
        profile_response.raise_for_status()
        profile = profile_response.json()
        if not profile.get("email"):
            email_response = await client.get("https://api.github.com/user/emails", headers=github_headers)
            if email_response.is_success:
                emails = email_response.json()
                preferred = next((item.get("email") for item in emails if item.get("primary") and item.get("verified")), None)
                profile["email"] = preferred
    email = profile.get("email") or f"github-{profile['id']}@users.noreply.github.com"

    # First-time GitHub sign-in creates the user. When a Google user clicks
    # “Connect GitHub”, retain the existing identity and only add the source.
    user_id = request.session.get("user_id") or f"github:{profile['id']}"
    if not get_user(user_id):
        upsert_user(user_id, email, profile.get("name") or profile.get("login"))
    request.session.pop("guest", None)
    request.session["user_id"] = user_id
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


@app.get("/api/me")
async def current_user(request: Request):
    if _is_guest(request):
        return {
            "guest": True,
            "user": {"email": "", "name": "Guest"},
            "connections": {"gmail": False, "github": False, "notion": False},
        }
    user_id = _current_user(request)
    user = get_user(user_id)
    if not user:
        raise HTTPException(401, "Your session is no longer valid. Sign in again.")
    return {"user": user, "connections": connection_status(user_id)}


class SearchRequest(BaseModel):
    provider: str
    query: str
    repo: str | None = None
    hours_back: int | None = None


@app.post("/api/search")
async def search(request: Request, body: SearchRequest):
    if body.provider not in {"gmail", "github", "notion"}:
        raise HTTPException(400, "Unsupported provider.")
    guest = _is_guest(request)
    if guest and body.provider != "github":
        raise HTTPException(403, "Guest mode only supports public GitHub repositories.")
    if guest and not body.repo:
        raise HTTPException(400, "Enter a public GitHub repository URL in guest mode.")
    user_id = None if guest else _current_user(request)
    token = "" if guest else await get_access_token(user_id, body.provider)
    if not guest and not token:
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
    guest = _is_guest(request)
    user_id = None if guest else _current_user(request)
    if guest:
        active_tools = {
            "github_agent": {
                "description": "github_agent(query: str, repo: str | None, hours_back: int | None) -> reads public GitHub repository activity and code without using a user account.",
            }
        }
    else:
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
        token = "" if guest else await get_access_token(user_id, provider)
        if not guest and not token:
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
