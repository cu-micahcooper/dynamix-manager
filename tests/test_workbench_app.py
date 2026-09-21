from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from dynamix_manager.workbench.app import create_app
from dynamix_manager.workbench.demo import DemoAdapter


def client(tmp_path, adapter=None):
    a = create_app(
        data_dir=tmp_path / "data",
        adapter_factory=lambda body: adapter or DemoAdapter(),
    )
    c = TestClient(a, base_url="http://127.0.0.1:8765")
    c.headers["X-CSRF-Token"] = c.get("/api/session").json()["csrf_token"]
    assert c.post("/api/login", json={"mode": "demo"}).status_code == 200
    return c


def preview(c):
    return c.post(
        "/api/tickets/101/preview",
        json={
            "text": "Reviewed message",
            "visibility": "public",
            "recipients": ["requester-1"],
            "status_id": None,
        },
    ).json()["preview_id"]


def test_security_and_logout(tmp_path):
    c = client(tmp_path)
    assert c.get("/api/session", headers={"Host": "evil.example"}).status_code == 403
    assert (
        c.post(
            "/api/logout", json={}, headers={"Origin": "http://evil.example"}
        ).status_code
        == 403
    )
    assert (
        c.post("/api/logout", json={}, headers={"X-CSRF-Token": "wrong"}).status_code
        == 403
    )
    assert c.post("/api/logout", json={}).status_code == 200
    assert c.get("/api/queue").status_code == 401


def test_demo_drafts_suggest_skip_and_send(tmp_path):
    c = client(tmp_path)
    assert c.get("/api/queue").json()["tickets"]
    assert (
        c.put(
            "/api/tickets/101/draft",
            json={"text": "Local edit", "visibility": "internal"},
        ).status_code
        == 200
    )
    assert (
        c.get("/api/tickets/101/draft?visibility=internal").json()["text"]
        == "Local edit"
    )
    assert c.get("/api/tickets/101/draft?visibility=public").json()["text"] == ""
    assert c.post("/api/tickets/101/suggest", json={"visibility": "public"}).json()[
        "simulated"
    ]
    assert c.post("/api/tickets/101/skip", json={}).status_code == 200
    assert c.get("/api/queue").json()["skipped"]
    c.post("/api/reset-pass", json={})
    p = preview(c)
    assert (
        c.post(
            "/api/tickets/101/submit", json={"preview_id": p, "text": "tampered"}
        ).json()["status"]
        == "confirmed"
    )
    assert c.post("/api/tickets/101/submit", json={"preview_id": p}).status_code == 409
    assert c.get("/api/queue").json()["reviewed"]


def test_stale_context_preserves_draft(tmp_path):
    adapter = DemoAdapter()
    c = client(tmp_path, adapter)
    p = preview(c)
    adapter.tickets[101]["modified"] = "2099-01-01T00:00:00Z"
    assert c.post("/api/tickets/101/submit", json={"preview_id": p}).status_code == 409
    assert c.get("/api/tickets/101/draft").json()["text"] == "Reviewed message"


def test_uncertain_blocks_retry_until_acknowledged(tmp_path):
    class Uncertain(DemoAdapter):
        def submit(self, *args):
            raise TimeoutError("private remote error")

    c = client(tmp_path, Uncertain())
    p = preview(c)
    r = c.post("/api/tickets/101/submit", json={"preview_id": p})
    assert r.json()["status"] == "uncertain"
    assert "private remote error" not in r.text
    assert (
        c.post(
            "/api/tickets/101/preview", json={"text": "again", "visibility": "internal"}
        ).status_code
        == 409
    )
    assert (
        c.post("/api/tickets/101/acknowledge", json={"acknowledged": False}).status_code
        == 422
    )
    assert (
        c.post("/api/tickets/101/acknowledge", json={"acknowledged": True}).status_code
        == 200
    )


def test_duplicate_concurrent_submission(tmp_path):
    c = client(tmp_path)
    p = preview(c)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: c.post("/api/tickets/101/submit", json={"preview_id": p}),
                range(2),
            )
        )
    assert sorted(r.status_code for r in responses) == [200, 409]


def test_invalid_recipients_and_incomplete_generation(tmp_path):
    a = DemoAdapter()
    c = client(tmp_path, a)
    assert (
        c.post(
            "/api/tickets/101/preview",
            json={"text": "x", "visibility": "public", "recipients": ["outsider"]},
        ).status_code
        == 422
    )
    a.tickets[101]["history_complete"] = False
    assert (
        c.post("/api/tickets/101/suggest", json={"visibility": "public"}).status_code
        == 409
    )


def test_uncertain_restored_and_skipped_only_reset(tmp_path):
    c = client(tmp_path)
    c.post("/api/tickets/101/skip", json={})
    p = c.post(
        "/api/tickets/102/preview", json={"text": "note", "visibility": "internal"}
    ).json()["preview_id"]
    c.post("/api/tickets/102/submit", json={"preview_id": p})
    c.post("/api/reset-pass", json={"skipped_only": True})
    q = c.get("/api/queue").json()
    assert [t["id"] for t in q["reviewed"]] == [102]
    assert any(t["id"] == 101 for t in q["tickets"])


def test_draft_roundtrip_survives_logout(tmp_path):
    c = client(tmp_path)
    c.put("/api/tickets/101/draft", json={"text": "Saved", "visibility": "public"})
    c.post("/api/logout", json={})
    c.post("/api/login", json={"mode": "demo"})
    assert c.get("/api/tickets/101/draft").json()["text"] == "Saved"


def test_stale_before_preview(tmp_path):
    a = DemoAdapter()
    c = client(tmp_path, a)
    revision = c.get("/api/tickets/101").json()["source_revision"]
    a.tickets[101]["modified"] = "2099-01-01T00:00:00Z"
    response = c.post(
        "/api/tickets/101/preview",
        json={
            "text": "old context",
            "visibility": "internal",
            "source_revision": revision,
        },
    )
    assert response.status_code == 409


def test_ai_settings_persist_locally_across_logout_and_restart(tmp_path):
    (tmp_path / ".env").write_text("# Keep tenant settings\nTDX_APP_ID=634\n")
    c = client(tmp_path)
    result = c.post(
        "/api/ai-settings",
        json={"api_key": "sk-secret-test", "model": "configured-model"},
    )
    assert result.status_code == 200
    session = c.get("/api/session")
    assert session.json()["capabilities"]["ai_configured"]
    assert session.json()["capabilities"]["model"] == "configured-model"
    assert "sk-secret-test" not in session.text
    assert (
        b"sk-secret-test" not in (tmp_path / "data" / "workbench.sqlite3").read_bytes()
    )
    c.post("/api/logout", json={})
    assert c.get("/api/session").json()["capabilities"]["ai_configured"]
    env = tmp_path / ".env"
    assert "sk-secret-test" in env.read_text()
    assert "# Keep tenant settings" in env.read_text()
    assert "TDX_APP_ID=634" in env.read_text()
    assert env.stat().st_mode & 0o777 == 0o600
    restarted = client(tmp_path)
    assert (
        restarted.get("/api/session").json()["capabilities"]["model"]
        == "configured-model"
    )
    assert "sk-secret-test" not in restarted.get("/api/session").text


def test_uncertain_changed_ticket_remains_recoverable_after_restart(tmp_path):
    class AcceptedThenTimeout(DemoAdapter):
        def submit(self, identity, payload):
            super().submit(identity, payload)
            self.tickets[int(identity)]["status_class"] = 3
            self.tickets[int(identity)]["assigned_id"] = "other-user"
            raise TimeoutError()

    a = AcceptedThenTimeout()
    c = client(tmp_path, a)
    p = preview(c)
    assert (
        c.post("/api/tickets/101/submit", json={"preview_id": p}).json()["status"]
        == "uncertain"
    )
    c = client(tmp_path, a)
    q = c.get("/api/queue").json()
    assert next(t for t in q["tickets"] if t["id"] == 101)["uncertain"]
    assert c.get("/api/tickets/101").json()["uncertain"]
    assert c.get("/api/tickets/101/draft").status_code == 200
    assert c.post("/api/tickets/101/reconcile", json={}).json()["status"] == "uncertain"
    assert (
        c.post(
            "/api/tickets/101/preview", json={"text": "repeat", "visibility": "public"}
        ).status_code
        == 409
    )
    del a.tickets[101]
    assert next(t for t in c.get("/api/queue").json()["tickets"] if t["id"] == 101)[
        "uncertain"
    ]
    assert c.get("/api/tickets/101").json()["history_complete"] is False
    assert c.post("/api/tickets/101/reconcile", json={}).status_code == 200
    assert (
        c.post("/api/tickets/101/acknowledge", json={"acknowledged": True}).status_code
        == 200
    )
    assert all(t["id"] != 101 for t in c.get("/api/queue").json()["tickets"])


def test_blank_live_login_never_uses_environment_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("TDX_USERNAME", "configured-service")
    monkeypatch.setenv("TDX_PASSWORD", "secret-env-password")
    monkeypatch.setenv("TDX_TOKEN", "secret-env-token")
    calls = []
    app = create_app(
        data_dir=tmp_path / "data",
        adapter_factory=lambda body: calls.append(body) or DemoAdapter(),
    )
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    c.headers["X-CSRF-Token"] = c.get("/api/session").json()["csrf_token"]
    for body in (
        {"mode": "live"},
        {"mode": "live", "username": "only-user"},
        {"mode": "live", "password": "only-password"},
    ):
        response = c.post("/api/login", json=body)
        assert response.status_code == 422
        assert "personal TeamDynamix" in response.json()["detail"]
    assert not calls
    assert (
        c.post(
            "/api/login", json={"mode": "live", "token": "explicit-personal-token"}
        ).status_code
        == 200
    )
    assert calls == [{"mode": "live", "token": "explicit-personal-token"}]


def test_inflight_submit_serializes_account_switch(tmp_path):
    import threading
    from time import sleep

    entered, release = threading.Event(), threading.Event()

    class SlowSubmit(DemoAdapter):
        def submit(self, identity, payload):
            entered.set()
            assert release.wait(5)
            return super().submit(identity, payload)

    class OtherAccount(DemoAdapter):
        def login(self):
            return {
                "id": "other-account",
                "name": "Other Analyst",
                "email": "other@example.invalid",
            }

    first = SlowSubmit()
    app = create_app(
        data_dir=tmp_path / "data",
        adapter_factory=lambda body: (
            OtherAccount() if body.get("token") == "other" else first
        ),
    )
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    c.headers["X-CSRF-Token"] = c.get("/api/session").json()["csrf_token"]
    c.post("/api/login", json={"mode": "demo"})
    p = preview(c)
    with ThreadPoolExecutor(max_workers=2) as pool:
        submitting = pool.submit(
            c.post, "/api/tickets/101/submit", json={"preview_id": p}
        )
        assert entered.wait(5)
        switching = pool.submit(
            c.post, "/api/login", json={"mode": "live", "token": "other"}
        )
        try:
            sleep(0.15)
            assert not switching.done(), (
                "Account switch must wait for in-flight submission"
            )
        finally:
            release.set()
        assert submitting.result().json()["status"] == "confirmed"
        assert switching.result().status_code == 200
    c.post("/api/login", json={"mode": "demo"})
    assert [t["id"] for t in c.get("/api/queue").json()["reviewed"]] == [101]


def test_logout_waits_for_pending_login_and_cannot_be_resurrected(tmp_path):
    import threading
    from time import sleep

    entered, release = threading.Event(), threading.Event()

    class SlowLogin(DemoAdapter):
        def login(self):
            entered.set()
            assert release.wait(5)
            return super().login()

    app = create_app(
        data_dir=tmp_path / "data", adapter_factory=lambda body: SlowLogin()
    )
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    c.headers["X-CSRF-Token"] = c.get("/api/session").json()["csrf_token"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        login = pool.submit(c.post, "/api/login", json={"mode": "demo"})
        assert entered.wait(5)
        logout = pool.submit(c.post, "/api/logout", json={})
        try:
            sleep(0.15)
            assert not logout.done()
        finally:
            release.set()
        assert login.result().status_code == 200
        assert logout.result().status_code == 200
    assert c.get("/api/session").json()["authenticated"] is False


def test_ticket_html_is_sanitized_without_changing_revision(tmp_path):
    from dynamix_manager.workbench.domain import revision

    adapter = DemoAdapter()
    source = '<div>Accounts Payable<br><strong>Example University</strong></div><a href="mailto:ap@example.edu">Email</a><img src="https://tracker.invalid/pixel">'
    adapter.tickets[101]["description"] = source
    adapter.tickets[101]["history"][0]["text"] = (
        "<table><tr><td>Invoice</td><td>Pending</td></tr></table>"
    )
    c = client(tmp_path, adapter)
    result = c.get("/api/tickets/101").json()
    assert result["description"] == source
    assert "<strong>Example University</strong>" in result["description_html"]
    assert "<img" not in result["description_html"]
    assert "<table>" in result["history"][0]["text_html"]
    assert result["source_revision"] == revision(adapter.ticket(101))


def test_safe_generation_error_reaches_user_without_provider_details(
    tmp_path, monkeypatch
):
    from dynamix_manager.workbench.ai import SuggestionError

    c = client(tmp_path)
    assert (
        c.post(
            "/api/login", json={"mode": "live", "token": "synthetic-test-token"}
        ).status_code
        == 200
    )
    assert (
        c.post(
            "/api/ai-settings",
            json={"api_key": "synthetic-key", "model": "gpt-5.4-mini"},
        ).status_code
        == 200
    )

    def fail(*args):
        raise SuggestionError(
            "OpenAI API billing quota is exhausted. Your draft is preserved."
        )

    monkeypatch.setattr("dynamix_manager.workbench.ai.suggest", fail)
    result = c.post("/api/tickets/101/suggest", json={"visibility": "public"})
    assert result.status_code == 502
    assert "billing quota" in result.json()["detail"]

    def unexpected(*args):
        raise RuntimeError("synthetic-key private-provider-body")

    monkeypatch.setattr("dynamix_manager.workbench.ai.suggest", unexpected)
    assert (
        "synthetic-key"
        not in c.post("/api/tickets/101/suggest", json={"visibility": "public"}).text
    )


def test_update_and_close_is_previewed_then_submitted_once(tmp_path):
    adapter = DemoAdapter()
    c = client(tmp_path, adapter)
    before = len(adapter.tickets[101]["history"])
    result = c.post(
        "/api/tickets/101/preview",
        json={
            "text": "Issue fixed and verified.",
            "visibility": "public",
            "recipients": ["requester-1"],
            "status_id": 3,
        },
    )
    assert result.status_code == 200
    p = result.json()
    assert p["new_status"] == "Resolved"
    assert len(adapter.tickets[101]["history"]) == before
    assert adapter.tickets[101]["status_id"] != 3
    submitted = c.post("/api/tickets/101/submit", json={"preview_id": p["preview_id"]})
    assert submitted.json()["status"] == "confirmed"
    assert adapter.tickets[101]["status_id"] == 3
    assert adapter.tickets[101]["history"][-1]["text"] == "Issue fixed and verified."
    assert all(t["id"] != 101 for t in c.get("/api/queue").json()["tickets"])
    assert (
        c.post(
            "/api/tickets/101/submit", json={"preview_id": p["preview_id"]}
        ).status_code
        == 409
    )


def test_ai_settings_write_failure_keeps_previous_settings(tmp_path, monkeypatch):
    from dynamix_manager.workbench import settings

    c = client(tmp_path)
    assert (
        c.post(
            "/api/ai-settings", json={"api_key": "old-test-key", "model": "old-model"}
        ).status_code
        == 200
    )
    before = (tmp_path / ".env").read_bytes()

    def fail(*args):
        raise OSError("private OS error")

    monkeypatch.setattr(settings.os, "replace", fail)
    result = c.post(
        "/api/ai-settings", json={"api_key": "new-test-key", "model": "new-model"}
    )
    assert result.status_code == 500
    assert "private OS error" not in result.text
    assert (tmp_path / ".env").read_bytes() == before
    assert c.get("/api/session").json()["capabilities"]["model"] == "old-model"
    assert not list(tmp_path.glob(".env-save-*"))


def test_ai_settings_rejects_newline_injection(tmp_path):
    c = client(tmp_path)
    result = c.post(
        "/api/ai-settings", json={"api_key": "test\nOTHER=value", "model": "model"}
    )
    assert result.status_code == 422
    assert not (tmp_path / ".env").exists()


def test_pdf_route_checks_ticket_membership_and_serves_inline(tmp_path):
    class PDFs(DemoAdapter):
        def pdf_attachment(self, identity):
            assert identity == "bill"
            return b"%PDF-1.4\nsynthetic"

    a = PDFs()
    a.tickets[101]["attachments"] = [{"id": "bill", "name": "bill.pdf"}]
    c = client(tmp_path, a)
    path = "/api/tickets/101/attachments/bill/pdf"
    result = c.get(path)
    assert result.status_code == 200
    assert result.headers["content-type"] == "application/pdf"
    assert result.headers["content-disposition"].startswith("inline;")
    assert result.headers["cache-control"] == "no-store"
    assert c.get("/api/tickets/102/attachments/bill/pdf").status_code == 404
    c.post("/api/logout", json={})
    assert c.get(path).status_code == 401


def test_personal_login_saved_reused_after_restart_and_forgotten(tmp_path):
    from dotenv import dotenv_values

    c = client(tmp_path)
    result = c.post(
        "/api/login",
        json={
            "mode": "live",
            "username": "personal-user",
            "password": "personal-secret",
            "remember": True,
        },
    )
    assert result.status_code == 200
    assert result.json()["capabilities"]["td_login_saved"]
    assert "personal-secret" not in result.text
    env = dotenv_values(tmp_path / ".env")
    assert env["WORKBENCH_PERSONAL_PASSWORD"] == "personal-secret"
    assert (tmp_path / ".env").stat().st_mode & 0o777 == 0o600
    seen = []
    app = create_app(
        data_dir=tmp_path / "data",
        adapter_factory=lambda body: seen.append(body) or DemoAdapter(),
    )
    restarted = TestClient(app, base_url="http://127.0.0.1:8765")
    restarted.headers["X-CSRF-Token"] = restarted.get("/api/session").json()[
        "csrf_token"
    ]
    assert (
        restarted.post(
            "/api/login", json={"mode": "live", "use_saved": True}
        ).status_code
        == 200
    )
    assert seen[-1]["password"] == "personal-secret"
    assert restarted.post("/api/forget-login", json={}).status_code == 200
    assert not restarted.get("/api/session").json()["capabilities"]["td_login_saved"]
    assert "personal-secret" not in (tmp_path / ".env").read_text()
    assert (
        restarted.post(
            "/api/login", json={"mode": "live", "use_saved": True}
        ).status_code
        == 422
    )


def test_login_is_not_saved_without_opt_in_or_after_failure(tmp_path):
    c = client(tmp_path)
    assert (
        c.post(
            "/api/login", json={"mode": "live", "token": "one-time-token"}
        ).status_code
        == 200
    )
    assert not (tmp_path / ".env").exists()

    class Rejected(DemoAdapter):
        def login(self):
            raise ValueError("bad login")

    c = TestClient(
        create_app(data_dir=tmp_path / "data", adapter_factory=lambda _: Rejected()),
        base_url="http://127.0.0.1:8765",
    )
    c.headers["X-CSRF-Token"] = c.get("/api/session").json()["csrf_token"]
    assert (
        c.post(
            "/api/login",
            json={"mode": "live", "token": "invalid-token", "remember": True},
        ).status_code
        == 401
    )
    assert not (tmp_path / ".env").exists()


def test_saved_token_replaces_password_and_preserves_other_env_settings(tmp_path):
    from dotenv import dotenv_values

    (tmp_path / ".env").write_text(
        "TDX_USERNAME=analytics\nOPENAI_API_KEY=ai-test-key\n"
    )
    c = client(tmp_path)
    c.post(
        "/api/login",
        json={
            "mode": "live",
            "username": "personal",
            "password": "secret",
            "remember": True,
        },
    )
    result = c.post(
        "/api/login", json={"mode": "live", "token": "saved-token", "remember": True}
    )
    assert result.status_code == 200
    values = dotenv_values(tmp_path / ".env")
    assert values["WORKBENCH_PERSONAL_TOKEN"] == "saved-token"
    assert values["WORKBENCH_PERSONAL_PASSWORD"] == ""
    assert values["TDX_USERNAME"] == "analytics"
    c.post("/api/forget-login", json={})
    values = dotenv_values(tmp_path / ".env")
    assert values["OPENAI_API_KEY"] == "ai-test-key"
    assert values["TDX_USERNAME"] == "analytics"
    assert "WORKBENCH_PERSONAL_TOKEN" not in values
