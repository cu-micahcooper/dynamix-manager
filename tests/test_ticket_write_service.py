import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from cryptography.fernet import Fernet

from dynamix_manager.hosted_vault import CredentialVault
from dynamix_manager.ticket_writes.models import parse_action


UID = "11111111-1111-4111-8111-111111111111"
BASE = "https://tenant.example/TDWebApi"


def capability(prepared):
    return urlsplit(prepared.review_url).fragment


class FakeProvider:
    def __init__(self):
        self.revoked = False
        self.binding = {
            "subject": UID,
            "client_id": "client-1",
            "resource": "https://connector.example/mcp",
            "family": "family-1",
            "scopes": ["tdx.read", "tdx.write"],
            "grant_expiry": 10_000.0,
        }

    def write_grant_binding(self, principal):
        if principal != "principal" or self.revoked:
            raise RuntimeError("Current write authorization is required.")
        return self.validate_write_grant(self.binding)

    def validate_write_grant(self, binding):
        if self.revoked or binding != self.binding:
            raise RuntimeError("Current write authorization is required.")
        return copy.deepcopy(self.binding)


class FakeRuntime:
    enabled = True

    def require_enabled(self):
        if not self.enabled:
            raise RuntimeError("Hosted ticket writes are disabled.")


class FakeSession:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, values, identity=UID):
        self.values = values
        self.base_url = BASE
        self.app_id = 42
        self.header_app_id = "8"
        self.token = values["WORKBENCH_PERSONAL_TOKEN"]
        self.auth_mode = "user"
        self._identity = identity
        self.client = SimpleNamespace(session=FakeSession())

    def ready(self):
        return self

    def identity(self):
        if isinstance(self._identity, Exception):
            raise self._identity
        return self._identity


class Upstream:
    def __init__(self):
        self.records = {
            "/api/42/tickets/1001": dict(
                ID=1001, AppID=42, Title="Before", Description="old",
                PriorityID=2, PriorityName="Normal", StatusID=1,
                StatusName="Open", ResponsibleUid=None,
                ResponsibleFullName=None, ResponsibleGroupID=9,
                ResponsibleGroupName="Support", ModifiedDate="version-1",
            ),
            "/api/42/tickets/statuses": [dict(
                ID=5, Name="Ordered", IsActive=True, StatusClass=5,
                RequireGoesOffHold=False,
            )],
            "/api/42/tickets/priorities": [dict(ID=7, Name="High", IsActive=True)],
            f"/api/people/{UID}": dict(
                UID=UID, IsActive=True, FullName="Person",
                OrgApplications=[dict(ID=42, IsActive=True)],
            ),
            "/api/groups/4": dict(ID=4, Name="Team", IsActive=True),
            "/api/groups/4/applications": [dict(AppID=42, GroupID=4)],
        }
        self.apply_count = 0
        self.apply_result = SimpleNamespace(status_code=200, json=lambda: dict(ID=1001, AppID=42))
        self.apply_error = None
        self.gate = None
        self.readback_error = None
        self.snapshot_reads = 0

    def read(self, path):
        if path == "/api/42/tickets/1001":
            self.snapshot_reads += 1
            if self.readback_error and self.apply_count:
                raise self.readback_error
        value = self.records[path]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    def request(self, method, url, **kwargs):
        self.apply_count += 1
        if self.gate:
            self.gate.wait(5)
        if self.apply_error:
            raise self.apply_error
        return self.apply_result


@pytest.fixture
def setup(tmp_path):
    from dynamix_manager.ticket_writes.adapter import WriteAdapter
    from dynamix_manager.ticket_writes.service import TicketWriteService
    from dynamix_manager.ticket_writes.store import WriteStore

    clock = [1_000.0]
    vault = CredentialVault(tmp_path / "private.sqlite", Fernet.generate_key())
    vault.put("https://issuer.example", UID, UID, "personal-secret", time.time() + 3600)
    provider, runtime, upstream = FakeProvider(), FakeRuntime(), Upstream()
    connections = []
    identity = [UID]

    def connection_factory(values):
        connection = FakeConnection(values, identity[0])
        connections.append(connection)
        return connection

    def adapter_factory(connection):
        return WriteAdapter(
            connection.base_url, connection.app_id, connection.token,
            header_app_id=connection.header_app_id,
            read=upstream.read, request=upstream.request,
        )

    store = WriteStore(vault, clock=lambda: clock[0])
    settings = SimpleNamespace(
        public_url="https://connector.example",
        issuer="https://issuer.example",
        tdx_url=BASE,
        tdx_client_id="8",
    )
    service = TicketWriteService(
        settings, vault, provider, runtime, store=store,
        connection_factory=connection_factory, adapter_factory=adapter_factory,
    )
    return SimpleNamespace(
        service=service, store=store, provider=provider, runtime=runtime,
        upstream=upstream, connections=connections, identity=identity,
        clock=clock, settings=settings, vault=vault,
    )


@pytest.mark.parametrize("action", [
    dict(kind="comment", ticket_id=1001, comments="Hello", is_private=False,
         notify=["person@example.invalid"]),
    dict(kind="status", ticket_id=1001, comments="Ordered", status_id=5),
    dict(kind="assign", ticket_id=1001, responsible_group_id=4),
    dict(kind="edit", ticket_id=1001, title="After", priority_id=7),
])
def test_prepare_all_supported_actions_without_upstream_write(setup, action):
    result = setup.service.prepare("principal", parse_action(action))
    assert result.operation_id
    assert result.preview.ticket_id == 1001
    assert result.review_url.startswith("https://connector.example/writes/review#")
    assert "?" not in result.review_url
    assert setup.upstream.apply_count == 0
    record = setup.store.get(capability(result))
    assert record.prepared.preview == result.preview
    assert setup.connections[-1].client.session.closed is True
    assert capability(result) not in repr(result)
    assert "Hello" not in repr(result)


def test_prepare_rejects_untyped_creation_unknown_notifications_and_ineligible_metadata(setup):
    from dynamix_manager.ticket_writes.service import TicketPreparationRejected

    with pytest.raises((TypeError, ValueError)):
        setup.service.prepare("principal", {"kind": "create", "ticket_id": 1001})
    with pytest.raises(TicketPreparationRejected, match="safely prepared"):
        setup.service.prepare("principal", parse_action(dict(
            kind="assign", ticket_id=1001, responsible_group_id=4,
            notify_new_responsible=True,
        )))
    setup.upstream.records["/api/groups/4/applications"] = []
    with pytest.raises(TicketPreparationRejected, match="safely prepared"):
        setup.service.prepare("principal", parse_action(dict(
            kind="assign", ticket_id=1001, responsible_group_id=4,
        )))
    assert setup.upstream.apply_count == 0


def test_prepare_validates_flag_scope_identity_and_closes_identity_failure(setup):
    setup.runtime.enabled = False
    with pytest.raises(RuntimeError, match="disabled"):
        setup.service.prepare("principal", parse_action(dict(kind="comment", ticket_id=1001, comments="x")))
    setup.runtime.enabled = True
    setup.provider.revoked = True
    with pytest.raises(RuntimeError, match="authorization"):
        setup.service.prepare("principal", parse_action(dict(kind="comment", ticket_id=1001, comments="x")))
    setup.provider.revoked = False
    setup.identity[0] = RuntimeError("secret identity response")
    with pytest.raises(RuntimeError, match="linked account") as error:
        setup.service.prepare("principal", parse_action(dict(kind="comment", ticket_id=1001, comments="x")))
    assert "secret identity" not in str(error.value)
    assert setup.connections[-1].client.session.closed is True


@pytest.mark.parametrize(
    "failure_point", ["connection", "adapter", "validate", "malformed_metadata"]
)
def test_prepare_sanitizes_arbitrary_dependency_failures(setup, caplog, failure_point):
    from dynamix_manager.ticket_writes.service import TicketPreparationRejected

    sentinel = "private upstream body must not escape"
    original_connection = setup.service.connection_factory
    original_adapter = setup.service.adapter_factory
    if failure_point == "connection":
        setup.service.connection_factory = lambda values: (_ for _ in ()).throw(
            RuntimeError(sentinel)
        )
    elif failure_point == "adapter":
        setup.service.adapter_factory = lambda connection: (_ for _ in ()).throw(
            RuntimeError(sentinel)
        )
    else:
        if failure_point == "validate":
            def failing_adapter(connection):
                adapter = original_adapter(connection)
                adapter.validate = lambda action: (_ for _ in ()).throw(RuntimeError(sentinel))
                return adapter

            setup.service.adapter_factory = failing_adapter
        else:
            setup.upstream.records["/api/42/tickets/statuses"][0]["Name"] = {
                "secret": sentinel
            }

    with pytest.raises(TicketPreparationRejected) as error:
        action = (
            dict(kind="status", ticket_id=1001, comments="x", status_id=5)
            if failure_point == "malformed_metadata"
            else dict(kind="comment", ticket_id=1001, comments="x")
        )
        setup.service.prepare(
            "principal", parse_action(action)
        )
    assert str(error.value) == "The ticket change could not be safely prepared."
    assert sentinel not in str(error.value)
    assert sentinel not in caplog.text
    with setup.vault._db() as db:
        assert db.execute("SELECT COUNT(*) FROM ticket_write_operations").fetchone()[0] == 0
    setup.service.connection_factory = original_connection


@pytest.mark.parametrize("change", ["revoked", "disabled"])
def test_prepare_rechecks_current_authority_after_live_validation_before_storage(setup, change):
    from dynamix_manager.ticket_writes.service import (
        WriteAuthorizationRequired,
        WritesDisabled,
    )

    original_factory = setup.service.adapter_factory

    def changing_factory(connection):
        adapter = original_factory(connection)
        original_validate = adapter.validate

        def validate(action):
            prepared = original_validate(action)
            if change == "revoked":
                setup.provider.revoked = True
            else:
                setup.runtime.enabled = False
            return prepared

        adapter.validate = validate
        return adapter

    setup.service.adapter_factory = changing_factory
    error = WriteAuthorizationRequired if change == "revoked" else WritesDisabled
    with pytest.raises(error):
        setup.service.prepare(
            "principal", parse_action(dict(kind="comment", ticket_id=1001, comments="x"))
        )
    with setup.vault._db() as db:
        assert db.execute("SELECT COUNT(*) FROM ticket_write_operations").fetchone()[0] == 0


def prepared_and_open(setup, action=None, *, browser="browser", csrf="csrf"):
    action = action or parse_action(dict(kind="edit", ticket_id=1001, title="After"))
    prepared = setup.service.prepare("principal", action)
    record = setup.service.open_review(capability(prepared), browser, csrf)
    assert record.prepared.preview == prepared.preview
    return prepared


def test_open_review_rechecks_flag_grant_and_binds_browser_without_write(setup):
    prepared = setup.service.prepare(
        "principal", parse_action(dict(kind="comment", ticket_id=1001, comments="Hello")))
    setup.runtime.enabled = False
    with pytest.raises(RuntimeError, match="disabled"):
        setup.service.open_review(capability(prepared), "browser", "csrf")
    setup.runtime.enabled = True
    setup.provider.revoked = True
    with pytest.raises(RuntimeError, match="authorization"):
        setup.service.open_review(capability(prepared), "browser", "csrf")
    setup.provider.revoked = False
    first = setup.service.open_review(capability(prepared), "browser", "csrf")
    assert setup.service.open_review(capability(prepared), "browser", "csrf") == first
    with pytest.raises(Exception):
        setup.service.open_review(capability(prepared), "other", "csrf")
    assert setup.upstream.apply_count == 0


def test_commit_conflicts_on_baseline_or_metadata_drift_and_sends_nothing(setup):
    first = prepared_and_open(setup)
    setup.upstream.records["/api/42/tickets/1001"]["ModifiedDate"] = "version-2"
    result = setup.service.commit(capability(first), "browser", "csrf")
    assert result.outcome == "conflict" and setup.upstream.apply_count == 0

    setup.upstream.records["/api/42/tickets/1001"]["ModifiedDate"] = "version-1"
    second = prepared_and_open(
        setup, parse_action(dict(kind="edit", ticket_id=1001, priority_id=7)),
        browser="b2", csrf="c2",
    )
    setup.upstream.records["/api/42/tickets/priorities"][0]["Name"] = "Renamed"
    result = setup.service.commit(capability(second), "b2", "c2")
    assert result.outcome == "conflict" and setup.upstream.apply_count == 0


@pytest.mark.parametrize("change", ["revoked", "disabled", "expired"])
def test_commit_rechecks_authority_immediately_before_apply(setup, change):
    prepared = prepared_and_open(setup)
    original_validate = setup.service.adapter_factory

    def changing_factory(connection):
        adapter = original_validate(connection)
        original = adapter.validate

        def validate(action):
            value = original(action)
            if change == "revoked":
                setup.provider.revoked = True
            elif change == "disabled":
                setup.runtime.enabled = False
            else:
                setup.clock[0] = 1_301.0
            return value

        adapter.validate = validate
        return adapter

    setup.service.adapter_factory = changing_factory
    result = setup.service.commit(capability(prepared), "browser", "csrf")
    assert result.outcome == "rejected"
    assert setup.upstream.apply_count == 0


@pytest.mark.parametrize("status,outcome", [(403, "rejected"), (500, "unknown")])
def test_commit_records_safe_single_attempt_outcomes(setup, status, outcome):
    prepared = prepared_and_open(setup)
    setup.upstream.apply_result = SimpleNamespace(status_code=status)
    result = setup.service.commit(capability(prepared), "browser", "csrf")
    assert result.outcome == outcome
    assert result.ticket_id == 1001
    assert result.ticket_url == "https://tenant.example/TDNext/Apps/42/Tickets/TicketDet.aspx?TicketID=1001"
    assert setup.upstream.apply_count == 1
    duplicate = setup.service.commit(capability(prepared), "browser", "csrf")
    assert duplicate.outcome == outcome and setup.upstream.apply_count == 1


def test_commit_never_persists_or_returns_adapter_error_text(setup):
    from dynamix_manager.ticket_writes.models import WriteResult

    prepared = prepared_and_open(setup)
    original_factory = setup.service.adapter_factory

    def unsafe_factory(connection):
        adapter = original_factory(connection)
        adapter.apply_once = lambda prepared: WriteResult(
            outcome="rejected", message="raw secret permission body", status_code=403
        )
        return adapter

    setup.service.adapter_factory = unsafe_factory
    result = setup.service.commit(capability(prepared), "browser", "csrf")
    assert result.outcome == "rejected"
    assert result.message == "TeamDynamix rejected the change."
    assert "secret" not in repr(result)


def test_success_is_durable_before_optional_readback_failure(setup):
    prepared = prepared_and_open(setup)
    setup.upstream.readback_error = RuntimeError("raw upstream secret")
    result = setup.service.commit(capability(prepared), "browser", "csrf")
    assert result.outcome == "applied"
    assert result.message == "TeamDynamix accepted the change."
    assert "secret" not in repr(result)
    assert setup.store.get(capability(prepared), "browser").state == "applied"


def test_timeout_is_unknown_and_restart_does_not_retry(setup):
    from dynamix_manager.ticket_writes.service import TicketWriteService
    from dynamix_manager.ticket_writes.store import WriteStore

    prepared = prepared_and_open(setup)
    setup.upstream.apply_error = TimeoutError("raw request secret")
    result = setup.service.commit(capability(prepared), "browser", "csrf")
    assert result.outcome == "unknown" and "secret" not in result.message
    restarted = TicketWriteService(
        setup.settings, setup.vault, setup.provider, setup.runtime,
        store=WriteStore(setup.vault, clock=lambda: setup.clock[0]),
        connection_factory=setup.service.connection_factory,
        adapter_factory=setup.service.adapter_factory,
    )
    assert restarted.commit(capability(prepared), "browser", "csrf").outcome == "unknown"
    assert setup.upstream.apply_count == 1


def test_simultaneous_duplicate_save_dispatches_once(setup):
    prepared = prepared_and_open(setup)
    setup.upstream.gate = threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(setup.service.commit, capability(prepared), "browser", "csrf")
        while setup.upstream.apply_count == 0:
            time.sleep(0.001)
        second = pool.submit(setup.service.commit, capability(prepared), "browser", "csrf")
        second_result = second.result(timeout=5)
        setup.upstream.gate.set()
        first_result = first.result(timeout=5)
    assert setup.upstream.apply_count == 1
    assert {first_result.outcome, second_result.outcome} == {"applied", "unknown"}


def test_distinct_same_ticket_claim_is_locked_before_second_preflight(setup):
    first = prepared_and_open(
        setup, parse_action(dict(kind="edit", ticket_id=1001, title="One")),
        browser="b1", csrf="c1",
    )
    second = prepared_and_open(
        setup, parse_action(dict(kind="edit", ticket_id=1001, description="Two")),
        browser="b2", csrf="c2",
    )
    setup.upstream.gate = threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        in_flight = pool.submit(setup.service.commit, capability(first), "b1", "c1")
        while setup.upstream.apply_count == 0:
            time.sleep(0.001)
        reads_before_blocked_claim = setup.upstream.snapshot_reads
        blocked = setup.service.commit(capability(second), "b2", "c2")
        assert blocked.outcome == "conflict"
        assert setup.store.get(capability(second), "b2").state == "pending"
        assert setup.upstream.snapshot_reads == reads_before_blocked_claim
        setup.upstream.gate.set()
        assert in_flight.result(timeout=5).outcome == "applied"
    assert setup.upstream.apply_count == 1


def test_owner_result_requires_current_exact_grant_and_never_opens_connection(setup):
    prepared = setup.service.prepare(
        "principal", parse_action(dict(kind="comment", ticket_id=1001, comments="private payload")))
    connection_count = len(setup.connections)
    result = setup.service.result("principal", prepared.operation_id)
    assert result.outcome == "pending" and result.ticket_id == 1001
    assert len(setup.connections) == connection_count
    assert "private payload" not in repr(result)

    setup.runtime.enabled = False
    assert setup.service.result("principal", prepared.operation_id).outcome == "pending"
    setup.runtime.enabled = True

    setup.provider.binding["client_id"] = "other-client"
    with pytest.raises(Exception):
        setup.service.result("principal", prepared.operation_id)
    setup.provider.binding["client_id"] = "client-1"
    setup.provider.revoked = True
    with pytest.raises(RuntimeError, match="authorization"):
        setup.service.result("principal", prepared.operation_id)


def test_factory_uses_personal_token_values_only(setup):
    from dynamix_manager.ticket_writes.service import create_ticket_write_service

    seen = []
    service = create_ticket_write_service(
        setup.settings, setup.vault, setup.provider, setup.runtime,
        store=setup.store,
        connection_factory=lambda values: (seen.append(values) or FakeConnection(values)),
        adapter_factory=setup.service.adapter_factory,
    )
    service.prepare("principal", parse_action(dict(kind="comment", ticket_id=1001, comments="x")))
    assert seen == [{
        "TDX_BASE_URL": BASE,
        "TDX_APP_ID": "8",
        "WORKBENCH_PERSONAL_TOKEN": "personal-secret",
    }]
