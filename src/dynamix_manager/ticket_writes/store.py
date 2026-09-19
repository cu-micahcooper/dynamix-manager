"""Encrypted, bounded lifecycle storage for explicitly reviewed ticket writes.

This module owns no network client.  A successful :meth:`claim` durably fences an
operation and its ticket before the caller performs preflight or one upstream
write.  ``sending`` and ``unknown`` are deliberately not recoverable retries.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Literal, Mapping

from cryptography.fernet import InvalidToken

from .models import PreparedChange


class WriteStoreError(RuntimeError):
    pass


class WriteNotFoundError(WriteStoreError):
    pass


class WriteBindingError(WriteStoreError):
    pass


class WriteExpiredError(WriteStoreError):
    pass


class WriteCapacityError(WriteStoreError):
    pass


class EquivalentWriteBlocked(WriteStoreError):
    pass


class TicketLockedError(WriteStoreError):
    pass


class WriteIntegrityError(WriteStoreError):
    pass


class WriteStateError(WriteStoreError):
    pass


class InvalidReconciliationEvidence(WriteStoreError):
    pass


@dataclass(frozen=True)
class GrantBinding:
    subject: str
    client_id: str
    resource: str
    family: str
    scopes: frozenset[str]
    grant_expiry: float

    @classmethod
    def validate(cls, value: Mapping, now: float) -> "GrantBinding":
        if not isinstance(value, Mapping):
            raise ValueError("A validated write-grant binding is required.")
        required = {"subject", "client_id", "resource", "family", "scopes", "grant_expiry"}
        if set(value) != required:
            raise ValueError("The write-grant binding is incomplete.")
        names = [value[name] for name in ("subject", "client_id", "resource", "family")]
        if any(not isinstance(item, str) or not item for item in names):
            raise ValueError("The write-grant binding is incomplete.")
        raw_scopes = value["scopes"]
        if (not isinstance(raw_scopes, (list, tuple, set, frozenset))
                or any(not isinstance(item, str) or not item for item in raw_scopes)):
            raise ValueError("The write-grant scopes are invalid.")
        scopes = frozenset(raw_scopes)
        expiry = value["grant_expiry"]
        if isinstance(expiry, bool) or not isinstance(expiry, (int, float)):
            raise ValueError("The write grant has no valid expiry.")
        expiry = float(expiry)
        if not math.isfinite(expiry) or expiry <= now or "tdx.write" not in scopes:
            raise ValueError("An active tdx.write grant is required.")
        return cls(*names, scopes, expiry)

    def as_json(self):
        return {
            "subject": self.subject,
            "client_id": self.client_id,
            "resource": self.resource,
            "family": self.family,
            "scopes": sorted(self.scopes),
            "grant_expiry": self.grant_expiry,
        }


@dataclass(frozen=True)
class IssuedOperation:
    operation_id: str
    capability: str = field(repr=False)
    expires_at: float


@dataclass(frozen=True)
class StoredResult:
    outcome: Literal["applied", "rejected", "unknown", "conflict", "expired"]
    message: str
    status_code: int | None = None


@dataclass(frozen=True)
class OperationRecord:
    operation_id: str
    binding: GrantBinding
    prepared: PreparedChange = field(repr=False)
    state: Literal["pending", "sending", "applied", "rejected", "unknown", "conflict", "expired"]
    created_at: float
    expires_at: float
    result: StoredResult | None = None

    @property
    def effective_state(self):
        """A crash-left/in-flight ``sending`` record is externally uncertain."""
        return "unknown" if self.state == "sending" else self.state


@dataclass(frozen=True)
class ClaimResult:
    claimed: bool
    record: OperationRecord = field(repr=False)
    claim_token: str | None = field(default=None, repr=False)


def _validate_evidence(evidence_id, observed_at):
    if not isinstance(evidence_id, str) or not evidence_id.strip():
        raise ValueError("A specific authoritative evidence identifier is required.")
    if (isinstance(observed_at, bool) or not isinstance(observed_at, (int, float))
            or not math.isfinite(float(observed_at)) or observed_at <= 0):
        raise ValueError("An authoritative observation time is required.")


@dataclass(frozen=True)
class AuthoritativeAppliedEvidence:
    evidence_id: str
    observed_at: float

    def __post_init__(self):
        _validate_evidence(self.evidence_id, self.observed_at)


@dataclass(frozen=True)
class AuthoritativeRejectedEvidence:
    evidence_id: str
    observed_at: float

    def __post_init__(self):
        _validate_evidence(self.evidence_id, self.observed_at)


class WriteStore:
    """Durable write records stored in the protected credential-vault database."""

    APPROVAL_TTL = 300
    RESULT_RETENTION = 300
    AUDIT_RETENTION = 30 * 24 * 60 * 60
    MAX_NORMAL = 128
    MAX_UNRESOLVED = 1000
    MAX_AUDIT = 10_000
    _TERMINAL = frozenset({"applied", "rejected", "unknown", "conflict", "expired"})
    _KNOWN = frozenset({"applied", "rejected", "conflict", "expired"})

    def __init__(self, vault, clock=time.time):
        self.vault = vault
        self.clock = clock
        with self.vault._db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS ticket_write_operations (
                    id TEXT PRIMARY KEY,
                    capability_hash TEXT UNIQUE NOT NULL,
                    state TEXT NOT NULL,
                    expires REAL NOT NULL,
                    purge_at REAL,
                    unresolved INTEGER NOT NULL,
                    value BLOB NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS ticket_write_markers (
                    id TEXT PRIMARY KEY,
                    operation_id TEXT UNIQUE NOT NULL,
                    value BLOB NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS ticket_write_locks (
                    id TEXT PRIMARY KEY,
                    operation_id TEXT UNIQUE NOT NULL,
                    value BLOB NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS ticket_write_audit (
                    id TEXT PRIMARY KEY,
                    created REAL NOT NULL,
                    value BLOB NOT NULL
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS ticket_write_operations_expiry ON ticket_write_operations(expires)")
            db.execute("CREATE INDEX IF NOT EXISTS ticket_write_audit_created ON ticket_write_audit(created)")

    @staticmethod
    def _digest(domain, value):
        if not isinstance(value, (str, bytes)) or not value:
            raise WriteBindingError("A required operation secret is missing.")
        value = value.encode() if isinstance(value, str) else value
        return hashlib.sha256(domain.encode() + b"\0" + value).hexdigest()

    @staticmethod
    def _canonical(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False)

    def _seal(self, table, row_id, data):
        envelope = self._canonical({"table": table, "row_id": row_id, "data": data}).encode()
        return self.vault.cipher.encrypt(envelope)

    def _open(self, table, row_id, value):
        try:
            envelope = json.loads(self.vault.cipher.decrypt(value))
            if envelope.get("table") != table or envelope.get("row_id") != row_id:
                raise ValueError("binding")
            data = envelope["data"]
            if not isinstance(data, dict):
                raise ValueError("record")
            return data
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError, KeyError,
                TypeError, ValueError, AttributeError):
            raise WriteIntegrityError("Encrypted ticket-write state failed integrity validation.") from None

    def _transaction(self):
        return self.vault._db()

    def _begin(self, db):
        db.execute("BEGIN IMMEDIATE")
        self._cleanup(db, float(self.clock()))

    def _cleanup(self, db, now):
        db.execute("DELETE FROM ticket_write_audit WHERE created <= ?", (now - self.AUDIT_RETENTION,))
        rows = db.execute(
            "SELECT id,capability_hash,state,expires,purge_at,unresolved,value "
            "FROM ticket_write_operations WHERE state='pending' AND expires <= ?", (now,)
        ).fetchall()
        for row in rows:
            data = self._operation_data(row)
            data.update(state="expired", finished_at=now,
                        purge_at=data["expires_at"] + self.RESULT_RETENTION,
                        result={"outcome": "expired", "message": "The approval capability expired.",
                                "status_code": None})
            self._write_operation(db, data, unresolved=0)
        db.execute(
            "DELETE FROM ticket_write_operations WHERE purge_at IS NOT NULL AND purge_at <= ? "
            "AND state IN ('applied','rejected','conflict','expired')", (now,)
        )

    def _operation_row_by_capability(self, db, capability):
        capability_hash = self._digest("capability", capability)
        row = db.execute(
            "SELECT id,capability_hash,state,expires,purge_at,unresolved,value "
            "FROM ticket_write_operations WHERE capability_hash=?", (capability_hash,)
        ).fetchone()
        if row is None:
            raise WriteNotFoundError("Ticket-write operation was not found.")
        data = self._operation_data(row)
        if not hmac.compare_digest(data["capability_hash"], capability_hash):
            raise WriteIntegrityError("Capability binding failed integrity validation.")
        return data

    def _operation_row_by_id(self, db, operation_id):
        row = db.execute(
            "SELECT id,capability_hash,state,expires,purge_at,unresolved,value "
            "FROM ticket_write_operations WHERE id=?", (operation_id,)
        ).fetchone()
        if row is None:
            raise WriteNotFoundError("Ticket-write operation was not found.")
        return self._operation_data(row)

    def _operation_data(self, row):
        operation_id, capability_hash, state, expires, purge_at, unresolved, value = row
        data = self._open("operation", operation_id, value)
        comparisons = (
            hmac.compare_digest(str(data.get("id", "")), operation_id),
            hmac.compare_digest(str(data.get("capability_hash", "")), capability_hash),
            data.get("state") == state,
            data.get("expires_at") == expires,
            data.get("purge_at") == purge_at,
            int(bool(data.get("unresolved"))) == unresolved,
        )
        if not all(comparisons):
            raise WriteIntegrityError("Ticket-write record binding failed integrity validation.")
        return data

    def _write_operation(self, db, data, *, unresolved):
        data["unresolved"] = bool(unresolved)
        db.execute(
            "UPDATE ticket_write_operations SET state=?,expires=?,purge_at=?,unresolved=?,value=? WHERE id=?",
            (data["state"], data["expires_at"], data.get("purge_at"), int(bool(unresolved)),
             self._seal("operation", data["id"], data), data["id"]),
        )

    def _record(self, data):
        binding = GrantBinding(
            data["binding"]["subject"], data["binding"]["client_id"],
            data["binding"]["resource"], data["binding"]["family"],
            frozenset(data["binding"]["scopes"]), data["binding"]["grant_expiry"],
        )
        try:
            prepared = PreparedChange.model_validate_json(data["prepared_json"])
            result = StoredResult(**data["result"]) if data.get("result") else None
            return OperationRecord(data["id"], binding, prepared, data["state"],
                                   data["created_at"], data["expires_at"], result)
        except Exception:
            raise WriteIntegrityError("Stored ticket-write payload is invalid.") from None

    def _audit(self, db, data, outcome, now):
        operation_id = data["id"]
        exists = db.execute("SELECT 1 FROM ticket_write_audit WHERE id=?", (operation_id,)).fetchone()
        if not exists and db.execute("SELECT COUNT(*) FROM ticket_write_audit").fetchone()[0] >= self.MAX_AUDIT:
            raise WriteCapacityError("Ticket-write audit capacity reached.")
        audit = {
            "actor": data["binding"]["subject"],
            "action": data["kind"],
            "ticket_id": data["ticket_id"],
            "operation_id": operation_id,
            "time": now,
            "outcome": outcome,
        }
        db.execute(
            "INSERT OR REPLACE INTO ticket_write_audit(id,created,value) VALUES (?,?,?)",
            (operation_id, now, self._seal("audit", operation_id, audit)),
        )

    def _retained_count(self, db):
        # Preparation reserves a slot for the operation's entire retained
        # lifetime. Claiming cannot create capacity that finish later needs.
        return db.execute("SELECT COUNT(*) FROM ticket_write_operations").fetchone()[0]

    def _equivalence_hash(self, binding, prepared):
        try:
            payload = json.loads(prepared.payload_json)
        except (TypeError, json.JSONDecodeError):
            raise ValueError("Prepared payload is not valid JSON.") from None
        identity = [binding.subject, prepared.base_url, prepared.app_id,
                    prepared.action.ticket_id, prepared.action.kind, payload]
        return hashlib.sha256(self._canonical(identity).encode()).hexdigest()

    def _ticket_hash(self, prepared):
        identity = [prepared.base_url, prepared.app_id, prepared.action.ticket_id]
        return hashlib.sha256(self._canonical(identity).encode()).hexdigest()

    def prepare(self, binding, prepared):
        now = float(self.clock())
        binding = GrantBinding.validate(binding, now)
        if not isinstance(prepared, PreparedChange):
            raise ValueError("An immutable PreparedChange is required.")
        operation_id = secrets.token_hex(16)
        capability = secrets.token_urlsafe(32)
        capability_hash = self._digest("capability", capability)
        expires_at = min(now + self.APPROVAL_TTL, binding.grant_expiry)
        equivalence_hash = self._equivalence_hash(binding, prepared)
        ticket_hash = self._ticket_hash(prepared)
        data = {
            "id": operation_id,
            "capability_hash": capability_hash,
            "binding": binding.as_json(),
            "prepared_json": prepared.model_dump_json(),
            "kind": prepared.action.kind,
            "ticket_id": prepared.action.ticket_id,
            "state": "pending",
            "created_at": now,
            "expires_at": expires_at,
            "purge_at": None,
            "unresolved": False,
            "browser_hash": None,
            "csrf_hash": None,
            "claim_hash": None,
            "equivalence_hash": equivalence_hash,
            "ticket_hash": ticket_hash,
            "result": None,
        }
        with self._transaction() as db:
            self._begin(db)
            if db.execute("SELECT 1 FROM ticket_write_markers WHERE id=?", (equivalence_hash,)).fetchone():
                raise EquivalentWriteBlocked("An equivalent write has an unresolved outcome.")
            if db.execute("SELECT COUNT(*) FROM ticket_write_markers").fetchone()[0] >= self.MAX_UNRESOLVED:
                raise WriteCapacityError("Unresolved ticket-write capacity reached.")
            if self._retained_count(db) >= self.MAX_NORMAL:
                raise WriteCapacityError("Ticket-write preview capacity reached.")
            value = self._seal("operation", operation_id, data)
            db.execute(
                "INSERT INTO ticket_write_operations "
                "(id,capability_hash,state,expires,purge_at,unresolved,value) VALUES (?,?,?,?,?,?,?)",
                (operation_id, capability_hash, "pending", expires_at, None, 0, value),
            )
            self._audit(db, data, "pending", now)
        return IssuedOperation(operation_id, capability, expires_at)

    def _bind_checks(self, data, browser, csrf=None, *, require_csrf=False):
        browser_hash = self._digest("browser", browser)
        if data["browser_hash"] is None:
            raise WriteBindingError("The approval capability is not browser-bound.")
        if not hmac.compare_digest(data["browser_hash"], browser_hash):
            raise WriteBindingError("The approval capability belongs to another browser.")
        if require_csrf and csrf is None:
            raise WriteBindingError("The approval request failed CSRF validation.")
        if csrf is not None:
            csrf_hash = self._digest("csrf", csrf)
            if not hmac.compare_digest(data["csrf_hash"], csrf_hash):
                raise WriteBindingError("The approval request failed CSRF validation.")

    def bind(self, capability, browser, csrf):
        now = float(self.clock())
        browser_hash = self._digest("browser", browser)
        csrf_hash = self._digest("csrf", csrf)
        with self._transaction() as db:
            self._begin(db)
            data = self._operation_row_by_capability(db, capability)
            if data["state"] == "expired" or now >= data["expires_at"]:
                raise WriteExpiredError("The approval capability expired.")
            if data["browser_hash"] is None:
                data["browser_hash"] = browser_hash
                data["csrf_hash"] = csrf_hash
                self._write_operation(db, data, unresolved=data["unresolved"])
            elif (not hmac.compare_digest(data["browser_hash"], browser_hash)
                  or not hmac.compare_digest(data["csrf_hash"], csrf_hash)):
                raise WriteBindingError("The approval capability belongs to another browser.")
            return self._record(data)

    def get(self, capability, browser=None):
        with self._transaction() as db:
            self._begin(db)
            data = self._operation_row_by_capability(db, capability)
            if data["browser_hash"] is not None:
                if browser is None:
                    raise WriteBindingError("The browser binding is required.")
                self._bind_checks(data, browser)
            return self._record(data)

    def check(self, capability, browser, csrf):
        now = float(self.clock())
        with self._transaction() as db:
            self._begin(db)
            data = self._operation_row_by_capability(db, capability)
            self._bind_checks(data, browser, csrf, require_csrf=True)
            if data["state"] == "expired" or now >= data["expires_at"]:
                raise WriteExpiredError("The approval capability expired.")
            return self._record(data)

    def _marker(self, db, data):
        marker_id = data["equivalence_hash"]
        marker = {"id": marker_id, "operation_id": data["id"]}
        db.execute(
            "INSERT INTO ticket_write_markers(id,operation_id,value) VALUES (?,?,?)",
            (marker_id, data["id"], self._seal("marker", marker_id, marker)),
        )

    def _lock(self, db, data):
        lock_id = data["ticket_hash"]
        lock = {"id": lock_id, "operation_id": data["id"]}
        db.execute(
            "INSERT INTO ticket_write_locks(id,operation_id,value) VALUES (?,?,?)",
            (lock_id, data["id"], self._seal("lock", lock_id, lock)),
        )

    def _terminal_before_dispatch(self, db, data, now, outcome, message):
        data.update(state=outcome, finished_at=now, purge_at=now + self.RESULT_RETENTION,
                    result={"outcome": outcome, "message": message, "status_code": None})
        self._write_operation(db, data, unresolved=0)
        self._audit(db, data, outcome, now)
        return ClaimResult(False, self._record(data))

    def _supersede_pending_equivalents(self, db, applied, now):
        rows = db.execute(
            "SELECT id,capability_hash,state,expires,purge_at,unresolved,value "
            "FROM ticket_write_operations WHERE state='pending'"
        ).fetchall()
        for row in rows:
            other = self._operation_data(row)
            if other["equivalence_hash"] == applied["equivalence_hash"]:
                other.update(
                    state="conflict", finished_at=now, purge_at=now + self.RESULT_RETENTION,
                    result={"outcome": "conflict",
                            "message": "An equivalent earlier preview was already applied.",
                            "status_code": None},
                )
                self._write_operation(db, other, unresolved=0)
                self._audit(db, other, "conflict", now)

    def claim(self, capability, browser, csrf):
        now = float(self.clock())
        with self._transaction() as db:
            self._begin(db)
            data = self._operation_row_by_capability(db, capability)
            self._bind_checks(data, browser, csrf, require_csrf=True)
            if data["state"] != "pending":
                return ClaimResult(False, self._record(data))
            if now >= data["expires_at"]:
                return self._terminal_before_dispatch(
                    db, data, now, "expired", "The approval capability expired.")
            if db.execute("SELECT 1 FROM ticket_write_markers WHERE id=?",
                          (data["equivalence_hash"],)).fetchone():
                return self._terminal_before_dispatch(
                    db, data, now, "conflict", "An equivalent write is unresolved.")
            if db.execute("SELECT 1 FROM ticket_write_locks WHERE id=?",
                          (data["ticket_hash"],)).fetchone():
                raise TicketLockedError("Another write for this ticket is unresolved.")
            if db.execute("SELECT COUNT(*) FROM ticket_write_markers").fetchone()[0] >= self.MAX_UNRESOLVED:
                raise WriteCapacityError("Unresolved ticket-write capacity reached.")
            claim_token = secrets.token_urlsafe(32)
            data.update(state="sending", claim_hash=self._digest("claim", claim_token),
                        unresolved=True)
            try:
                self._lock(db, data)
                self._marker(db, data)
            except sqlite3.IntegrityError:
                # BEGIN IMMEDIATE makes this defensive path rare, but the unique
                # constraints remain the cross-process authority.
                if db.execute("SELECT 1 FROM ticket_write_markers WHERE id=?",
                              (data["equivalence_hash"],)).fetchone():
                    db.execute("DELETE FROM ticket_write_locks WHERE operation_id=?", (data["id"],))
                    return self._terminal_before_dispatch(
                        db, data, now, "conflict", "An equivalent write is unresolved.")
                raise TicketLockedError("Another write for this ticket is unresolved.") from None
            self._write_operation(db, data, unresolved=1)
            self._audit(db, data, "sending", now)
            return ClaimResult(True, self._record(data), claim_token)

    def finish(self, claim, outcome, message, *, status_code=None):
        if not isinstance(claim, ClaimResult) or not claim.claimed or not claim.claim_token:
            raise WriteStateError("A successful claim fence is required.")
        if outcome not in {"applied", "rejected", "unknown", "conflict"}:
            raise ValueError("Unsupported ticket-write outcome.")
        if not isinstance(message, str) or not message:
            raise ValueError("A safe result message is required.")
        if status_code is not None and (type(status_code) is not int or not 100 <= status_code <= 599):
            raise ValueError("Invalid result status code.")
        now = float(self.clock())
        with self._transaction() as db:
            self._begin(db)
            data = self._operation_row_by_id(db, claim.record.operation_id)
            claim_hash = self._digest("claim", claim.claim_token)
            if not data.get("claim_hash") or not hmac.compare_digest(data["claim_hash"], claim_hash):
                raise WriteBindingError("The claim fence is invalid.")
            if data["state"] != "sending":
                return self._record(data)
            data.update(state=outcome, finished_at=now,
                        purge_at=None if outcome == "unknown" else now + self.RESULT_RETENTION,
                        result={"outcome": outcome, "message": message,
                                "status_code": status_code})
            marker_unresolved = outcome == "unknown"
            if not marker_unresolved:
                if outcome == "applied":
                    self._supersede_pending_equivalents(db, data, now)
                db.execute("DELETE FROM ticket_write_markers WHERE operation_id=?", (data["id"],))
                db.execute("DELETE FROM ticket_write_locks WHERE operation_id=?", (data["id"],))
            self._write_operation(db, data, unresolved=marker_unresolved)
            self._audit(db, data, outcome, now)
            return self._record(data)

    def reconcile_authoritative(self, operation_id, evidence):
        """Internal resolution hook; only typed, specific evidence is accepted.

        A missing item in a bounded feed is intentionally not representable as
        evidence.  This method records a known result and never makes a retryable
        operation.
        """
        if type(evidence) is AuthoritativeAppliedEvidence:
            outcome, message = "applied", "Authoritative evidence confirms the change."
        elif type(evidence) is AuthoritativeRejectedEvidence:
            outcome, message = "rejected", "Authoritative evidence confirms no change was applied."
        else:
            raise InvalidReconciliationEvidence("Typed authoritative evidence is required.")
        now = float(self.clock())
        with self._transaction() as db:
            self._begin(db)
            data = self._operation_row_by_id(db, operation_id)
            if data["state"] in {"applied", "rejected"}:
                return self._record(data)
            if data["state"] not in {"sending", "unknown"}:
                raise WriteStateError("Only an unresolved dispatched operation can be reconciled.")
            data.update(state=outcome, finished_at=now, purge_at=now + self.RESULT_RETENTION,
                        result={"outcome": outcome, "message": message, "status_code": None})
            if outcome == "applied":
                self._supersede_pending_equivalents(db, data, now)
            db.execute("DELETE FROM ticket_write_markers WHERE operation_id=?", (data["id"],))
            db.execute("DELETE FROM ticket_write_locks WHERE operation_id=?", (data["id"],))
            self._write_operation(db, data, unresolved=0)
            self._audit(db, data, outcome, now)
            return self._record(data)
