import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from dynamix_manager.hosted_vault import CredentialVault
from dynamix_manager.ticket_writes.models import parse_action


UID = "11111111-1111-4111-8111-111111111111"
BASE = "https://tenant.example/TDWebApi"


@pytest.mark.parametrize("payload", [
    dict(kind="comment", ticket_id=1001, comments="Hello"),
    dict(kind="status", ticket_id=1001, comments="Ordered", status_id=5),
    dict(kind="assign", ticket_id=1001, responsible_group_id=4),
    dict(kind="edit", ticket_id=1001, title="After", priority_id=7),
])
def test_direct_all_actions_and_replay_before_metadata_reads(setup, payload):
    action = parse_action(payload)
    result = setup.service.submit("principal", action, "request-1")
    assert result.outcome == "applied"
    assert result.ticket_id == 1001
    assert not hasattr(result, "review_url")
    connections = len(setup.connections)
    setup.upstream.records.clear()
    assert setup.service.submit("principal", action, "request-1") == result
    assert len(setup.connections) == connections
    assert setup.upstream.apply_count == 1


@pytest.mark.parametrize("failure", ["missing", "read", "revoked", "expired", "disabled", "identity", "metadata"])
def test_direct_rejects_invalid_authority_and_metadata(setup, failure):
    principal = "principal"
    if failure == "missing":
        principal = None
    elif failure == "read":
        setup.provider.binding["scopes"] = ["tdx.read"]
    elif failure == "revoked":
        setup.provider.revoked = True
    elif failure == "expired":
        setup.provider.binding["grant_expiry"] = 999.0
    elif failure == "disabled":
        setup.runtime.enabled = False
    elif failure == "identity":
        setup.identity[0] = "other-person"
    else:
        setup.upstream.records.clear()
    with pytest.raises(Exception) as error:
        setup.service.submit(principal, parse_action(dict(kind="comment", ticket_id=1001, comments="Hello")), "request-1")
    assert not isinstance(error.value, AttributeError)
    assert setup.upstream.apply_count == 0


@pytest.mark.parametrize("change", ["baseline", "metadata", "revoked", "disabled", "expired", "grant"])
def test_direct_preflight_rechecks_baseline_and_current_authority(setup, change):
    factory = setup.service.adapter_factory
    count = [0]

    def changing_factory(connection):
        adapter = factory(connection)
        validate = adapter.validate

        def changing_validate(action):
            count[0] += 1
            if count[0] == 2 and change == "baseline":
                setup.upstream.records["/api/42/tickets/1001"]["ModifiedDate"] = "version-2"
            if count[0] == 2 and change == "metadata":
                setup.upstream.records["/api/42/tickets/priorities"][0]["Name"] = "Renamed"
            result = validate(action)
            if count[0] == 2:
                if change == "revoked":
                    setup.provider.revoked = True
                elif change == "disabled":
                    setup.runtime.enabled = False
                elif change == "expired":
                    setup.clock[0] += 301
                elif change == "grant":
                    setup.provider.binding["family"] = "replacement"
            return result

        adapter.validate = changing_validate
        return adapter

    setup.service.adapter_factory = changing_factory
    result = setup.service.submit("principal", parse_action(dict(kind="edit", ticket_id=1001, priority_id=7)), "request-1")
    assert result.outcome == ("conflict" if change in {"baseline", "metadata"} else "rejected")
    assert setup.upstream.apply_count == 0


def test_direct_unknown_restart_changed_arguments_and_retained_lock(setup):
    from dynamix_manager.ticket_writes.service import TicketWriteService
    from dynamix_manager.ticket_writes.store import WriteStore

    action = parse_action(dict(kind="comment", ticket_id=1001, comments="Hello"))
    setup.upstream.apply_error = TimeoutError("secret upstream body")
    result = setup.service.submit("principal", action, "request-1")
    assert result.outcome == "unknown" and "secret" not in repr(result)
    restarted = TicketWriteService(setup.settings, setup.vault, setup.provider, setup.runtime,
        store=WriteStore(setup.vault, clock=lambda: setup.clock[0]),
        connection_factory=setup.service.connection_factory, adapter_factory=setup.service.adapter_factory)
    assert restarted.submit("principal", action, "request-1") == result
    from dynamix_manager.ticket_writes.store import EquivalentWriteBlocked
    with pytest.raises(EquivalentWriteBlocked, match="unresolved"):
        restarted.submit("principal", action, "different-request")
    changed = parse_action(dict(kind="comment", ticket_id=1001, comments="Different"))
    with pytest.raises(Exception, match="different arguments"):
        restarted.submit("principal", changed, "request-1")
    assert restarted.submit("principal", changed, "request-2").outcome == "conflict"
    assert setup.upstream.apply_count == 1


def test_direct_concurrent_requests_dispatch_once(setup):
    action = parse_action(dict(kind="comment", ticket_id=1001, comments="Hello"))
    setup.upstream.gate = threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(setup.service.submit, "principal", action, "request-1")
        deadline = time.monotonic() + 5
        while not setup.upstream.apply_count and time.monotonic() < deadline and not first.done():
            time.sleep(0.001)
        try:
            second = pool.submit(setup.service.submit, "principal", action, "request-1").result(timeout=5)
        finally:
            setup.upstream.gate.set()
        assert first.result(timeout=5).outcome == "applied"
    assert second.outcome == "unknown"
    assert setup.upstream.apply_count == 1


def test_direct_expired_result_tombstone_does_not_dispatch(setup):
    action = parse_action(dict(kind="comment", ticket_id=1001, comments="Hello"))
    first = setup.service.submit("principal", action, "request-1")
    setup.clock[0] += setup.store.RESULT_RETENTION + 1
    setup.upstream.records.clear()
    replay = setup.service.submit("principal", action, "request-1")
    assert replay.operation_id == first.operation_id
    assert replay.outcome == "expired"
    assert replay.ticket_url == first.ticket_url
    assert setup.upstream.apply_count == 1


@pytest.mark.parametrize("change", ["revoked", "disabled", "expired"])
def test_direct_replay_requires_current_authority(setup, change):
    action = parse_action(dict(kind="comment", ticket_id=1001, comments="Hello"))
    setup.service.submit("principal", action, "request-1")
    if change == "revoked":
        setup.provider.revoked = True
    elif change == "disabled":
        setup.runtime.enabled = False
    else:
        setup.clock[0] = setup.provider.binding["grant_expiry"] + 1
    with pytest.raises(Exception):
        setup.service.submit("principal", action, "request-1")
    assert setup.upstream.apply_count == 1


def test_direct_result_and_replay_survive_grant_renewal_but_not_other_owners(setup):
    action = parse_action(dict(kind="comment", ticket_id=1001, comments="Hello"))
    first = setup.service.submit("principal", action, "request-1")
    # A fresh OAuth authorization mints a new grant family for the same person.
    setup.provider.binding["family"] = "renewed-grant"
    setup.provider.binding["grant_expiry"] = 20_000.0
    assert setup.service.result("principal", first.operation_id).outcome == "applied"
    assert setup.service.submit("principal", action, "request-1") == first
    assert setup.upstream.apply_count == 1

    setup.provider.binding["client_id"] = "other-client"
    with pytest.raises(Exception, match="another grant"):
        setup.service.result("principal", first.operation_id)
    second = setup.service.submit("principal", action, "request-1")
    assert second.operation_id != first.operation_id
    assert setup.upstream.apply_count == 2


def test_pending_direct_record_is_dispatched_on_replay_and_reports_resubmission(setup):
    action = parse_action(dict(kind="comment", ticket_id=1001, comments="Hello"))
    owner = setup.provider.validate_write_grant(setup.provider.binding)
    prepared = setup.service.adapter_factory(FakeConnection({"WORKBENCH_PERSONAL_TOKEN": "x"})).validate(action)
    record = setup.store.prepare_direct(owner, prepared, "request-1")
    status = setup.service.result("principal", record.operation_id)
    assert status.outcome == "pending"
    assert "review" not in status.message.lower()
    assert "same request ID" in status.message
    replay = setup.service.submit("principal", action, "request-1")
    assert replay.operation_id == record.operation_id
    assert replay.outcome == "applied"
    assert setup.upstream.apply_count == 1


def test_applied_submit_reads_the_ticket_only_for_preflight(setup):
    action = parse_action(dict(kind="comment", ticket_id=1001, comments="Hello"))
    assert setup.service.submit("principal", action, "request-1").outcome == "applied"
    # One read validates the request, one rechecks the baseline under the claim.
    assert setup.upstream.snapshot_reads == 2


@pytest.mark.parametrize("status,fragment", [(429, "rate limit"), (403, "permission"), (400, "rejected")])
def test_rejected_messages_distinguish_throttling_and_permission(setup, status, fragment):
    setup.upstream.apply_result = SimpleNamespace(status_code=status)
    result = setup.service.submit(
        "principal", parse_action(dict(kind="comment", ticket_id=1001, comments="Hello")), "request-1")
    assert result.outcome == "rejected"
    assert fragment in result.message.lower()
    assert setup.upstream.apply_count == 1


def test_direct_racing_initial_validation_creates_one_operation(setup):
    factory = setup.service.adapter_factory
    barrier = threading.Barrier(2)
    count = [0]
    lock = threading.Lock()

    def racing_factory(connection):
        adapter = factory(connection)
        validate = adapter.validate

        def racing_validate(action):
            value = validate(action)
            with lock:
                count[0] += 1
                initial = count[0] <= 2
            if initial:
                barrier.wait(timeout=5)
            return value

        adapter.validate = racing_validate
        return adapter

    setup.service.adapter_factory = racing_factory
    action = parse_action(dict(kind="comment", ticket_id=1001, comments="Hello"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(setup.service.submit, "principal", action, "request-1") for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert results[0].operation_id == results[1].operation_id
    assert "applied" in {result.outcome for result in results}
    assert setup.upstream.apply_count == 1


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
            "/api/42/tickets/types": [dict(ID=3, Name="Hardware", IsActive=True, CategoryName="Support")],
            "/api/accounts/11": dict(ID=11, Name="Information Technology", IsActive=True),
            "/api/42/tickets/1001/tasks/77": dict(
                ID=77, TicketID=1001, Title="Approve payment", IsActive=True,
                PercentComplete=0, CompletedDate=None, ModifiedDate="task-v1", TypeID=1,
            ),
        }
        self.apply_count = 0
        self.apply_result = SimpleNamespace(status_code=200, json=lambda: dict(ID=1001, AppID=42))
        self.apply_error = None
        self.gate = None
        self.snapshot_reads = 0

    def read(self, path):
        if path == "/api/42/tickets/1001":
            self.snapshot_reads += 1
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


def test_factory_uses_personal_token_values_only(setup):
    from dynamix_manager.ticket_writes.service import create_ticket_write_service

    seen = []
    service = create_ticket_write_service(
        setup.settings, setup.vault, setup.provider, setup.runtime,
        store=setup.store,
        connection_factory=lambda values: (seen.append(values) or FakeConnection(values)),
        adapter_factory=setup.service.adapter_factory,
    )
    service.submit("principal", parse_action(dict(kind="comment", ticket_id=1001, comments="x")), "request-1")
    assert seen == 2 * [{
        "TDX_BASE_URL": BASE,
        "TDX_APP_ID": "8",
        "WORKBENCH_PERSONAL_TOKEN": "personal-secret",
    }]


def test_submit_rejects_untyped_creation_unknown_notifications_and_ineligible_metadata(setup):
    from dynamix_manager.ticket_writes.service import TicketPreparationRejected

    with pytest.raises((TypeError, ValueError)):
        setup.service.submit("principal", {"kind": "create", "ticket_id": 1001}, "request-1")
    with pytest.raises(TicketPreparationRejected, match="safely prepared"):
        setup.service.submit("principal", parse_action(dict(
            kind="assign", ticket_id=1001, responsible_group_id=4, notify_new_responsible=True,
        )), "request-2")
    setup.upstream.records["/api/groups/4/applications"] = []
    with pytest.raises(TicketPreparationRejected, match="safely prepared"):
        setup.service.submit("principal", parse_action(dict(
            kind="assign", ticket_id=1001, responsible_group_id=4,
        )), "request-3")
    assert setup.upstream.apply_count == 0
    with setup.vault._db() as db:
        assert db.execute("SELECT COUNT(*) FROM ticket_write_operations").fetchone()[0] == 0


def test_submit_closes_connection_after_identity_failure(setup):
    setup.identity[0] = RuntimeError("secret identity response")
    with pytest.raises(RuntimeError, match="linked account") as error:
        setup.service.submit("principal", parse_action(dict(kind="comment", ticket_id=1001, comments="x")), "r")
    assert "secret identity" not in str(error.value)
    assert setup.connections[-1].client.session.closed is True


@pytest.mark.parametrize("failure_point", ["connection", "adapter", "validate", "malformed_metadata"])
def test_submit_sanitizes_arbitrary_dependency_failures(setup, caplog, failure_point):
    from dynamix_manager.ticket_writes.service import TicketPreparationRejected

    sentinel = "private upstream body must not escape"
    original_adapter = setup.service.adapter_factory
    if failure_point == "connection":
        setup.service.connection_factory = lambda values: (_ for _ in ()).throw(RuntimeError(sentinel))
    elif failure_point == "adapter":
        setup.service.adapter_factory = lambda connection: (_ for _ in ()).throw(RuntimeError(sentinel))
    elif failure_point == "validate":
        def failing_adapter(connection):
            adapter = original_adapter(connection)
            adapter.validate = lambda action: (_ for _ in ()).throw(RuntimeError(sentinel))
            return adapter
        setup.service.adapter_factory = failing_adapter
    else:
        setup.upstream.records["/api/42/tickets/statuses"][0]["Name"] = {"secret": sentinel}

    action = (dict(kind="status", ticket_id=1001, comments="x", status_id=5)
              if failure_point == "malformed_metadata" else dict(kind="comment", ticket_id=1001, comments="x"))
    with pytest.raises(TicketPreparationRejected) as error:
        setup.service.submit("principal", parse_action(action), "request-1")
    assert str(error.value) == "The ticket change could not be safely prepared."
    assert sentinel not in caplog.text
    with setup.vault._db() as db:
        assert db.execute("SELECT COUNT(*) FROM ticket_write_operations").fetchone()[0] == 0


def test_submit_never_persists_or_returns_adapter_error_text(setup):
    from dynamix_manager.ticket_writes.models import WriteResult

    original_factory = setup.service.adapter_factory

    def unsafe_factory(connection):
        adapter = original_factory(connection)
        adapter.apply_once = lambda prepared: WriteResult(
            outcome="rejected", message="raw secret permission body", status_code=400)
        return adapter

    setup.service.adapter_factory = unsafe_factory
    result = setup.service.submit(
        "principal", parse_action(dict(kind="comment", ticket_id=1001, comments="Hello")), "request-1")
    assert result.outcome == "rejected"
    assert result.message == "TeamDynamix rejected the change."
    assert "secret" not in repr(result)
    assert b"raw secret" not in setup.vault.path.read_bytes()


def test_result_ignores_the_write_gate_but_requires_current_grant_and_never_connects(setup):
    result = setup.service.submit(
        "principal", parse_action(dict(kind="comment", ticket_id=1001, comments="private payload")), "request-1")
    connection_count = len(setup.connections)
    setup.runtime.enabled = False
    status = setup.service.result("principal", result.operation_id)
    assert status.outcome == "applied" and status.ticket_id == 1001
    assert "private payload" not in repr(status)
    assert len(setup.connections) == connection_count
    setup.runtime.enabled = True
    setup.provider.revoked = True
    with pytest.raises(RuntimeError, match="authorization"):
        setup.service.result("principal", result.operation_id)


def test_service_uses_injected_credential_provider_for_personal_token(setup):
    from dynamix_manager.ticket_writes.service import TicketWriteService

    served = []

    def credential_provider(subject):
        served.append(subject)
        return {"uid": UID, "token": "renewed-secret"}

    service = TicketWriteService(
        setup.settings, setup.vault, setup.provider, setup.runtime, store=setup.store,
        connection_factory=setup.service.connection_factory, adapter_factory=setup.service.adapter_factory,
        credential_provider=credential_provider,
    )
    result = service.submit("principal", parse_action(dict(kind="comment", ticket_id=1001, comments="x")), "r1")
    assert result.outcome == "applied"
    assert served == [UID, UID]
    assert {c.token for c in setup.connections} == {"renewed-secret"}


def test_task_completion_submits_once_and_conflicts_when_the_task_changes(setup):
    action = parse_action(dict(kind="task", ticket_id=1001, task_id=77, comments="Approved"))
    result = setup.service.submit("principal", action, "task-1")
    assert result.outcome == "applied" and result.ticket_id == 1001
    assert setup.service.submit("principal", action, "task-1") == result
    assert setup.upstream.apply_count == 1

    factory = setup.service.adapter_factory
    count = [0]

    def changing_factory(connection):
        adapter = factory(connection)
        validate = adapter.validate

        def changing_validate(value):
            count[0] += 1
            if count[0] == 2:
                setup.upstream.records["/api/42/tickets/1001/tasks/77"]["ModifiedDate"] = "task-v2"
            return validate(value)

        adapter.validate = changing_validate
        return adapter

    setup.service.adapter_factory = changing_factory
    second = setup.service.submit("principal", parse_action(dict(kind="task", ticket_id=1001, task_id=77)), "task-2")
    assert second.outcome == "conflict"
    assert setup.upstream.apply_count == 1


def test_create_ticket_reports_the_new_ticket_and_replays_without_a_second_creation(setup):
    setup.upstream.apply_result = SimpleNamespace(status_code=201, json=lambda: dict(
        ID=5555, AppID=42, Title="New printer", StatusName="New", PriorityName="Normal",
        ResponsibleFullName="Person", ResponsibleGroupName=None, FormName="Standard", TypeName="Hardware",
        RequestorName="Person", AccountName="Information Technology"))
    action = parse_action(dict(kind="create", title="New printer", type_id=3, account_id=11,
                               requestor_uid=UID, responsible_uid=UID))
    result = setup.service.submit("principal", action, "create-1")
    assert result.outcome == "applied" and result.status_code == 201
    assert result.ticket_id == 5555
    assert result.ticket_url == "https://tenant.example/TDNext/Apps/42/Tickets/TicketDet.aspx?TicketID=5555"
    assert result.detail["responsible"] == "Person" and result.detail["status"] == "New"
    assert setup.service.submit("principal", action, "create-1") == result
    assert setup.upstream.apply_count == 1
    assert setup.service.result("principal", result.operation_id).ticket_id == 5555


def test_create_ticket_rejection_has_no_ticket_and_points_at_the_app(setup):
    setup.upstream.apply_result = SimpleNamespace(status_code=400, json=lambda: {"Message": "private detail"})
    action = parse_action(dict(kind="create", title="New printer", type_id=3, account_id=11, requestor_uid=UID))
    result = setup.service.submit("principal", action, "create-1")
    assert result.outcome == "rejected" and result.ticket_id == 0 and result.detail is None
    assert result.ticket_url == "https://tenant.example/TDNext/Apps/42/Tickets/"
    assert "private detail" not in repr(result)


def test_another_subject_cannot_see_or_replay_a_users_operation(setup):
    action = parse_action(dict(kind="comment", ticket_id=1001, comments="Hello"))
    first = setup.service.submit("principal", action, "request-1")
    setup.provider.binding["subject"] = "22222222-2222-4222-8222-222222222222"
    setup.vault.put("https://issuer.example", setup.provider.binding["subject"], setup.provider.binding["subject"],
                    "other-secret", time.time() + 3600)
    with pytest.raises(Exception, match="another grant"):
        setup.service.result("principal", first.operation_id)
    setup.identity[0] = setup.provider.binding["subject"]
    second = setup.service.submit("principal", action, "request-1")
    assert second.operation_id != first.operation_id
    assert setup.upstream.apply_count == 2
    assert setup.connections[-1].values["WORKBENCH_PERSONAL_TOKEN"] == "other-secret"
