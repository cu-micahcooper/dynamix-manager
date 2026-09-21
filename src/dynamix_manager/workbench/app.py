"""Loopback-only HTTP interface with server-memory credentials and CSRF sessions."""

import hashlib
import json
import os
import secrets
import threading
from pathlib import Path
from urllib.parse import quote, urlsplit

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .demo import DemoAdapter, suggestion
from .domain import active_assigned, queue_sort, revision
from .richtext import render_html
from .service import WorkbenchService
from .settings import AISettings
from .store import Store


def create_app(data_dir=None, adapter_factory=None, env_path=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    store = Store(data_dir or Path.cwd() / ".workbench")
    ai_settings_store = AISettings(
        env_path or (Path(data_dir).parent if data_dir else Path.cwd()) / ".env"
    )
    config_path = store.path.parent / "config.json"
    config = {}
    if config_path.is_file() and not config_path.is_symlink():
        try:
            loaded = json.loads(config_path.read_text())
            config = {
                key: loaded[key]
                for key in ("tdx_app_id", "submission_verified")
                if key in loaded
            }
        except (OSError, ValueError, TypeError):
            config = {}
    service = WorkbenchService(store)
    sessions = {}
    session_locks = {}
    app.state.store = store
    app.state.service = service

    @app.middleware("http")
    async def security(request, call_next):
        host = request.headers.get("host", "")
        try:
            hostname = urlsplit("http://" + host).hostname
        except ValueError:
            hostname = None
        if hostname not in ("localhost", "127.0.0.1", "::1"):
            return JSONResponse({"detail": "Only loopback hosts are allowed."}, 403)
        expected = f"{request.url.scheme}://{host}"
        origin = request.headers.get("origin")
        if origin and origin != expected:
            return JSONResponse(
                {"detail": "Cross-origin requests are not allowed."}, 403
            )
        sid = request.cookies.get("workbench_session")
        fresh = sid not in sessions
        if fresh:
            sid = secrets.token_urlsafe(32)
            sessions[sid] = {
                "csrf_token": secrets.token_urlsafe(32),
                "nonce": secrets.token_urlsafe(32),
            }
            session_locks[sid] = threading.Lock()
        # Serialize each browser's authenticated operations across login/logout.
        # Nonblocking acquisition avoids exhausting the sync endpoint worker pool.
        lock = session_locks[sid]
        while not lock.acquire(blocking=False):
            await anyio.sleep(0.01)
        try:
            request.state.session = sessions[sid]
            if request.method not in ("GET", "HEAD", "OPTIONS") and (
                request.headers.get("sec-fetch-site") == "cross-site"
                or not secrets.compare_digest(
                    request.headers.get("x-csrf-token", ""), sessions[sid]["csrf_token"]
                )
            ):
                return JSONResponse(
                    {"detail": "Invalid CSRF token. Reload the page."}, 403
                )
            response = await call_next(request)
        finally:
            lock.release()
        if fresh:
            response.set_cookie(
                "workbench_session",
                sid,
                httponly=True,
                samesite="strict",
                secure=request.url.scheme == "https",
            )
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    def authenticated(request):
        session = request.state.session
        if "user" not in session:
            raise HTTPException(401, "Sign in to the workbench first.")
        return session

    def session_view(s):
        return {
            "authenticated": "user" in s,
            "csrf_token": s["csrf_token"],
            "user": s.get("user"),
            "mode": s.get("mode"),
            "capabilities": {
                **getattr(s.get("adapter"), "capabilities", {}),
                "ai_configured": bool(
                    ai_settings_store.key and ai_settings_store.model
                ),
                "model": ai_settings_store.model,
                "td_login_saved": ai_settings_store.login_saved,
            },
        }

    @app.get("/api/session")
    def session(request: Request):
        return session_view(request.state.session)

    @app.post("/api/login")
    def login(request: Request, body: dict):
        s = request.state.session
        if "scope" in s:
            service.clear_session(s["scope"])
        csrf = s["csrf_token"]
        s.clear()
        s["csrf_token"] = csrf
        s["nonce"] = secrets.token_urlsafe(32)
        mode = body.get("mode", "live")
        if mode not in ("demo", "live"):
            raise HTTPException(422, "Choose demo or live mode.")
        if body.get("use_saved") is True and mode == "live":
            if not ai_settings_store.login_saved:
                raise HTTPException(
                    422, "No personal login is saved. Enter your login first."
                )
            body = {**body, **ai_settings_store.personal}
        username, password, token = (
            body.get(key, "") for key in ("username", "password", "token")
        )
        if mode == "live" and (
            not all(isinstance(value, str) for value in (username, password, token))
            or not (token.strip() or (username.strip() and password))
        ):
            raise HTTPException(
                422, "Enter your personal TeamDynamix username/password or token."
            )
        if mode == "live" and any(
            len(v) > 10000 or any(c in v for c in ("\n", "\r", "\x00"))
            for v in (username, password, token)
        ):
            raise HTTPException(
                422, "Login fields must be single lines of valid length."
            )
        try:
            if adapter_factory:
                adapter = adapter_factory(body)
            elif mode == "demo":
                adapter = DemoAdapter()
            else:
                from .tdx import TDXAdapter

                adapter = TDXAdapter(
                    os.getenv("TDX_BASE_URL", ""),
                    os.getenv("WORKBENCH_TDX_APP_ID")
                    or str(config.get("tdx_app_id") or os.getenv("TDX_APP_ID", "")),
                    verified_submission=config.get("submission_verified") is True,
                    username=username,
                    password=password,
                    token=token,
                )
            user = adapter.login()
        except Exception as exc:
            raise HTTPException(
                401,
                "API login failed. Check the tenant URL, ticketing app ID, and personal API credentials. Browser SSO does not establish API access.",
            ) from exc
        if mode == "live" and body.get("remember") is True:
            try:
                ai_settings_store.save_login(username, password, token)
            except (OSError, ValueError):
                raise HTTPException(
                    500,
                    "Login verified, but could not save it to the local .env file. Check file permissions.",
                ) from None
        scope = hashlib.sha256(
            f"{adapter.base_url}|{adapter.app_id}|{user['id']}".encode()
        ).hexdigest()
        s.update(user=user, adapter=adapter, scope=scope, mode=mode)
        return session_view(s)

    @app.post("/api/forget-login")
    def forget_login(request: Request):
        try:
            ai_settings_store.forget_login()
        except (OSError, ValueError):
            raise HTTPException(
                500, "Could not remove the saved login from .env."
            ) from None
        return session_view(request.state.session)

    @app.post("/api/ai-settings")
    def ai_settings(request: Request, body: dict):
        s = authenticated(request)
        key, model = body.get("api_key", ""), body.get("model", "")
        if (
            not isinstance(key, str)
            or not isinstance(model, str)
            or not key.strip()
            or not model.strip()
            or len(key) > 1000
            or len(model) > 200
        ):
            raise HTTPException(422, "Enter an API key and model name.")
        if any(c in key + model for c in ("\n", "\r", "\x00")):
            raise HTTPException(422, "API key and model must each be a single line.")
        try:
            ai_settings_store.save(key.strip(), model.strip())
        except (OSError, ValueError):
            raise HTTPException(
                500,
                "Could not save OpenAI settings to the local .env file. Check file permissions.",
            ) from None
        return session_view(s)

    @app.post("/api/logout")
    def logout(request: Request):
        s = request.state.session
        if "scope" in s:
            service.clear_session(s["scope"])
        csrf = s["csrf_token"]
        s.clear()
        s["csrf_token"] = csrf
        s["nonce"] = secrets.token_urlsafe(32)
        return session_view(s)

    @app.get("/api/queue")
    def queue(request: Request):
        s = authenticated(request)
        try:
            result = s["adapter"].queue()
        except Exception as exc:
            if not store.blocking_intents(s["scope"]):
                raise HTTPException(
                    502,
                    "Queue could not be loaded. Check your connection or sign in again.",
                ) from exc
            result = {
                "tickets": [],
                "complete": False,
                "warning": "Queue unavailable; showing unresolved submission intents for recovery.",
            }
        states = store.pass_states(s["scope"])
        hydrated = []
        for candidate in result["tickets"]:
            try:
                current = s["adapter"].ticket(candidate["id"])
            except Exception:  # noqa: BLE001 - redact vendor failures and show incomplete queue
                result["complete"] = False
                result["warning"] = (
                    "Some tickets could not be refreshed; queue ordering and completeness are limited."
                )
                continue
            if active_assigned(current, s["user"]["id"]):
                hydrated.append(current)
                if not current.get("history_complete"):
                    result["warning"] = (
                        "Some ticket histories are incomplete; requester activity ordering may be incomplete."
                    )
        blocked = {intent["ticket"] for intent in store.blocking_intents(s["scope"])}
        hydrated = [t for t in hydrated if str(t["id"]) not in blocked]
        recovery = [service.read_ticket(s, identity) for identity in sorted(blocked)]
        tickets = queue_sort(hydrated)
        return {
            **result,
            "tickets": recovery + [t for t in tickets if str(t["id"]) not in states],
            "skipped": [t for t in tickets if states.get(str(t["id"])) == "skipped"],
            "reviewed": [t for t in tickets if states.get(str(t["id"])) == "reviewed"],
        }

    @app.get("/api/tickets/{identity}")
    def ticket(identity: int, request: Request):
        s = authenticated(request)
        result = service.read_ticket(s, identity)
        blocking = store.blocking(s["scope"], identity)
        return {
            **result,
            "submission_state": blocking["status"] if blocking else None,
            "uncertain": bool(blocking),
            "source_revision": revision(result),
            "description_html": render_html(result.get("description", "")),
            "history": [
                {**entry, "text_html": render_html(entry.get("text", ""))}
                for entry in result.get("history", [])
            ],
        }

    @app.get("/api/tickets/{identity}/attachments/{attachment_id}/pdf")
    def view_pdf(identity: int, attachment_id: str, request: Request):
        s = authenticated(request)
        current = service.ticket(s, identity)
        attachment = next(
            (a for a in current.get("attachments", []) if a["id"] == attachment_id),
            None,
        )
        if not attachment:
            raise HTTPException(404, "PDF attachment is not available on this ticket.")
        try:
            content = s["adapter"].pdf_attachment(attachment_id)
        except (ValueError, RuntimeError):
            raise HTTPException(
                502,
                "PDF could not be opened. Check your login, attachment access, and the 30 MB size limit. You can also open it in TeamDynamix.",
            ) from None
        return Response(
            content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": "inline; filename*=UTF-8''"
                + quote(attachment["name"], safe=""),
            },
        )

    @app.get("/api/tickets/{identity}/draft")
    def draft(identity: int, request: Request, visibility: str = "public"):
        s = authenticated(request)
        if visibility not in ("public", "internal"):
            raise HTTPException(422, "Invalid visibility.")
        t = service.read_ticket(s, identity)
        return store.draft(s["scope"], identity, visibility) or {
            "text": "",
            "instructions": "",
            "visibility": visibility,
            "recipients": [str(t["requester"]["id"])]
            if visibility == "public"
            and str(t["requester"]["id"])
            in {str(r["id"]) for r in t.get("recipients", [])}
            else [],
            "status_id": None,
        }

    @app.put("/api/tickets/{identity}/draft")
    def save_draft(identity: int, request: Request, body: dict):
        s = authenticated(request)
        visibility = body.get("visibility", "public")
        if visibility not in ("public", "internal"):
            raise HTTPException(422, "Invalid visibility.")
        for key in ("text", "instructions"):
            if not isinstance(body.get(key, ""), str) or len(body.get(key, "")) > 50000:
                raise HTTPException(422, "Draft is too long or invalid.")
        data = {
            k: body[k]
            for k in (
                "text",
                "instructions",
                "visibility",
                "recipients",
                "status_id",
                "source_revision",
            )
            if k in body
        }
        data["visibility"] = visibility
        store.save_draft(s["scope"], identity, visibility, data)
        return data

    @app.post("/api/tickets/{identity}/suggest")
    def suggest(identity: int, request: Request, body: dict):
        s = authenticated(request)
        t = service.ticket(s, identity)
        visibility = body.get("visibility", "public")
        instructions = body.get("instructions", "")
        if (
            visibility not in ("public", "internal")
            or not isinstance(instructions, str)
            or len(instructions) > 50000
        ):
            raise HTTPException(422, "Invalid generation request.")
        if not t.get("history_complete"):
            raise HTTPException(
                409,
                "History is incomplete. AI generation is disabled; manual drafting remains available.",
            )
        if s["mode"] == "demo":
            return suggestion(t, visibility, instructions)
        from .ai import SuggestionError
        from .ai import suggest as generate

        if not ai_settings_store.key or not ai_settings_store.model:
            raise HTTPException(
                422,
                "Open AI settings and enter your OpenAI API key and model, then save locally.",
            )
        try:
            return {
                **generate(
                    t,
                    visibility,
                    instructions,
                    ai_settings_store.key,
                    ai_settings_store.model,
                ),
                "simulated": False,
            }
        except SuggestionError as exc:
            raise HTTPException(502, str(exc)) from None
        except Exception as exc:
            raise HTTPException(
                502,
                "AI generation failed or context could not be safely processed. Your edits are preserved; manual composition remains available.",
            ) from exc

    @app.post("/api/tickets/{identity}/preview")
    def preview(identity: int, request: Request, body: dict):
        return service.preview(authenticated(request), identity, body)

    @app.post("/api/tickets/{identity}/submit")
    def submit(identity: int, request: Request, body: dict):
        return service.submit(
            authenticated(request), identity, body.get("preview_id", "")
        )

    @app.post("/api/tickets/{identity}/skip")
    def skip(identity: int, request: Request):
        s = authenticated(request)
        store.mark(s["scope"], identity, "skipped")
        return {"ok": True}

    @app.post("/api/reset-pass")
    def reset(request: Request, body: dict):
        store.reset(authenticated(request)["scope"], body.get("skipped_only") is True)
        return {"ok": True}

    @app.post("/api/tickets/{identity}/reconcile")
    def reconcile(identity: int, request: Request):
        s = authenticated(request)
        t = service.read_ticket(s, identity)
        intent = store.blocking(s["scope"], identity)
        # Message text alone cannot establish an accepted remote operation.
        return {
            "status": intent["status"] if intent else "clear",
            "message": "Automatic reconciliation cannot conclusively identify this operation. Inspect the remote ticket before acknowledging.",
            "url": t.get("url"),
        }

    @app.post("/api/tickets/{identity}/acknowledge")
    def acknowledge(identity: int, request: Request, body: dict):
        s = authenticated(request)
        if body.get("acknowledged") is not True:
            raise HTTPException(
                422, "Explicit acknowledgement after remote inspection is required."
            )
        intent = store.blocking(s["scope"], identity)
        if intent and intent["status"] == "pending":
            raise HTTPException(409, "Submission is still running.")
        if intent:
            store.finish(intent["id"], "acknowledged", {"user_acknowledged": True})
        return {"status": "clear"}

    static = Path(__file__).parent / "static"
    if static.is_dir():
        app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    return app
