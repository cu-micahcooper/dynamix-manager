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


def bind(store, issued, *, browser="browser-secret", csrf="csrf-secret"):
    return store.bind(issued.capability, browser, csrf)


def claim(store, issued, *, browser="browser-secret", csrf="csrf-secret"):
    return store.claim(issued.capability, browser, csrf)


def test_prepare_encrypts_immutable_record_and_stores_only_capability_hash(store_setup):
    store, path, _, _ = store_setup
    original = prepared()
    issued = store.prepare(binding(), original)
    assert issued.operation_id and issued.capability and issued.expires_at == 1_300.0

    record = bind(store, issued)
    assert record.prepared == original
    assert record.prepared.action.model_fields_set == original.action.model_fields_set
    assert record.binding.subject == "user-1"
    assert record.binding.scopes == frozenset({"tdx.read", "tdx.write"})
    assert record.state == "pending"

    raw = path.read_bytes()
    assert issued.capability.encode() not in raw
    assert b"secret ticket text" not in raw
    assert b"Sensitive title" not in raw
    assert b"family-1" not in raw
    with sqlite3.connect(path) as db:
        stored = db.execute(
            "SELECT capability_hash FROM ticket_write_operations WHERE id=?",
            (issued.operation_id,),
        ).fetchone()[0]
    assert len(stored) == 64 and stored != issued.capability


def test_binding_is_idempotent_for_same_browser_and_rejects_stolen_link(store_setup):
    from dynamix_manager.ticket_writes.store import WriteBindingError

    store, _, _, _ = store_setup
    issued = store.prepare(binding(), prepared())
    first = bind(store, issued)
    assert bind(store, issued) == first
    assert store.get(issued.capability, "browser-secret") == first
    assert store.check(issued.capability, "browser-secret", "csrf-secret") == first
    with pytest.raises(WriteBindingError):
        store.bind(issued.capability, "thief-browser", "thief-csrf")
    with pytest.raises(WriteBindingError):
        store.get(issued.capability, "thief-browser")
    with pytest.raises(WriteBindingError):
        store.check(issued.capability, "browser-secret", "wrong-csrf")


@pytest.mark.parametrize("csrf", [None, ""])
def test_check_and_claim_require_nonempty_csrf_but_get_remains_read_only(store_setup, csrf):
    from dynamix_manager.ticket_writes.store import WriteBindingError

    store, _, _, _ = store_setup
    issued = store.prepare(binding(), prepared())
    bind(store, issued)
    assert store.get(issued.capability, "browser-secret").state == "pending"
    with pytest.raises(WriteBindingError):
        store.check(issued.capability, "browser-secret", csrf)
    with pytest.raises(WriteBindingError):
        store.claim(issued.capability, "browser-secret", csrf)
    assert store.get(issued.capability, "browser-secret").state == "pending"


def test_expired_approval_cannot_bind_or_claim_and_is_retained_briefly(store_setup):
    from dynamix_manager.ticket_writes.store import WriteExpiredError

    store, _, _, clock = store_setup
    issued = store.prepare(binding(), prepared())
    bind(store, issued)
    clock[0] = issued.expires_at
    with pytest.raises(WriteExpiredError):
        store.check(issued.capability, "browser-secret", "csrf-secret")
    duplicate = claim(store, issued)
    assert duplicate.claimed is False
    assert duplicate.record.state == "expired"


def test_abandoned_full_pool_recovers_after_expiry_retention_without_prior_access(store_setup, monkeypatch):
    from dynamix_manager.ticket_writes.store import WriteNotFoundError

    store, _, _, clock = store_setup
    monkeypatch.setattr(store, "MAX_NORMAL", 1)
    abandoned = store.prepare(binding(), prepared(ticket_id=1))
    clock[0] = abandoned.expires_at + store.RESULT_RETENTION

    replacement = store.prepare(
        binding(grant_expiry=20_000.0), prepared(ticket_id=2)
    )
    assert replacement.operation_id != abandoned.operation_id
    with pytest.raises(WriteNotFoundError):
        store.get(abandoned.capability)


def test_claim_is_exactly_once_and_known_terminal_duplicate_keeps_result(store_setup):
    store, _, _, _ = store_setup
    issued = store.prepare(binding(), prepared())
    bind(store, issued)
    first = claim(store, issued)
    assert first.claimed is True and first.record.state == "sending"
    duplicate = claim(store, issued)
    assert duplicate.claimed is False and duplicate.record.state == "sending"

    applied = store.finish(first, "applied", "TeamDynamix accepted the change.", status_code=200)
    assert applied.state == "applied"
    assert applied.result.outcome == "applied"
    duplicate = claim(store, issued)
    assert duplicate.claimed is False
    assert duplicate.record.state == "applied"
    assert duplicate.record.result == applied.result


def test_claim_fence_cannot_overwrite_unknown_or_be_forged(store_setup):
    from dynamix_manager.ticket_writes.store import ClaimResult, WriteBindingError

    store, _, _, _ = store_setup
    issued = store.prepare(binding(), prepared())
    bind(store, issued)
    first = claim(store, issued)
    forged = ClaimResult(True, first.record, "forged-claim-secret")
    with pytest.raises(WriteBindingError):
        store.finish(forged, "applied", "Forged.")

    unknown = store.finish(first, "unknown", "The outcome is unknown.")
    repeated = store.finish(first, "applied", "Late overwrite attempt.", status_code=200)
    assert unknown.state == repeated.state == "unknown"
    assert repeated.result.message == "The outcome is unknown."


def test_capability_and_claim_secrets_are_redacted_from_representations(store_setup):
    store, _, _, _ = store_setup
    issued = store.prepare(binding(), prepared())
    assert issued.capability not in repr(issued)
    record = bind(store, issued)
    assert "secret ticket text" not in repr(record)
    claimed = claim(store, issued)
    assert claimed.claim_token not in repr(claimed)
    assert "secret ticket text" not in repr(claimed)


def test_same_ticket_lock_precedes_preflight_and_known_outcome_releases_it(store_setup):
    from dynamix_manager.ticket_writes.store import TicketLockedError

    store, _, _, _ = store_setup
    first = store.prepare(binding(), prepared(comments="first"))
    second = store.prepare(binding(), prepared(comments="second"))
    bind(store, first, browser="first-browser", csrf="first-csrf")
    bind(store, second, browser="second-browser", csrf="second-csrf")
    first_claim = claim(store, first, browser="first-browser", csrf="first-csrf")
    with pytest.raises(TicketLockedError):
        claim(store, second, browser="second-browser", csrf="second-csrf")
    store.finish(first_claim, "conflict", "The ticket changed before dispatch.")
    assert claim(store, second, browser="second-browser", csrf="second-csrf").claimed is True


def test_unknown_and_crash_left_sending_survive_expiry_cleanup_and_restart(store_setup):
    from dynamix_manager.ticket_writes.store import TicketLockedError, WriteStore

    store, path, key, clock = store_setup
    uncertain = store.prepare(binding(), prepared(comments="uncertain"))
    bind(store, uncertain, browser="u-browser", csrf="u-csrf")
    claim(store, uncertain, browser="u-browser", csrf="u-csrf")
    clock[0] = 100_000.0

    reopened = WriteStore(CredentialVault(path, key), clock=lambda: clock[0])
    status = reopened.claim(uncertain.capability, "u-browser", "u-csrf")
    assert status.claimed is False and status.record.state == "sending"
    assert status.record.effective_state == "unknown"
    other = reopened.prepare(binding(grant_expiry=200_000.0), prepared(comments="other"))
    bind(reopened, other, browser="o-browser", csrf="o-csrf")
    with pytest.raises(TicketLockedError):
        reopened.claim(other.capability, "o-browser", "o-csrf")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM ticket_write_markers").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM ticket_write_locks").fetchone()[0] == 1


def test_unknown_finish_retains_marker_and_blocks_equivalent_prepare(store_setup):
    from dynamix_manager.ticket_writes.store import EquivalentWriteBlocked

    store, _, _, _ = store_setup
    change = prepared()
    issued = store.prepare(binding(), change)
    bind(store, issued)
    unknown = store.finish(claim(store, issued), "unknown", "Outcome cannot be determined.")
    assert unknown.state == "unknown"
    with pytest.raises(EquivalentWriteBlocked):
        store.prepare(binding(), change)


def test_two_preexisting_equivalent_previews_allow_only_one_claim(store_setup):
    store, path, key, clock = store_setup
    first = store.prepare(binding(), prepared())
    second = store.prepare(binding(), prepared())
    bind(store, first, browser="first-browser", csrf="first-csrf")
    bind(store, second, browser="second-browser", csrf="second-csrf")

    def attempt(args):
        issued, browser, csrf = args
        from dynamix_manager.ticket_writes.store import WriteStore
        local = WriteStore(CredentialVault(path, key), clock=lambda: clock[0])
        result = local.claim(issued.capability, browser, csrf)
        return result.claimed, result.record.state

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [
            (first, "first-browser", "first-csrf"),
            (second, "second-browser", "second-csrf"),
        ]))
    assert sorted(results) == [(False, "conflict"), (True, "sending")]


def test_applied_claim_supersedes_equivalent_preexisting_preview(store_setup):
    store, _, _, _ = store_setup
    first = store.prepare(binding(), prepared())
    second = store.prepare(binding(), prepared())
    bind(store, first, browser="first-browser", csrf="first-csrf")
    bind(store, second, browser="second-browser", csrf="second-csrf")

    first_claim = claim(store, first, browser="first-browser", csrf="first-csrf")
    store.finish(first_claim, "applied", "Applied.", status_code=200)
    stale = claim(store, second, browser="second-browser", csrf="second-csrf")
    assert stale.claimed is False
    assert stale.record.state == "conflict"
    assert store.prepare(binding(), prepared()).operation_id != second.operation_id


def _process_claim(path, key, now, capability, browser, csrf, start, output):
    from dynamix_manager.hosted_vault import CredentialVault
    from dynamix_manager.ticket_writes.store import WriteStore

    local = WriteStore(CredentialVault(path, key), clock=lambda: now)
    start.wait(10)
    result = local.claim(capability, browser, csrf)
    output.put((result.claimed, result.record.state))


def test_claim_is_atomic_across_process_connections(store_setup):
    store, path, key, clock = store_setup
    issued = store.prepare(binding(), prepared())
    bind(store, issued)
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    output = context.Queue()
    processes = [context.Process(
        target=_process_claim,
        args=(str(path), key, clock[0], issued.capability, "browser-secret", "csrf-secret", start, output),
    ) for _ in range(2)]
    for process in processes:
        process.start()
    start.set()
    results = [output.get(timeout=15) for _ in processes]
    for process in processes:
        process.join(15)
        assert process.exitcode == 0
    assert sorted(results) == [(False, "sending"), (True, "sending")]


def test_normal_and_unresolved_capacity_fail_closed_without_eviction(store_setup, monkeypatch):
    from dynamix_manager.ticket_writes.store import WriteCapacityError

    store, _, _, _ = store_setup
    monkeypatch.setattr(store, "MAX_NORMAL", 2)
    one = store.prepare(binding(), prepared(ticket_id=1))
    two = store.prepare(binding(), prepared(ticket_id=2))
    with pytest.raises(WriteCapacityError):
        store.prepare(binding(), prepared(ticket_id=3))
    assert store.get(one.capability).state == "pending"
    assert store.get(two.capability).state == "pending"

    monkeypatch.setattr(store, "MAX_UNRESOLVED", 1)
    bind(store, one, browser="one", csrf="one-csrf")
    store.claim(one.capability, "one", "one-csrf")
    bind(store, two, browser="two", csrf="two-csrf")
    with pytest.raises(WriteCapacityError):
        store.claim(two.capability, "two", "two-csrf")
    assert store.claim(one.capability, "one", "one-csrf").record.state == "sending"


def test_claim_reserves_total_capacity_through_known_finish(store_setup, monkeypatch):
    from dynamix_manager.ticket_writes.store import WriteCapacityError

    store, path, _, _ = store_setup
    monkeypatch.setattr(store, "MAX_NORMAL", 2)
    claimed = store.prepare(binding(), prepared(ticket_id=1))
    bind(store, claimed, browser="claimed", csrf="claimed-csrf")
    fence = store.claim(claimed.capability, "claimed", "claimed-csrf")
    store.prepare(binding(), prepared(ticket_id=2))
    with pytest.raises(WriteCapacityError):
        store.prepare(binding(), prepared(ticket_id=3))

    store.finish(fence, "applied", "Applied.", status_code=200)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM ticket_write_operations").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM ticket_write_operations WHERE unresolved=1").fetchone()[0] == 0


def test_unknown_reserves_total_capacity_through_reconciliation(store_setup, monkeypatch):
    from dynamix_manager.ticket_writes.store import AuthoritativeAppliedEvidence, WriteCapacityError

    store, path, _, _ = store_setup
    monkeypatch.setattr(store, "MAX_NORMAL", 2)
    uncertain = store.prepare(binding(), prepared(ticket_id=1))
    bind(store, uncertain)
    fence = claim(store, uncertain)
    store.finish(fence, "unknown", "Unknown.")
    store.prepare(binding(), prepared(ticket_id=2))
    with pytest.raises(WriteCapacityError):
        store.prepare(binding(), prepared(ticket_id=3))

    store.reconcile_authoritative(
        uncertain.operation_id,
        AuthoritativeAppliedEvidence(evidence_id="tdx-event", observed_at=1_001.0),
    )
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM ticket_write_operations").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM ticket_write_operations WHERE unresolved=1").fetchone()[0] == 0


def test_full_unresolved_pool_blocks_prepare_not_only_claim(store_setup, monkeypatch):
    from dynamix_manager.ticket_writes.store import WriteCapacityError

    store, _, _, _ = store_setup
    monkeypatch.setattr(store, "MAX_UNRESOLVED", 1)
    uncertain = store.prepare(binding(), prepared(ticket_id=1))
    bind(store, uncertain)
    claim(store, uncertain)
    with pytest.raises(WriteCapacityError):
        store.prepare(binding(), prepared(ticket_id=2))


def test_reconciliation_requires_typed_authoritative_evidence(store_setup):
    from dynamix_manager.ticket_writes.store import (
        AuthoritativeAppliedEvidence,
        InvalidReconciliationEvidence,
    )

    store, _, _, _ = store_setup
    issued = store.prepare(binding(), prepared())
    bind(store, issued)
    store.finish(claim(store, issued), "unknown", "No reliable response.")
    with pytest.raises(InvalidReconciliationEvidence):
        store.reconcile_authoritative(issued.operation_id, {"outcome": "rejected", "reason": "not in feed"})

    resolved = store.reconcile_authoritative(
        issued.operation_id,
        AuthoritativeAppliedEvidence(evidence_id="tdx-event-123", observed_at=1_001.0),
    )
    assert resolved.state == "applied"
    assert store.prepare(binding(), prepared()).operation_id != issued.operation_id


def test_ciphertext_record_swapping_is_detected(store_setup):
    from dynamix_manager.ticket_writes.store import WriteIntegrityError

    store, path, _, _ = store_setup
    first = store.prepare(binding(), prepared(ticket_id=1))
    second = store.prepare(binding(), prepared(ticket_id=2))
    with sqlite3.connect(path) as db:
        rows = db.execute(
            "SELECT id,value FROM ticket_write_operations WHERE id IN (?,?) ORDER BY id",
            (first.operation_id, second.operation_id),
        ).fetchall()
        db.execute("UPDATE ticket_write_operations SET value=? WHERE id=?", (rows[1][1], rows[0][0]))
        db.execute("UPDATE ticket_write_operations SET value=? WHERE id=?", (rows[0][1], rows[1][0]))
    with pytest.raises(WriteIntegrityError):
        store.get(first.capability)


def test_audit_contains_only_encrypted_safe_fields(store_setup):
    store, path, _, _ = store_setup
    issued = store.prepare(binding(), prepared(comments="payload-secret"))
    bind(store, issued)
    store.finish(claim(store, issued), "rejected", "safe summary", status_code=403)
    raw = path.read_bytes()
    assert b"payload-secret" not in raw
    assert issued.capability.encode() not in raw
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
def test_prepare_rejects_incomplete_or_non_write_binding(store_setup, bad):
    store, _, _, _ = store_setup
    with pytest.raises(ValueError):
        store.prepare(bad, prepared())
