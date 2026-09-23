import json
import multiprocessing
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.fernet import Fernet

from dynamix_manager.hosted_vault import CredentialVault
from dynamix_manager.ticket_writes.models import ChangePreview, PreparedChange, PreviewField, parse_action


def prepared(*, ticket_id=1001, comments="secret ticket text", baseline="version-1"):
    action = parse_action(dict(kind="comment", ticket_id=ticket_id, comments=comments))
    return PreparedChange(
        action=action,
        base_url="https://tenant.example/TDWebApi",
        app_id=42,
        baseline_json=json.dumps({"ModifiedDate": baseline}),
        payload_json=json.dumps({"Comments": comments}, sort_keys=True, separators=(",", ":")),
        preview=ChangePreview(
            application="InfoTech Tickets",
            ticket_id=ticket_id,
            ticket_title="Sensitive title",
            action="comment",
            fields=(PreviewField(name="Comment", before=None, after=comments),),
        ),
    )


def binding(**updates):
    value = dict(
        subject="user-1",
        client_id="client-1",
        resource="https://connector.example/mcp",
        family="family-1",
        scopes=("tdx.read", "tdx.write"),
        grant_expiry=10_000.0,
    )
    value.update(updates)
    return value


@pytest.fixture
def store_setup(tmp_path):
    from dynamix_manager.ticket_writes.store import WriteStore

    path = tmp_path / "private.sqlite"
    key = Fernet.generate_key()
    vault = CredentialVault(path, key)
    clock = [1_000.0]
    return WriteStore(vault, clock=lambda: clock[0]), path, key, clock


def test_direct_replay_is_durable_and_results_expire_without_resubmission(store_setup):
    from dynamix_manager.ticket_writes.store import WriteStore, DirectReplayResult, WriteBindingError
    store, path, key, clock = store_setup
    original = prepared()
    operation = store.prepare_direct(binding(), original, "private-request-id")
    claimed = store.claim_direct(operation.operation_id, binding())
    assert claimed.claimed
    store.finish(claimed, "applied", "Applied.")
    restarted = WriteStore(CredentialVault(path, key), clock=lambda: clock[0])
    assert restarted.lookup_direct(binding(), original.action, "private-request-id").state == "applied"
    with pytest.raises(WriteBindingError):
        restarted.lookup_direct(binding(), prepared(comments="different").action, "private-request-id")
    clock[0] += store.RESULT_RETENTION + 1
    replay = restarted.lookup_direct(binding(), original.action, "private-request-id")
    assert isinstance(replay, DirectReplayResult)
    assert replay.operation_id == operation.operation_id
    assert replay.result.outcome == "expired"
    assert restarted.prepare_direct(binding(), original, "private-request-id") == replay
    raw = path.read_bytes()
    for secret in (b"private-request-id", b"secret ticket text", b"user-1"):
        assert secret not in raw


def test_direct_concurrent_prepare_and_claim_fenced_once(store_setup):
    store, _, _, _ = store_setup
    def submit(_):
        record = store.prepare_direct(binding(), prepared(), "request-1")
        return store.claim_direct(record.operation_id, binding())
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(16)))
    assert len({r.record.operation_id for r in results}) == 1
    assert sum(r.claimed for r in results) == 1


def test_direct_owner_isolation_and_uncertain_locks(store_setup):
    from dynamix_manager.ticket_writes.store import WriteBindingError, EquivalentWriteBlocked
    store, _, _, _ = store_setup
    record = store.prepare_direct(binding(), prepared(), "request-1")
    with pytest.raises(WriteBindingError):
        store.claim_direct(record.operation_id, binding(subject="other"))
    assert store.lookup_direct(binding(subject="other"), prepared().action, "request-1") is None
    claimed = store.claim_direct(record.operation_id, binding())
    store.finish(claimed, "unknown", "Unknown.")
    assert not store.claim_direct(record.operation_id, binding()).claimed
    with pytest.raises(EquivalentWriteBlocked):
        store.prepare_direct(binding(), prepared(), "request-2")
    other = store.prepare_direct(binding(), prepared(comments="other"), "request-3")
    conflict = store.claim_direct(other.operation_id, binding())
    assert not conflict.claimed
    assert conflict.record.state == "conflict"


def test_lock_conflict_names_the_blocking_operation_only_to_its_owner(store_setup):
    store, _, _, _ = store_setup
    stuck, fence = submit(store, prepared(), "first")
    store.finish(fence, "unknown", "Unknown.")
    mine = store.prepare_direct(binding(), prepared(comments="different"), "second")
    blocked = store.claim_direct(mine.operation_id, binding())
    assert blocked.record.state == "conflict"
    assert blocked.record.result.detail == {"blocking_operation_id": stuck.operation_id}
    theirs = store.prepare_direct(binding(subject="user-2"), prepared(comments="other"), "third")
    blocked = store.claim_direct(theirs.operation_id, binding(subject="user-2"))
    assert blocked.record.state == "conflict" and blocked.record.result.detail is None


def test_direct_ticket_lock_conflict_remains_terminal_after_lock_clears(store_setup):
    store, _, _, _ = store_setup
    first = store.prepare_direct(binding(), prepared(), "first")
    claim = store.claim_direct(first.operation_id, binding())
    second_action = prepared(comments="different change")
    second = store.prepare_direct(binding(), second_action, "second")
    blocked = store.claim_direct(second.operation_id, binding())
    assert not blocked.claimed
    assert blocked.record.state == "conflict"
    store.finish(claim, "applied", "Applied.")
    replay = store.prepare_direct(binding(), second_action, "second")
    assert replay.operation_id == second.operation_id
    assert replay.state == "conflict"
    assert not store.claim_direct(second.operation_id, binding()).claimed


@pytest.mark.parametrize("request_id", [None, "", "   ", 42, "x" * 201])
def test_direct_requires_bounded_request_id(store_setup, request_id):
    store, _, _, _ = store_setup
    with pytest.raises(ValueError):
        store.prepare_direct(binding(), prepared(), request_id)


def test_direct_normalizes_defaults_but_preserves_explicit_null(store_setup):
    from dynamix_manager.ticket_writes.store import WriteBindingError
    store, _, _, _ = store_setup
    record = store.prepare_direct(binding(), prepared(), "normalized")
    explicit = parse_action(dict(kind="comment", ticket_id=1001, comments="secret ticket text",
                                 is_private=True, notify=[]))
    assert store.lookup_direct(binding(), explicit, "normalized") == record
    edit = parse_action(dict(kind="edit", ticket_id=1001, title="Title"))
    record = store.prepare_direct(binding(), prepared().model_copy(update={"action": edit}), "edit")
    with pytest.raises(WriteBindingError):
        store.lookup_direct(binding(), parse_action(dict(kind="edit", ticket_id=1001,
                                                        title="Title", description=None)), "edit")


def test_direct_capacity_and_thirty_day_window(store_setup, monkeypatch):
    from dynamix_manager.ticket_writes.store import WriteCapacityError
    store, _, _, clock = store_setup
    owner = binding(grant_expiry=10_000_000.0)
    monkeypatch.setattr(store, "MAX_DIRECT", 1)
    first = store.prepare_direct(owner, prepared(), "first")
    fence = store.claim_direct(first.operation_id, owner)
    store.finish(fence, "applied", "Applied.")
    clock[0] += store.RESULT_RETENTION + 1
    with pytest.raises(WriteCapacityError):
        store.prepare_direct(owner, prepared(), "second")
    assert store.lookup_direct(owner, prepared().action, "first") is not None
    clock[0] = first.created_at + store.DIRECT_RETENTION
    assert store.lookup_direct(owner, prepared().action, "first") is None
    assert store.prepare_direct(owner, prepared(), "first").operation_id != first.operation_id


def _process_direct(path, key, now, start, output):
    from dynamix_manager.ticket_writes.store import WriteStore
    store = WriteStore(CredentialVault(path, key), clock=lambda: now)
    start.wait(10)
    record = store.prepare_direct(binding(), prepared(), "cross-process")
    claim = store.claim_direct(record.operation_id, binding())
    output.put((record.operation_id, claim.claimed))


def test_direct_processes_prepare_and_dispatch_once(store_setup):
    _, path, key, clock = store_setup
    context = multiprocessing.get_context("spawn")
    start, output = context.Event(), context.Queue()
    processes = [context.Process(target=_process_direct,
                                args=(str(path), key, clock[0], start, output)) for _ in range(3)]
    for process in processes:
        process.start()
    start.set()
    results = [output.get(timeout=15) for _ in processes]
    for process in processes:
        process.join(15)
        assert process.exitcode == 0
    assert len({result[0] for result in results}) == 1
    assert sum(result[1] for result in results) == 1


def test_owner_lookups_survive_grant_renewal_but_not_a_different_owner(store_setup):
    from dynamix_manager.ticket_writes.store import WriteBindingError

    store, _, _, _ = store_setup
    record = store.prepare_direct(binding(), prepared(), "request-1")
    assert not hasattr(record, "capability")
    renewed = binding(family="family-2", grant_expiry=20_000.0)
    assert store.get_for_owner(record.operation_id, renewed).operation_id == record.operation_id
    replay = store.lookup_direct(renewed, prepared().action, "request-1")
    assert replay.operation_id == record.operation_id

    for field, replacement in (
        ("subject", "other-user"),
        ("client_id", "other-client"),
        ("resource", "https://other.example/mcp"),
    ):
        other = binding(**{field: replacement})
        with pytest.raises(WriteBindingError):
            store.get_for_owner(record.operation_id, other)
        assert store.lookup_direct(other, prepared().action, "request-1") is None


def test_claim_under_renewed_grant_rebinds_pending_operation(store_setup):
    from dynamix_manager.ticket_writes.store import WriteBindingError

    store, _, _, _ = store_setup
    record = store.prepare_direct(binding(), prepared(), "request-1")
    renewed = binding(family="family-2", grant_expiry=20_000.0)
    with pytest.raises(WriteBindingError):
        store.claim_direct(record.operation_id, binding(client_id="other-client"))
    claim = store.claim_direct(record.operation_id, renewed)
    assert claim.claimed
    assert claim.record.binding.family == "family-2"
    assert store.get_for_owner(record.operation_id, renewed).binding.family == "family-2"
    finished = store.finish(claim, "applied", "Applied.")
    assert finished.binding.family == "family-2"


def submit(store, change, request_id, owner=None):
    owner = owner or binding()
    record = store.prepare_direct(owner, change, request_id)
    return record, store.claim_direct(record.operation_id, owner)


def test_prepare_direct_encrypts_immutable_record_and_stores_no_secrets(store_setup):
    store, path, _, _ = store_setup
    original = prepared()
    record = store.prepare_direct(binding(), original, "request-1")
    assert record.operation_id and record.expires_at == 1_300.0
    assert record.prepared == original
    assert record.prepared.action.model_fields_set == original.action.model_fields_set
    assert record.binding.subject == "user-1"
    assert record.binding.scopes == frozenset({"tdx.read", "tdx.write"})
    assert record.state == "pending"
    raw = path.read_bytes()
    for secret in (b"secret ticket text", b"Sensitive title", b"family-1", b"request-1"):
        assert secret not in raw
    with sqlite3.connect(path) as db:
        stored = db.execute("SELECT capability_hash FROM ticket_write_operations WHERE id=?",
                            (record.operation_id,)).fetchone()[0]
    assert len(stored) == 64


def test_expired_pending_operation_is_terminal_on_claim_and_retained_briefly(store_setup):
    from dynamix_manager.ticket_writes.store import WriteNotFoundError
    store, _, _, clock = store_setup
    record = store.prepare_direct(binding(), prepared(), "request-1")
    clock[0] = record.expires_at
    expired = store.claim_direct(record.operation_id, binding())
    assert expired.claimed is False and expired.record.state == "expired"
    assert store.get_for_owner(record.operation_id, binding()).state == "expired"
    clock[0] = record.expires_at + store.RESULT_RETENTION
    with pytest.raises(WriteNotFoundError):
        store.get_for_owner(record.operation_id, binding())


def test_abandoned_full_pool_recovers_after_expiry_retention(store_setup, monkeypatch):
    from dynamix_manager.ticket_writes.store import WriteCapacityError
    store, _, _, clock = store_setup
    monkeypatch.setattr(store, "MAX_NORMAL", 1)
    abandoned = store.prepare_direct(binding(), prepared(ticket_id=1), "one")
    with pytest.raises(WriteCapacityError):
        store.prepare_direct(binding(), prepared(ticket_id=2), "two")
    clock[0] = abandoned.expires_at + store.RESULT_RETENTION
    replacement = store.prepare_direct(binding(grant_expiry=20_000.0), prepared(ticket_id=2), "two")
    assert replacement.operation_id != abandoned.operation_id


def test_claim_is_exactly_once_and_known_terminal_duplicate_keeps_result(store_setup):
    store, _, _, _ = store_setup
    record, first = submit(store, prepared(), "request-1")
    assert first.claimed is True and first.record.state == "sending"
    duplicate = store.claim_direct(record.operation_id, binding())
    assert duplicate.claimed is False and duplicate.record.state == "sending"
    applied = store.finish(first, "applied", "TeamDynamix accepted the change.", status_code=200)
    assert applied.state == "applied" and applied.result.outcome == "applied"
    duplicate = store.claim_direct(record.operation_id, binding())
    assert duplicate.claimed is False
    assert duplicate.record.state == "applied"
    assert duplicate.record.result == applied.result


def test_claim_fence_cannot_overwrite_unknown_or_be_forged(store_setup):
    from dynamix_manager.ticket_writes.store import ClaimResult, WriteBindingError
    store, _, _, _ = store_setup
    _, first = submit(store, prepared(), "request-1")
    forged = ClaimResult(True, first.record, "forged-claim-secret")
    with pytest.raises(WriteBindingError):
        store.finish(forged, "applied", "Forged.")
    unknown = store.finish(first, "unknown", "The outcome is unknown.")
    repeated = store.finish(first, "applied", "Late overwrite attempt.", status_code=200)
    assert unknown.state == repeated.state == "unknown"
    assert repeated.result.message == "The outcome is unknown."


def test_claim_secrets_and_payloads_are_redacted_from_representations(store_setup):
    store, _, _, _ = store_setup
    record, claimed = submit(store, prepared(), "request-1")
    assert "secret ticket text" not in repr(record)
    assert claimed.claim_token not in repr(claimed)
    assert "secret ticket text" not in repr(claimed)


def test_same_ticket_lock_precedes_preflight_and_known_outcome_releases_it(store_setup):
    store, _, _, _ = store_setup
    _, first_claim = submit(store, prepared(comments="first"), "first")
    _, blocked = submit(store, prepared(comments="second"), "second")
    assert blocked.claimed is False and blocked.record.state == "conflict"
    store.finish(first_claim, "conflict", "The ticket changed before dispatch.")
    _, third = submit(store, prepared(comments="third"), "third")
    assert third.claimed is True


def test_unknown_and_crash_left_sending_survive_expiry_cleanup_and_restart(store_setup):
    from dynamix_manager.ticket_writes.store import WriteStore
    store, path, key, clock = store_setup
    uncertain, _ = submit(store, prepared(comments="uncertain"), "uncertain")
    clock[0] = 100_000.0
    reopened = WriteStore(CredentialVault(path, key), clock=lambda: clock[0])
    owner = binding(grant_expiry=200_000.0)
    status = reopened.claim_direct(uncertain.operation_id, owner)
    assert status.claimed is False and status.record.state == "sending"
    assert status.record.effective_state == "unknown"
    _, other = submit(reopened, prepared(comments="other"), "other", owner)
    assert other.claimed is False and other.record.state == "conflict"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM ticket_write_markers").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM ticket_write_locks").fetchone()[0] == 1


def test_unknown_finish_retains_marker_and_blocks_equivalent_prepare(store_setup):
    from dynamix_manager.ticket_writes.store import EquivalentWriteBlocked
    store, _, _, _ = store_setup
    change = prepared()
    _, claimed = submit(store, change, "request-1")
    assert store.finish(claimed, "unknown", "Outcome cannot be determined.").state == "unknown"
    with pytest.raises(EquivalentWriteBlocked):
        store.prepare_direct(binding(), change, "request-2")


def test_two_pending_equivalent_operations_allow_only_one_claim(store_setup):
    from dynamix_manager.ticket_writes.store import WriteStore
    store, path, key, clock = store_setup
    first = store.prepare_direct(binding(), prepared(), "first")
    second = store.prepare_direct(binding(), prepared(), "second")

    def attempt(record):
        local = WriteStore(CredentialVault(path, key), clock=lambda: clock[0])
        result = local.claim_direct(record.operation_id, binding())
        return result.claimed, result.record.state

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [first, second]))
    assert sorted(results) == [(False, "conflict"), (True, "sending")]


def test_applied_claim_supersedes_equivalent_pending_operation(store_setup):
    store, _, _, _ = store_setup
    first = store.prepare_direct(binding(), prepared(), "first")
    second = store.prepare_direct(binding(), prepared(), "second")
    store.finish(store.claim_direct(first.operation_id, binding()), "applied", "Applied.", status_code=200)
    stale = store.claim_direct(second.operation_id, binding())
    assert stale.claimed is False and stale.record.state == "conflict"
    assert store.prepare_direct(binding(), prepared(), "third").operation_id != second.operation_id


def test_normal_and_unresolved_capacity_fail_closed_without_eviction(store_setup, monkeypatch):
    from dynamix_manager.ticket_writes.store import WriteCapacityError
    store, _, _, _ = store_setup
    monkeypatch.setattr(store, "MAX_NORMAL", 2)
    one = store.prepare_direct(binding(), prepared(ticket_id=1), "one")
    two = store.prepare_direct(binding(), prepared(ticket_id=2), "two")
    with pytest.raises(WriteCapacityError):
        store.prepare_direct(binding(), prepared(ticket_id=3), "three")
    assert store.get_for_owner(one.operation_id, binding()).state == "pending"
    assert store.get_for_owner(two.operation_id, binding()).state == "pending"
    monkeypatch.setattr(store, "MAX_UNRESOLVED", 1)
    store.claim_direct(one.operation_id, binding())
    with pytest.raises(WriteCapacityError):
        store.claim_direct(two.operation_id, binding())
    assert store.claim_direct(one.operation_id, binding()).record.state == "sending"


def test_claim_reserves_total_capacity_through_known_finish(store_setup, monkeypatch):
    from dynamix_manager.ticket_writes.store import WriteCapacityError
    store, path, _, _ = store_setup
    monkeypatch.setattr(store, "MAX_NORMAL", 2)
    _, fence = submit(store, prepared(ticket_id=1), "one")
    store.prepare_direct(binding(), prepared(ticket_id=2), "two")
    with pytest.raises(WriteCapacityError):
        store.prepare_direct(binding(), prepared(ticket_id=3), "three")
    store.finish(fence, "applied", "Applied.", status_code=200)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM ticket_write_operations").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM ticket_write_operations WHERE unresolved=1").fetchone()[0] == 0


def test_unknown_reserves_total_capacity_through_reconciliation(store_setup, monkeypatch):
    from dynamix_manager.ticket_writes.store import AuthoritativeAppliedEvidence, WriteCapacityError
    store, path, _, _ = store_setup
    monkeypatch.setattr(store, "MAX_NORMAL", 2)
    uncertain, fence = submit(store, prepared(ticket_id=1), "one")
    store.finish(fence, "unknown", "Unknown.")
    store.prepare_direct(binding(), prepared(ticket_id=2), "two")
    with pytest.raises(WriteCapacityError):
        store.prepare_direct(binding(), prepared(ticket_id=3), "three")
    store.reconcile_authoritative(
        uncertain.operation_id, AuthoritativeAppliedEvidence(evidence_id="tdx-event", observed_at=1_001.0))
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM ticket_write_operations").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM ticket_write_operations WHERE unresolved=1").fetchone()[0] == 0


def test_full_unresolved_pool_blocks_prepare_not_only_claim(store_setup, monkeypatch):
    from dynamix_manager.ticket_writes.store import WriteCapacityError
    store, _, _, _ = store_setup
    monkeypatch.setattr(store, "MAX_UNRESOLVED", 1)
    submit(store, prepared(ticket_id=1), "one")
    with pytest.raises(WriteCapacityError):
        store.prepare_direct(binding(), prepared(ticket_id=2), "two")


def test_reconciliation_requires_typed_authoritative_evidence(store_setup):
    from dynamix_manager.ticket_writes.store import AuthoritativeAppliedEvidence, InvalidReconciliationEvidence
    store, _, _, _ = store_setup
    record, fence = submit(store, prepared(), "request-1")
    store.finish(fence, "unknown", "No reliable response.")
    with pytest.raises(InvalidReconciliationEvidence):
        store.reconcile_authoritative(record.operation_id, {"outcome": "rejected", "reason": "not in feed"})
    resolved = store.reconcile_authoritative(
        record.operation_id, AuthoritativeAppliedEvidence(evidence_id="tdx-event-123", observed_at=1_001.0))
    assert resolved.state == "applied"
    assert store.prepare_direct(binding(), prepared(), "request-2").operation_id != record.operation_id


def test_reconciliation_with_a_binding_is_owner_only_and_records_the_note(store_setup):
    from dynamix_manager.ticket_writes.store import AuthoritativeRejectedEvidence, WriteBindingError
    store, path, _, _ = store_setup
    record, fence = submit(store, prepared(), "request-1")
    store.finish(fence, "unknown", "No reliable response.")
    evidence = AuthoritativeRejectedEvidence(evidence_id="owner-inspection", observed_at=1_001.0)
    with pytest.raises(WriteBindingError):
        store.reconcile_authoritative(record.operation_id, evidence, binding=binding(subject="user-2"))
    resolved = store.reconcile_authoritative(record.operation_id, evidence, binding=binding(),
                                             note="Feed shows no such comment.")
    assert resolved.state == "rejected"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM ticket_write_locks").fetchone()[0] == 0
        sealed = db.execute("SELECT value FROM ticket_write_audit WHERE id=?", (record.operation_id,)).fetchone()[0]
    assert store._open("audit", record.operation_id, sealed)["note"] == "Feed shows no such comment."


def test_ciphertext_record_swapping_is_detected(store_setup):
    from dynamix_manager.ticket_writes.store import WriteIntegrityError
    store, path, _, _ = store_setup
    first = store.prepare_direct(binding(), prepared(ticket_id=1), "one")
    second = store.prepare_direct(binding(), prepared(ticket_id=2), "two")
    with sqlite3.connect(path) as db:
        rows = db.execute("SELECT id,value FROM ticket_write_operations WHERE id IN (?,?) ORDER BY id",
                          (first.operation_id, second.operation_id)).fetchall()
        db.execute("UPDATE ticket_write_operations SET value=? WHERE id=?", (rows[1][1], rows[0][0]))
        db.execute("UPDATE ticket_write_operations SET value=? WHERE id=?", (rows[0][1], rows[1][0]))
    with pytest.raises(WriteIntegrityError):
        store.get_for_owner(first.operation_id, binding())


def test_audit_contains_only_encrypted_safe_fields(store_setup):
    store, path, _, _ = store_setup
    _, fence = submit(store, prepared(comments="payload-secret"), "request-1")
    store.finish(fence, "rejected", "safe summary", status_code=403)
    assert b"payload-secret" not in path.read_bytes()
    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(ticket_write_audit)")}
        assert columns == {"id", "created", "value"}


@pytest.mark.parametrize("bad", [
    binding(scopes=("tdx.read",)),
    binding(grant_expiry=999.0),
    binding(subject=""),
    binding(client_id=""),
    binding(resource=""),
    binding(family=""),
])
def test_prepare_direct_rejects_incomplete_or_non_write_binding(store_setup, bad):
    store, _, _, _ = store_setup
    with pytest.raises(ValueError):
        store.prepare_direct(bad, prepared(), "request-1")


def created(title="New printer", requestor="aaaaaaaa-0000-4000-8000-000000000001"):
    action = parse_action(dict(kind="create", title=title, type_id=3, account_id=11, requestor_uid=requestor))
    return PreparedChange(
        action=action, base_url="https://tenant.example/TDWebApi", app_id=42,
        baseline_json=json.dumps({"type": "Hardware"}),
        payload_json=json.dumps({"Title": title, "TypeID": 3}, sort_keys=True, separators=(",", ":")),
        preview=ChangePreview(application="InfoTech Tickets", ticket_id=0, ticket_title=title, action="create",
                              fields=(PreviewField(name="Title", before=None, after=title),)),
    )


def test_finish_persists_result_detail_for_created_tickets(store_setup):
    from dynamix_manager.ticket_writes.store import WriteStore
    store, path, key, clock = store_setup
    record, claim = submit(store, created(), "create-1")
    assert record.ticket_id == 0 if hasattr(record, "ticket_id") else True
    finished = store.finish(claim, "applied", "Created.", status_code=201,
                            detail={"ticket_id": 5555, "status": "New"})
    assert finished.result.detail == {"ticket_id": 5555, "status": "New"}
    reopened = WriteStore(CredentialVault(path, key), clock=lambda: clock[0])
    replay = reopened.lookup_direct(binding(), created().action, "create-1")
    assert replay.result.detail["ticket_id"] == 5555
    assert b"New printer" not in path.read_bytes()


def test_distinct_creations_do_not_lock_each_other_but_identical_ones_do(store_setup):
    store, _, _, _ = store_setup
    _, first = submit(store, created("New printer"), "create-1")
    assert first.claimed
    _, second = submit(store, created("New laptop"), "create-2")
    assert second.claimed, "a different creation must not wait on an unresolved one"
    from dynamix_manager.ticket_writes.store import EquivalentWriteBlocked
    with pytest.raises(EquivalentWriteBlocked):
        store.prepare_direct(binding(), created("New printer"), "create-3")
    store.finish(first, "applied", "Created.", status_code=201, detail={"ticket_id": 5555})
    _, again = submit(store, created("New printer"), "create-4")
    assert again.claimed, "a resolved creation no longer blocks an identical new request"


def asset_change(comments="Racked", asset_id=1973209):
    action = parse_action(dict(kind="asset_comment", asset_id=asset_id, comments=comments))
    return PreparedChange(action=action, base_url="https://tenant.example/TDWebApi", app_id=42, asset_app_id=928,
                          baseline_json=json.dumps({"ModifiedDate": "v1"}),
                          payload_json=json.dumps({"Comments": comments}, sort_keys=True, separators=(",", ":")),
                          preview=ChangePreview(application="InfoTech Assets/CIs", ticket_id=0, ticket_title="MacBook",
                                                action="asset_comment", fields=()))


def test_asset_and_ticket_operations_lock_independently_by_item(store_setup):
    store, _, _, _ = store_setup
    _, ticket_claim = submit(store, prepared(), "t-1")
    _, asset_claim = submit(store, asset_change(), "a-1")
    assert ticket_claim.claimed and asset_claim.claimed
    _, same_asset = submit(store, asset_change("Different"), "a-2")
    assert not same_asset.claimed and same_asset.record.state == "conflict"
    _, other_asset = submit(store, asset_change(asset_id=5), "a-3")
    assert other_asset.claimed
    assert asset_claim.record.prepared.asset_app_id == 928


def prepared_for(action, *, payload=None, baseline="v1"):
    parsed = parse_action(action)
    return PreparedChange(action=parsed, base_url="https://tenant.example/TDWebApi", app_id=42, asset_app_id=928, portal_app_id=2045,
                          baseline_json=json.dumps({"ModifiedDate": baseline}),
                          payload_json=json.dumps(payload or {"k": action["kind"]}, sort_keys=True, separators=(",", ":")),
                          preview=ChangePreview(application="Knowledge Base", ticket_id=0, ticket_title="t", action=action["kind"], fields=()))


def test_article_and_category_items_lock_independently(store_setup):
    store, _, _, _ = store_setup
    article = prepared_for(dict(kind="article_edit", article_id=95821, subject="A"))
    other_article = prepared_for(dict(kind="article_edit", article_id=84764, subject="B"))
    category = prepared_for(dict(kind="category_edit", category_id=95821, name="C"))
    first, fence = submit(store, article, "one")
    assert fence.claimed
    assert submit(store, other_article, "two")[1].claimed
    assert submit(store, category, "three")[1].claimed  # same numeric ID, different domain
    blocked = store.prepare_direct(binding(), prepared_for(dict(kind="article_edit", article_id=95821, tags=["x"]), payload={"k": "other"}), "four")
    assert not store.claim_direct(blocked.operation_id, binding()).claimed
