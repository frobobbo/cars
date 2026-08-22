from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .auth import (
    create_session,
    csrf_for_session,
    hash_password,
    read_session,
    verify_csrf,
    verify_password,
)
from .config import Settings
from .storage import Store

APP_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = APP_DIR.parent / "templates"
STATIC_DIR = APP_DIR.parent / "static"
SESSION_COOKIE = "cars_session"
ALLOWED_STATUSES = {"new", "interested", "researching", "contacted", "passed", "sold"}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'self'; style-src 'self'; img-src 'self' data:; "
        "script-src 'none'; connect-src 'self'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    ),
    "Cache-Control": "no-store",
}


def _friendly_time(value: str | None) -> str:
    if not value:
        return "Not recorded"
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return moment.astimezone().strftime("%b %-d, %Y · %-I:%M %p %Z")
    except (ValueError, TypeError):
        return value


def _freshness(value: str | None) -> tuple[str, str]:
    if not value:
        return "Unknown freshness", "unknown"
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        age_days = (datetime.now(UTC) - moment.astimezone(UTC)).total_seconds() / 86400
        if age_days <= 2:
            return "Recently verified", "fresh"
        if age_days <= 7:
            return "Checked this week", "aging"
        return "Stale — recheck listing", "stale"
    except (ValueError, TypeError):
        return "Unknown freshness", "unknown"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    store = Store(settings.database_path)
    store.ensure_password_hash(settings.initial_password_hash)
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

    app = FastAPI(title="Vehicle Watch", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.store = store
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    login_failures: dict[str, list[float]] = {}
    login_lock = threading.Lock()

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        host = (request.url.hostname or "").lower()
        host_allowed = any(
            pattern == "*"
            or host == pattern.lower()
            or (
                pattern.startswith("*.")
                and host.endswith(pattern[1:].lower())
                and host != pattern[2:].lower()
            )
            for pattern in settings.allowed_hosts
        )
        if request.url.path not in {"/health", "/ready"} and not host_allowed:
            response = PlainTextResponse("Invalid host header", status_code=400)
        else:
            response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers[key] = value
        return response

    def current_session(request: Request) -> tuple[str, dict[str, Any]] | None:
        token = request.cookies.get(SESSION_COOKIE, "")
        data = read_session(settings.session_secret, token) if token else None
        if not data:
            return None
        try:
            _, revision = store.password_state()
        except (sqlite3.Error, KeyError):
            return None
        if int(data.get("rev", -1)) != revision:
            return None
        return token, data

    def require_session(request: Request) -> tuple[str, dict[str, Any]]:
        session = current_session(request)
        if not session:
            raise HTTPException(status_code=401, detail="Authentication required.")
        return session

    def require_csrf_token(request: Request, supplied: str) -> None:
        token, _ = require_session(request)
        if not verify_csrf(settings.session_secret, token, supplied):
            raise HTTPException(status_code=403, detail="Invalid CSRF token.")

    def render_login(request: Request, *, error: str = "", status_code: int = 200):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "error": error,
                "changed": request.query_params.get("changed") == "1",
            },
            status_code=status_code,
        )

    @app.get("/health")
    def health():
        return {"status": "ok", "app": "cars"}

    @app.get("/ready")
    def ready():
        try:
            store.verify_connection()
        except sqlite3.Error as exc:
            raise HTTPException(status_code=503, detail="Storage unavailable.") from exc
        return {"status": "ok", "app": "cars"}

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        if current_session(request):
            return RedirectResponse("/", status_code=303)
        return render_login(request)

    @app.post("/login", response_class=HTMLResponse)
    def login_submit(
        request: Request,
        username: str = Form(..., max_length=64),
        password: str = Form(..., max_length=128),
    ):
        client_key = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with login_lock:
            attempts = [t for t in login_failures.get(client_key, []) if now - t < 900]
            login_failures[client_key] = attempts
            blocked = len(attempts) >= 8
        encoded, revision = store.password_state()
        valid = (
            not blocked
            and secrets.compare_digest(username.strip().lower(), "brett")
            and verify_password(password, encoded)
        )
        if not valid:
            with login_lock:
                login_failures.setdefault(client_key, []).append(now)
            return render_login(
                request,
                error="Invalid username or password.",
                status_code=429 if blocked else 401,
            )
        with login_lock:
            login_failures.pop(client_key, None)
        token = create_session(settings.session_secret, revision)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=30 * 24 * 60 * 60,
            httponly=True,
            secure=settings.secure_cookies,
            samesite="lax",
            path="/",
        )
        return response

    @app.get("/", response_class=HTMLResponse)
    def dashboard(
        request: Request,
        collection: str = "all",
        status: str = "all",
        q: str = "",
        saved: str = "",
    ):
        session = current_session(request)
        if not session:
            return RedirectResponse("/login", status_code=303)
        if collection not in {"all", "watch", "saved"}:
            collection = "all"
        if status not in ALLOWED_STATUSES | {"all"}:
            status = "all"
        vehicles = store.list_vehicles(collection=collection, status=status, query=q[:100])
        for vehicle in vehicles:
            try:
                vehicle["details"] = json.loads(vehicle.get("details_json") or "{}")
            except json.JSONDecodeError:
                vehicle["details"] = {}
            vehicle["collection_set"] = set((vehicle.get("collections") or "").split(","))
            vehicle["last_checked_label"] = _friendly_time(vehicle.get("last_verified_at"))
            vehicle["freshness_label"], vehicle["freshness_class"] = _freshness(
                vehicle.get("last_verified_at")
            )
        token, _ = session
        metadata = store.metadata()
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "vehicles": vehicles,
                "stats": store.stats(),
                "metadata": metadata,
                "last_run_label": _friendly_time(metadata.get("last_successful_run_at")),
                "criteria": metadata.get("criteria") or {},
                "collection": collection,
                "status": status,
                "query": q[:100],
                "saved_key": saved,
                "csrf_token": csrf_for_session(settings.session_secret, token),
                "statuses": ["new", "interested", "researching", "contacted", "passed", "sold"],
            },
        )

    @app.post("/vehicles/{vehicle_key:path}/note")
    def save_note(
        request: Request,
        vehicle_key: str,
        csrf_token: str = Form("", max_length=128),
        note: str = Form("", max_length=5000),
        status: str = Form("new", max_length=32),
    ):
        require_csrf_token(request, csrf_token)
        if status not in ALLOWED_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid vehicle status.")
        try:
            store.save_annotation(vehicle_key, note.strip(), status)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Vehicle not found.") from exc
        return RedirectResponse(f"/?saved={quote(vehicle_key, safe='')}", status_code=303)

    @app.post("/account/password")
    def change_password(
        request: Request,
        csrf_token: str = Form(..., max_length=128),
        current_password: str = Form(..., min_length=1, max_length=128),
        new_password: str = Form(..., min_length=12, max_length=128),
    ):
        require_csrf_token(request, csrf_token)
        encoded, _ = store.password_state()
        if not verify_password(current_password, encoded):
            raise HTTPException(status_code=400, detail="Current password is incorrect.")
        store.change_password(hash_password(new_password))
        response = RedirectResponse("/login?changed=1", status_code=303)
        response.delete_cookie(
            SESSION_COOKIE,
            path="/",
            secure=settings.secure_cookies,
            httponly=True,
            samesite="lax",
        )
        return response

    @app.post("/logout")
    def logout(request: Request, csrf_token: str = Form(..., max_length=128)):
        require_csrf_token(request, csrf_token)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(
            SESSION_COOKIE,
            path="/",
            secure=settings.secure_cookies,
            httponly=True,
            samesite="lax",
        )
        return response

    @app.post("/api/import")
    async def import_vehicles(
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        expected = f"Bearer {settings.ingest_token}"
        if not authorization or not secrets.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="Invalid ingest token.")
        length = request.headers.get("content-length")
        if length and int(length) > 5_000_000:
            raise HTTPException(status_code=413, detail="Import payload is too large.")
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("Payload must be a JSON object.")
            counts = store.import_payload(payload)
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True, **counts}

    return app
