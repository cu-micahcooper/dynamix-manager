import asyncio
import re
import time
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from cryptography.fernet import Fernet
from starlette.applications import Starlette
from starlette.testclient import TestClient

from dynamix_manager.hosted_vault import CredentialVault
from dynamix_manager.personal_auth_store import OAuthStore
from dynamix_manager.ticket_writes.models import (
    ChangePreview,
    PreparedChange,
    PreviewField,
    WriteResult,
    parse_action,
)
from dynamix_manager.ticket_writes.service import TicketWriteService
from dynamix_manager.ticket_writes.store import WriteStore


UID = "11111111-1111-4111-8111-111111111111"
ORIGIN = "https://connector.example"
BASE = "https://tenant.example/TDWebApi"


class Provider:
    def __init__(self, vault):
        self.revoked = False
        self.store = OAuthStore(vault)
        self.binding = {
            "subject": UID,
            "client_id": "client-1",
            "resource": ORIGIN + "/mcp",
            "family": "family-1",
            "scopes": ["tdx.read", "tdx.write"],
            "grant_expiry": 10_000.0,
        }

    def validate_write_grant(self, binding):
        if self.revoked or binding != self.binding:
            raise RuntimeError("revoked secret")
        return dict(self.binding)


class Runtime:
    enabled = True

    def require_enabled(self):
        if not self.enabled:
            raise RuntimeError("disabled secret")


class Adapter:
    def __init__(self, fixture):
        self.fixture = fixture

    def validate(self, action):
        return self.fixture.prepared_by_ticket[action.ticket_id]

    def apply_once(self, prepared):
        self.fixture.apply_count += 1
        if self.fixture.outcome == "exception":
            raise TimeoutError("private upstream response")
        return WriteResult(
            outcome=self.fixture.outcome,
            message="untrusted upstream text",
            status_code=200 if self.fixture.outcome == "applied" else 403,
        )

    def snapshot(self, ticket_id):
        return {}


def make_prepared(ticket_id=1001, *, title="A ticket", value="New value"):
    action = parse_action(dict(kind="edit", ticket_id=ticket_id, description=value))
    preview = ChangePreview(
        application="InfoTech Tickets",
        ticket_id=ticket_id,
        ticket_title=title,
        action="edit",
        fields=(PreviewField(name="Description", before="Old value", after=value),),
        visibility=None,
        recipients=(),
        notices=(
            "Read access does not guarantee write permission.",
            "Preflight cannot prevent an external race.",
        ),
    )
    return PreparedChange(
        action=action,
        base_url=BASE,
        app_id=42,
        baseline_json='{"ID":1001}',
        payload_json='[{"field":"Description","value":"New value"}]',
        preview=preview,
    )


@pytest.fixture
def routes(tmp_path):
    from dynamix_manager.ticket_writes.routes import create_ticket_write_routes

    clock = [1_000.0]
    vault = CredentialVault(tmp_path / "vault.sqlite", Fernet.generate_key())
    vault.put(ORIGIN, UID, UID, "personal-token", time.time() + 3600)
    provider, runtime = Provider(vault), Runtime()
    fixture = SimpleNamespace(
        apply_count=0,
        outcome="applied",
        prepared_by_ticket={},
    )
    store = WriteStore(vault, clock=lambda: clock[0])
    settings = SimpleNamespace(
        public_url=ORIGIN,
        issuer=ORIGIN,
        tdx_url=BASE,
        tdx_client_id="8",
    )

    class Connection:
        def __init__(self):
            self.client = SimpleNamespace(session=SimpleNamespace(close=lambda: None))

        def identity(self):
            return UID

    service = TicketWriteService(
        settings,
        vault,
        provider,
        runtime,
        store=store,
        connection_factory=lambda values: Connection(),
        adapter_factory=lambda connection: Adapter(fixture),
    )
    app = Starlette(routes=create_ticket_write_routes(settings, service, provider.store))
    client = TestClient(app, base_url=ORIGIN)

    def issue(prepared=None):
        prepared = prepared or make_prepared()
        fixture.prepared_by_ticket[prepared.action.ticket_id] = prepared
        issued = store.prepare(provider.binding, prepared)
        return issued.capability

    yield SimpleNamespace(
        client=client,
        issue=issue,
        clock=clock,
        provider=provider,
        runtime=runtime,
        fixture=fixture,
    )
    client.close()


def open_review(routes, capability, *, client=None):
    client = client or routes.client
    assert client.get("/writes/review").status_code == 200
    response = client.post(
        "/writes/open",
        content=urlencode({"cap": capability}),
        headers={"Origin": ORIGIN, "Content-Type": "application/x-www-form-urlencoded"},
    )
    cookie = client.cookies.get("__Host-tdx-write")
    assert cookie and len(cookie) == 43
    # The server derives this stable value from the HttpOnly browser cookie.
    from dynamix_manager.ticket_writes.routes import derive_csrf

    csrf = derive_csrf(cookie, capability)
    return response, cookie, csrf


def assert_hardened(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "same-origin"
    csp = response.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "form-action 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'none'" in csp


def test_review_get_is_data_free_fragment_bootstrap_and_does_not_replace_browser_cookie(routes):
    response = routes.client.get("/writes/review")
    assert response.status_code == 200
    assert "location.hash" in response.text
    assert "history.replaceState" in response.text
    assert 'action="/writes/open"' in response.text
    assert 'name="cap"' in response.text
    assert "ticket" not in response.text.lower()
    assert not response.request.url.query
    assert_hardened(response)
    csp = response.headers["content-security-policy"]
    assert re.search(r"script-src 'sha256-[A-Za-z0-9+/=]+'", csp)
    assert "set-cookie" not in response.headers


def test_successful_same_origin_open_sets_and_reissues_strict_browser_cookie(routes):
    first_cap = routes.issue(make_prepared(1001))
    response, first_cookie, _ = open_review(routes, first_cap)
    cookie_header = response.headers["set-cookie"]
    assert all(value in cookie_header for value in ("Secure", "HttpOnly", "SameSite=strict", "Path=/"))
    assert "Max-Age=600" in cookie_header
    assert "Domain=" not in cookie_header

    # A later cross-site entry reaches the data-free shell without a Strict
    # cookie; that GET must not overwrite the browser's established binding.
    second_cap = routes.issue(make_prepared(1002))
    shell = routes.client.get(
        "/writes/review", headers={"Referer": "https://chatgpt.com/"}
    )
    assert "set-cookie" not in shell.headers
    _, second_cookie, _ = open_review(routes, second_cap)
    assert second_cookie == first_cookie


@pytest.mark.parametrize("suffix", ["?cap=secret", "?x=1&x=2"])
def test_review_get_rejects_every_query_and_never_echoes_it(routes, suffix):
    response = routes.client.get("/writes/review" + suffix)
    assert response.status_code == 400
    assert "secret" not in response.text
    assert_hardened(response)


def test_open_renders_complete_escaped_preview_without_dispatch(routes):
    prepared = make_prepared(
        title='<script>location="https://evil.invalid"</script>',
        value="A" * 5000 + "<&\" end",
    )
    cap = routes.issue(prepared)
    response, _, csrf = open_review(routes, cap)
    assert response.status_code == 200
    assert routes.fixture.apply_count == 0
    assert "&lt;script&gt;" in response.text and "<script>location=" not in response.text
    assert "A" * 5000 in response.text
    assert "Old value" in response.text
    assert "Before" in response.text and "After" in response.text
    assert "No email notifications requested" in response.text
    assert "Preflight cannot prevent an external race" in response.text
    assert "Not saved" in response.text
    assert 'action="/writes/save"' in response.text
    assert f'value="{csrf}"' in response.text
    assert f'value="{cap}"' in response.text
    assert "https://chatgpt.com" in response.text
    assert 'rel="noreferrer noopener"' in response.text
    assert_hardened(response)


@pytest.mark.parametrize(
    ("path", "body", "headers", "status"),
    [
        ("/writes/open?x=1", {"cap": "x" * 43}, {}, 400),
        ("/writes/open", {"cap": "x" * 43}, {}, 403),
        ("/writes/open", {"cap": "x" * 43}, {"Origin": "null"}, 403),
        ("/writes/open", {"cap": "x" * 43}, {"Origin": "https://evil.invalid"}, 403),
        ("/writes/open", {"cap": "x" * 43}, {"Origin": ORIGIN, "Content-Type": "application/json"}, 415),
    ],
)
def test_open_rejects_query_origin_and_content_type_before_capability_lookup(
    routes, path, body, headers, status
):
    headers = {"Content-Type": "application/x-www-form-urlencoded", **headers}
    response = routes.client.post(path, content=urlencode(body), headers=headers)
    assert response.status_code == status
    assert "x" * 43 not in response.text
    assert_hardened(response)


@pytest.mark.parametrize(
    "body",
    [
        "cap=" + "x" * 43 + "&cap=" + "y" * 43,
        "cap=" + "x" * 43 + "&action=replace",
        "unknown=value",
    ],
)
def test_open_rejects_repeated_or_unknown_fields(routes, body):
    routes.client.get("/writes/review")
    response = routes.client.post(
        "/writes/open",
        content=body,
        headers={"Origin": ORIGIN, "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 400
    assert_hardened(response)


def test_open_rejects_oversized_body_without_parsing(routes):
    routes.client.get("/writes/review")
    response = routes.client.post(
        "/writes/open",
        content="cap=" + "x" * 4096,
        headers={"Origin": ORIGIN, "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 413
    assert_hardened(response)


def test_same_browser_repeat_parallel_previews_and_stolen_bound_link(routes):
    first, second = routes.issue(make_prepared(1001)), routes.issue(make_prepared(1002))
    response, cookie, _ = open_review(routes, first)
    assert response.status_code == 200
    assert open_review(routes, first)[0].status_code == 200
    assert open_review(routes, second)[0].status_code == 200

    thief = TestClient(routes.client.app, base_url=ORIGIN)
    assert thief.get("/writes/review").status_code == 200
    stolen = thief.post(
        "/writes/open",
        content=urlencode({"cap": first}),
        headers={"Origin": ORIGIN, "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert stolen.status_code == 403
    assert thief.cookies.get("__Host-tdx-write") is None
    assert cookie not in stolen.text
    thief.close()


@pytest.mark.parametrize(("change", "expected"), [("expired", "Expired"), ("revoked", "No longer authorized"), ("disabled", "Writes disabled")])
def test_expired_revoked_or_disabled_after_view_has_no_save(routes, change, expected):
    cap = routes.issue()
    opened, _, _ = open_review(routes, cap)
    assert opened.status_code == 200
    if change == "expired":
        routes.clock[0] = 1_301.0
    elif change == "revoked":
        routes.provider.revoked = True
    else:
        routes.runtime.enabled = False
    reopened, _, _ = open_review(routes, cap)
    assert expected in reopened.text
    assert 'action="/writes/save"' not in reopened.text
    assert_hardened(reopened)


def test_save_accepts_only_cap_and_csrf_and_duplicate_dispatches_once(routes):
    cap = routes.issue()
    opened, _, csrf = open_review(routes, cap)
    assert opened.status_code == 200
    headers = {"Origin": ORIGIN, "Content-Type": "application/x-www-form-urlencoded"}
    response = routes.client.post(
        "/writes/save", content=urlencode({"cap": cap, "csrf": csrf}), headers=headers
    )
    duplicate = routes.client.post(
        "/writes/save", content=urlencode({"cap": cap, "csrf": csrf}), headers=headers
    )
    assert response.status_code == duplicate.status_code == 200
    assert routes.fixture.apply_count == 1
    assert "Saved" in response.text and "TeamDynamix accepted" in response.text
    assert 'action="/writes/save"' not in response.text
    assert_hardened(response)


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("rejected", "Rejected"),
        ("exception", "Outcome unknown"),
    ],
)
def test_save_terminal_outcomes_are_distinct_and_never_offer_retry(routes, outcome, expected):
    routes.fixture.outcome = outcome
    cap = routes.issue()
    _, _, csrf = open_review(routes, cap)
    response = routes.client.post(
        "/writes/save",
        content=urlencode({"cap": cap, "csrf": csrf}),
        headers={"Origin": ORIGIN, "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert expected in response.text
    assert 'action="/writes/save"' not in response.text
    if outcome == "exception":
        assert "do not retry" in response.text.lower()


def test_save_rejects_missing_bad_csrf_and_replacement_fields(routes):
    cap = routes.issue()
    _, _, csrf = open_review(routes, cap)
    headers = {"Origin": ORIGIN, "Content-Type": "application/x-www-form-urlencoded"}
    for body in (
        {"cap": cap},
        {"cap": cap, "csrf": "x" * 43},
        {"cap": cap, "csrf": csrf, "title": "replacement"},
    ):
        response = routes.client.post("/writes/save", content=urlencode(body), headers=headers)
        assert response.status_code in (400, 403)
        assert routes.fixture.apply_count == 0
        assert_hardened(response)


def test_write_route_rate_limit_is_persistent_bounded_and_safe(routes):
    # The production OAuth store has five auth route keys plus exactly these three.
    assert routes.provider.store.CAPACITY["rate"] == 8
    limited = None
    for _ in range(31):
        limited = routes.client.get("/writes/review")
    assert limited.status_code == 429
    assert "temporarily unavailable" in limited.text.lower()
    assert_hardened(limited)


@pytest.mark.parametrize(
    ("method", "path"),
    [("post", "/writes/review"), ("get", "/writes/open"), ("get", "/writes/save")],
)
def test_unsupported_write_route_methods_are_hardened(routes, method, path):
    response = getattr(routes.client, method)(path)
    assert response.status_code == 405
    assert_hardened(response)


@pytest.mark.parametrize("path", ["/writes/open", "/writes/save"])
def test_disconnected_write_request_body_returns_hardened_fixed_error(routes, path):
    messages = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "server": ("connector.example", 443),
        "client": ("127.0.0.1", 12345),
        "headers": [
            (b"host", b"connector.example"),
            (b"origin", ORIGIN.encode()),
            (b"content-type", b"application/x-www-form-urlencoded"),
            (b"content-length", b"100"),
        ],
    }

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    asyncio.run(routes.client.app(scope, receive, send))
    start = next(message for message in messages if message["type"] == "http.response.start")
    headers = {key.decode(): value.decode() for key, value in start["headers"]}
    assert start["status"] == 400
    assert headers["cache-control"] == "no-store"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "same-origin"
    assert "default-src 'none'" in headers["content-security-policy"]
