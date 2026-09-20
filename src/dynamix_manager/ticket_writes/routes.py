"""Human-only, capability-bound review and Save routes for hosted ticket writes."""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import re
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import parse_qsl

from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect, Request
from starlette.responses import HTMLResponse, PlainTextResponse
from starlette.routing import Route

from .service import (
    LinkedIdentityMismatch,
    TicketWriteServiceError,
    WriteAuthorizationRequired,
    WritesDisabled,
)
from .store import (
    WriteBindingError,
    WriteExpiredError,
    WriteIntegrityError,
    WriteNotFoundError,
    WriteStoreError,
)


_COOKIE = "__Host-tdx-write"
_SECRET = re.compile(r"^[A-Za-z0-9_-]{43}$")
_MAX_BODY = 2048
_RATE_LIMIT = 30
_RATE_SECONDS = 60

_STYLE = """
:root {
  --paper: oklch(0.975 0.008 252);
  --ink: oklch(0.24 0.035 252);
  --muted: oklch(0.46 0.025 252);
  --rule: oklch(0.78 0.025 252);
  --navy: oklch(0.33 0.09 252);
  --navy-strong: oklch(0.25 0.09 252);
  --focus: oklch(0.61 0.15 67);
  --quiet: oklch(0.93 0.015 252);
  --danger: oklch(0.45 0.13 28);
  --space-xs: 0.25rem;
  --space-sm: 0.5rem;
  --space-md: 1rem;
  --space-lg: 1.5rem;
  --space-xl: 2rem;
  --space-2xl: 3rem;
}
* { box-sizing: border-box; }
html { background: var(--paper); color: var(--ink); font-family: "Trebuchet MS", sans-serif; }
body { margin: 0; min-width: 0; }
main { width: min(100% - 2rem, 58rem); margin: 0 auto; padding: var(--space-2xl) 0; }
h1, h2 { font-family: Palatino, "Palatino Linotype", serif; color: var(--navy-strong); margin: 0; }
h1 { font-size: 2rem; line-height: 1.15; }
h1 { overflow-wrap: anywhere; }
h2 { font-size: 1.25rem; line-height: 1.3; }
p, li, dd, dt, a, button { font-size: 1rem; line-height: 1.5; }
p { max-width: 75ch; }
.eyebrow { color: var(--muted); font-size: 0.8rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; }
.intro { display: grid; gap: var(--space-sm); padding-bottom: var(--space-xl); border-bottom: 1px solid var(--rule); }
.status { color: var(--danger); font-weight: 700; }
.meta { display: grid; grid-template-columns: 10rem minmax(0, 1fr); gap: var(--space-sm) var(--space-lg); padding: var(--space-lg) 0; margin: 0; border-bottom: 1px solid var(--rule); }
.meta dt { color: var(--muted); font-weight: 700; }
.meta dd { margin: 0; overflow-wrap: anywhere; }
.changes { display: grid; gap: var(--space-xl); padding: var(--space-xl) 0; }
.change { display: grid; gap: var(--space-md); padding-bottom: var(--space-xl); border-bottom: 1px solid var(--rule); }
.comparison { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: var(--space-lg); }
.value { margin: var(--space-xs) 0 0; padding: var(--space-md); background: var(--quiet); border: 1px solid var(--rule); white-space: pre-wrap; overflow-wrap: anywhere; }
.notices { display: grid; gap: var(--space-sm); padding-left: 1.25rem; max-width: 75ch; }
.actions { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-md); padding-top: var(--space-xl); border-top: 1px solid var(--rule); }
button { min-height: 2.75rem; padding: 0.65rem 1.25rem; border: 1px solid var(--navy); border-radius: 0.2rem; background: var(--navy); color: var(--paper); font-weight: 700; cursor: pointer; }
button:disabled { cursor: wait; opacity: 0.72; }
a { color: var(--navy); text-underline-offset: 0.2em; overflow-wrap: anywhere; }
.actions a { display: inline-flex; align-items: center; min-height: 2.75rem; }
a:focus-visible, button:focus-visible { outline: 0.2rem solid var(--focus); outline-offset: 0.2rem; }
@media (max-width: 36rem) {
  main { width: min(100% - 1.5rem, 58rem); padding-top: var(--space-xl); }
  .meta, .comparison { grid-template-columns: minmax(0, 1fr); }
  .comparison { gap: var(--space-md); }
  .actions { align-items: stretch; flex-direction: column; }
  button { width: 100%; }
}
""".strip()

_BOOTSTRAP_JS = """
(() => {
  const form = document.getElementById("open-review");
  const message = document.getElementById("bootstrap-message");
  const capability = location.hash.startsWith("#") ? location.hash.slice(1) : "";
  history.replaceState(null, "", location.pathname);
  if (/^[A-Za-z0-9_-]{43}$/.test(capability)) {
    form.elements.cap.value = capability;
    form.requestSubmit();
  } else {
    message.textContent = "This review link is incomplete or invalid.";
  }
})();
""".strip()

_SAVE_JS = """
(() => {
  const form = document.getElementById("save-review");
  if (!form) return;
  form.addEventListener("submit", () => {
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    button.textContent = "Saving…";
  });
})();
""".strip()


def _hash(value):
    digest = hashlib.sha256(value.encode()).digest()
    return base64.b64encode(digest).decode()


_STYLE_HASH = _hash(_STYLE)
_BOOTSTRAP_HASH = _hash(_BOOTSTRAP_JS)
_SAVE_HASH = _hash(_SAVE_JS)


def derive_csrf(browser, capability):
    """Derive a stable CSRF token from the secret browser binding and capability."""
    if not _SECRET.fullmatch(browser or "") or not _SECRET.fullmatch(capability or ""):
        raise ValueError("A valid browser binding and capability are required.")
    digest = hmac.new(
        browser.encode(), b"ticket-write-csrf\0" + capability.encode(), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _headers(*, script_hash=None):
    policy = [
        "default-src 'none'",
        "form-action 'self'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
        f"style-src 'sha256-{_STYLE_HASH}'",
    ]
    if script_hash:
        policy.append(f"script-src 'sha256-{script_hash}'")
    return {
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "same-origin",
        "Content-Security-Policy": "; ".join(policy),
    }


def _plain(message, status):
    return PlainTextResponse(message, status_code=status, headers=_headers())


def _page(title, content, *, status=200, script=None, script_hash=None):
    script_markup = f"<script>{script}</script>" if script else ""
    body = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><main>{content}</main>{script_markup}</body></html>"
    )
    return HTMLResponse(body, status_code=status, headers=_headers(script_hash=script_hash))


def _safe(value):
    return html.escape(str(value), quote=True)


def _format_expiry(value):
    moment = datetime.fromtimestamp(value, timezone.utc)
    return moment.strftime("%B %-d, %Y at %H:%M UTC"), moment.isoformat()


class _RateLimiter:
    def __init__(self, store):
        self.store = store

    def _check(self, path):
        now = time.time()
        with self.store.transaction() as db:
            bucket = self.store.get(db, "rate", path) or {"start": now, "count": 0}
            if now - bucket["start"] >= _RATE_SECONDS:
                bucket = {"start": now, "count": 0}
            bucket["count"] += 1
            self.store.put(db, "rate", path, bucket)
            return bucket["count"] <= _RATE_LIMIT

    async def check(self, path):
        try:
            return await run_in_threadpool(self._check, path)
        except Exception:
            return False


class _ExactMethodEndpoint:
    """Handle method errors inside the write surface so headers stay hardened."""

    def __init__(self, method, endpoint):
        self.method = method
        self.endpoint = endpoint

    async def __call__(self, scope, receive, send):
        if scope["method"] != self.method:
            response = _plain("Method not allowed.", 405)
            response.headers["Allow"] = self.method
        else:
            response = await self.endpoint(Request(scope, receive))
        await response(scope, receive, send)


async def _bounded_form(request, expected, public_url):
    if request.url.query:
        return None, _plain("Invalid request.", 400)
    if request.headers.get("origin") != public_url:
        return None, _plain("Request origin was not accepted.", 403)
    content_type = request.headers.get("content-type", "").strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        return None, _plain("Unsupported request content type.", 415)
    try:
        length = int(request.headers.get("content-length", "0"))
    except ValueError:
        return None, _plain("Invalid request.", 400)
    if length > _MAX_BODY:
        return None, _plain("Request body is too large.", 413)
    body = bytearray()
    try:
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > _MAX_BODY:
                return None, _plain("Request body is too large.", 413)
    except (ClientDisconnect, RuntimeError):
        return None, _plain("Invalid request body.", 400)
    try:
        pairs = parse_qsl(
            body.decode("utf-8"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=len(expected),
        )
    except (UnicodeError, ValueError):
        return None, _plain("Invalid request form.", 400)
    if len(pairs) != len(expected) or {key for key, _ in pairs} != set(expected):
        return None, _plain("Invalid request form.", 400)
    values = dict(pairs)
    if any(not _SECRET.fullmatch(values[name]) for name in expected):
        return None, _plain("Invalid request form.", 400)
    return values, None


def _browser(request):
    value = request.cookies.get(_COOKIE)
    return value if _SECRET.fullmatch(value or "") else None


def _status_page(title, message, *, status=200, ticket_url=None):
    link = ""
    if ticket_url:
        link = (
            f'<a href="{_safe(ticket_url)}" rel="noreferrer noopener">'
            "Open the ticket in TeamDynamix</a>"
        )
    content = (
        '<header class="intro"><p class="eyebrow">Ticket change</p>'
        f"<h1>{_safe(title)}</h1><p>{_safe(message)}</p></header>"
        '<div class="actions">'
        f"{link}"
        '<a href="https://chatgpt.com" rel="noreferrer noopener">Return to ChatGPT</a>'
        "</div>"
    )
    return _page(title, content, status=status)


def _failure(error):
    if isinstance(error, WriteExpiredError):
        return _status_page("Expired", "This review approval has expired.", status=410)
    if isinstance(error, WritesDisabled):
        return _status_page("Writes disabled", "Ticket writes are currently disabled.", status=403)
    if isinstance(error, (WriteAuthorizationRequired, LinkedIdentityMismatch)):
        return _status_page(
            "No longer authorized", "This change no longer has current authorization.", status=403
        )
    if isinstance(error, WriteBindingError):
        return _status_page(
            "Review unavailable", "This review belongs to another browser.", status=403
        )
    if isinstance(error, WriteNotFoundError):
        return _status_page("Review unavailable", "This review link is unavailable.", status=404)
    if isinstance(error, WriteIntegrityError):
        return _status_page("Review unavailable", "This review could not be verified.", status=409)
    if isinstance(error, (WriteStoreError, TicketWriteServiceError)):
        return _status_page("Review unavailable", "This review cannot be opened safely.", status=400)
    return _status_page("Review unavailable", "This review is temporarily unavailable.", status=503)


def _render_record(service, record, capability, csrf):
    status = service.status_for_record(record)
    if status.outcome != "pending":
        return _render_status(status)
    preview = record.prepared.preview
    expiry_text, expiry_value = _format_expiry(record.expires_at)
    visibility = preview.visibility.title() if preview.visibility else "Not applicable"
    recipients = (
        ", ".join(_safe(value) for value in preview.recipients)
        if preview.recipients
        else "No email notifications requested"
    )
    fields = []
    for field in preview.fields:
        before = "Not set" if field.before is None else field.before
        after = "Not set" if field.after is None else field.after
        fields.append(
            '<section class="change">'
            f"<h2>{_safe(field.name)}</h2>"
            '<div class="comparison">'
            f'<div><p class="eyebrow">Before</p><p class="value">{_safe(before)}</p></div>'
            f'<div><p class="eyebrow">After</p><p class="value">{_safe(after)}</p></div>'
            "</div></section>"
        )
    notices = "".join(f"<li>{_safe(notice)}</li>" for notice in preview.notices)
    content = (
        '<header class="intro"><p class="eyebrow">Review ticket change</p>'
        f"<h1>{_safe(preview.ticket_title)}</h1>"
        '<p class="status">Not saved</p></header>'
        '<dl class="meta">'
        f"<dt>Application</dt><dd>{_safe(preview.application)}</dd>"
        f"<dt>Ticket</dt><dd>{preview.ticket_id}</dd>"
        f"<dt>Action</dt><dd>{_safe(preview.action.title())}</dd>"
        f"<dt>Visibility</dt><dd>{_safe(visibility)}</dd>"
        f"<dt>Recipients</dt><dd>{recipients}</dd>"
        f'<dt>Approval expires</dt><dd><time datetime="{_safe(expiry_value)}">'
        f"{_safe(expiry_text)}</time></dd></dl>"
        f'<div class="changes">{"".join(fields)}</div>'
        f'<section><h2>Before you save</h2><ul class="notices">{notices}</ul></section>'
        '<div class="actions"><form id="save-review" method="post" action="/writes/save">'
        f'<input type="hidden" name="cap" value="{_safe(capability)}">'
        f'<input type="hidden" name="csrf" value="{_safe(csrf)}">'
        '<button type="submit">Save change</button></form>'
        f'<a href="{_safe(status.ticket_url)}" rel="noreferrer noopener">View ticket</a>'
        '<a href="https://chatgpt.com" rel="noreferrer noopener">Return to ChatGPT</a></div>'
    )
    return _page(
        "Review ticket change", content, script=_SAVE_JS, script_hash=_SAVE_HASH
    )


def _render_status(status):
    titles = {
        "applied": "Saved",
        "rejected": "Rejected",
        "unknown": "Outcome unknown",
        "conflict": "Conflict",
        "expired": "Expired",
    }
    title = titles.get(status.outcome, "Review unavailable")
    return _status_page(title, status.message, ticket_url=status.ticket_url)


def create_retired_ticket_write_routes():
    """Keep old links inert; no request parsing, service access, or credentials."""
    class RetiredEndpoint:
        async def __call__(self, scope, receive, send):
            response = PlainTextResponse(
                "This review page has been retired and cannot submit changes. "
                "Return to ChatGPT and explicitly request the ticket update there.",
                status_code=410,
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )
            await response(scope, receive, send)

    return [Route(path, RetiredEndpoint()) for path in (
        "/writes/review", "/writes/open", "/writes/save",
    )]


def create_ticket_write_routes(settings, service, rate_store):
    """Create the three uncredentialed human review routes for a personal app."""
    limiter = _RateLimiter(rate_store)

    async def review(request):
        if not await limiter.check("/writes/review"):
            return _plain("Ticket review is temporarily unavailable.", 429)
        if request.url.query:
            return _plain("Invalid request.", 400)
        content = (
            '<header class="intro"><p class="eyebrow">Secure review</p>'
            '<h1>Opening your review</h1>'
            '<p id="bootstrap-message">Checking the approval link…</p></header>'
            '<form id="open-review" method="post" action="/writes/open">'
            '<input type="hidden" name="cap" value=""></form>'
            '<noscript><p>JavaScript is required to open this review link safely.</p></noscript>'
        )
        return _page(
            "Opening review",
            content,
            script=_BOOTSTRAP_JS,
            script_hash=_BOOTSTRAP_HASH,
        )

    async def open_route(request):
        if not await limiter.check("/writes/open"):
            return _plain("Ticket review is temporarily unavailable.", 429)
        values, error = await _bounded_form(request, ("cap",), settings.public_url)
        if error:
            return error
        browser = _browser(request) or secrets.token_urlsafe(32)
        capability = values["cap"]
        csrf = derive_csrf(browser, capability)
        try:
            record = await run_in_threadpool(
                service.open_review, capability, browser, csrf
            )
            response = _render_record(service, record, capability, csrf)
            response.set_cookie(
                _COOKIE,
                browser,
                max_age=600,
                secure=True,
                httponly=True,
                samesite="strict",
                path="/",
            )
            return response
        except Exception as exc:
            return _failure(exc)

    async def save(request):
        if not await limiter.check("/writes/save"):
            return _plain("Ticket save is temporarily unavailable.", 429)
        values, error = await _bounded_form(
            request, ("cap", "csrf"), settings.public_url
        )
        if error:
            return error
        browser = _browser(request)
        if browser is None:
            return _plain("Browser approval binding is required.", 403)
        expected = derive_csrf(browser, values["cap"])
        if not hmac.compare_digest(expected, values["csrf"]):
            return _plain("Approval validation failed.", 403)
        try:
            status = await run_in_threadpool(
                service.commit, values["cap"], browser, values["csrf"]
            )
            return _render_status(status)
        except Exception as exc:
            return _failure(exc)

    return [
        Route("/writes/review", _ExactMethodEndpoint("GET", review)),
        Route("/writes/open", _ExactMethodEndpoint("POST", open_route)),
        Route("/writes/save", _ExactMethodEndpoint("POST", save)),
    ]
