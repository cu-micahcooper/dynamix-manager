"""Explicit-review orchestration for personal TeamDynamix ticket writes.

Preparation performs only authenticated reads and persists the exact immutable
change that was shown to the user.  Commit durably claims the operation before
refreshing its baseline, then makes at most one mutation attempt.  The service
does not claim external exactly-once delivery: an interrupted request remains an
unknown outcome and is never automatically retried.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

from dynamix_manager.plugin import Connection

from .adapter import WriteAdapter, canonical_json
from .models import (
    AssignAction,
    ChangePreview,
    CommentAction,
    EditAction,
    StatusAction,
    WriteResult,
)
from .store import GrantBinding, TicketLockedError, WriteStore, WriteStoreError


class TicketWriteServiceError(RuntimeError):
    """Base class for safe orchestration failures."""


class WriteAuthorizationRequired(TicketWriteServiceError):
    """The caller or stored operation lacks current durable write consent."""


class WritesDisabled(TicketWriteServiceError):
    """The dynamic hosted-write feature gate is closed."""


class LinkedIdentityMismatch(TicketWriteServiceError):
    """The personal TeamDynamix credential is not the approved grant subject."""


class TicketPreparationRejected(TicketWriteServiceError):
    """A dependency could not produce a safe, complete immutable preview."""


@dataclass(frozen=True)
class PreparedOperation:
    operation_id: str
    preview: ChangePreview = field(repr=False)
    review_url: str = field(repr=False)
    expires_at: float


@dataclass(frozen=True)
class TicketWriteStatus:
    operation_id: str
    outcome: Literal[
        "pending", "applied", "rejected", "unknown", "conflict", "expired"
    ]
    message: str
    ticket_id: int
    ticket_url: str
    status_code: int | None = None


_ACTION_TYPES = (CommentAction, StatusAction, AssignAction, EditAction)
_UNKNOWN_MESSAGE = "The upstream outcome is unknown; do not retry."
_PENDING_MESSAGE = "The change is awaiting explicit review and Save."
_CONFLICT_MESSAGE = "The ticket or selected metadata changed after approval."
_AUTH_MESSAGE = "Current write authorization is required."
_DISABLED_MESSAGE = "Hosted ticket writes are disabled."
_PREPARATION_MESSAGE = "The ticket change could not be safely prepared."


class TicketWriteService:
    """Coordinate immutable previews and one-attempt personal ticket writes."""

    def __init__(
        self,
        settings,
        vault,
        auth_provider,
        write_runtime,
        *,
        store=None,
        connection_factory=None,
        adapter_factory=None,
    ):
        self.settings = settings
        self.vault = vault
        self.auth_provider = auth_provider
        self.write_runtime = write_runtime
        self.store = store or WriteStore(vault)
        self.connection_factory = connection_factory or self._default_connection
        self.adapter_factory = adapter_factory or WriteAdapter.from_connection

    def _default_connection(self, values):
        return Connection("/unused", values=values)

    def _require_enabled(self):
        try:
            self.write_runtime.require_enabled()
        except Exception:
            raise WritesDisabled(_DISABLED_MESSAGE) from None

    def _binding_for_principal(self, principal, *, require_enabled=True):
        if require_enabled:
            self._require_enabled()
        try:
            return self.auth_provider.write_grant_binding(principal)
        except Exception:
            raise WriteAuthorizationRequired(_AUTH_MESSAGE) from None

    def _validate_stored_binding(self, record):
        try:
            return self.auth_provider.validate_write_grant(record.binding.as_json())
        except Exception:
            raise WriteAuthorizationRequired(_AUTH_MESSAGE) from None

    @contextmanager
    def _personal_adapter(self, binding):
        connection = None
        try:
            try:
                credential = self.vault.get(self.settings.issuer, binding.subject)
                uid = credential["uid"]
                token = credential["token"]
            except Exception:
                raise WriteAuthorizationRequired(_AUTH_MESSAGE) from None
            if uid != binding.subject or not isinstance(token, str) or not token:
                raise LinkedIdentityMismatch(
                    "Personal TeamDynamix identity did not match the linked account."
                )
            values = {
                "TDX_BASE_URL": self.settings.tdx_url,
                "TDX_APP_ID": self.settings.tdx_client_id,
                "WORKBENCH_PERSONAL_TOKEN": token,
            }
            try:
                connection = self.connection_factory(values)
            except Exception:
                raise TicketPreparationRejected(_PREPARATION_MESSAGE) from None
            try:
                identity = connection.identity()
            except Exception:
                raise LinkedIdentityMismatch(
                    "Personal TeamDynamix identity did not match the linked account."
                ) from None
            if identity != uid or identity != binding.subject:
                raise LinkedIdentityMismatch(
                    "Personal TeamDynamix identity did not match the linked account."
                )
            try:
                adapter = self.adapter_factory(connection)
            except Exception:
                raise TicketPreparationRejected(_PREPARATION_MESSAGE) from None
            yield adapter
        finally:
            if connection is not None:
                try:
                    connection.client.session.close()
                except Exception:
                    pass

    def prepare(self, principal, action):
        """Validate a typed action using live reads and issue a review capability."""
        if not isinstance(action, _ACTION_TYPES):
            raise TypeError("A supported typed ticket action is required.")
        binding = self._binding_for_principal(principal)
        # Store validation is an independent final shape/scope/expiry check.
        try:
            with self._personal_adapter_binding(binding) as (normalized, adapter):
                try:
                    prepared = adapter.validate(action)
                except Exception:
                    raise TicketPreparationRejected(_PREPARATION_MESSAGE) from None
                # Live reads may take long enough for the dynamic gate or durable
                # grant to change. Recheck both immediately before persistence so
                # no stale approval capability is issued.
                self._require_enabled()
                current = self._validate_binding(normalized)
                original_grant = GrantBinding.validate(
                    normalized, float(self.store.clock())
                )
                current_grant = GrantBinding.validate(
                    current, float(self.store.clock())
                )
                if current_grant != original_grant:
                    raise WriteAuthorizationRequired(_AUTH_MESSAGE)
                issued = self.store.prepare(current, prepared)
        except (TicketWriteServiceError, WriteStoreError):
            raise
        except Exception:
            raise TicketPreparationRejected(_PREPARATION_MESSAGE) from None
        review_url = (
            self.settings.public_url
            + "/writes/review#"
            + issued.capability
        )
        return PreparedOperation(
            operation_id=issued.operation_id,
            preview=prepared.preview,
            review_url=review_url,
            expires_at=issued.expires_at,
        )

    @contextmanager
    def _personal_adapter_binding(self, binding):
        """Normalize a freshly loaded binding and open its personal connection."""
        normalized = self._validate_binding(binding)
        # Use the store's strict binding normalization before any operation is
        # persisted; its prepare() repeats this check at the actual write point.
        grant = GrantBinding.validate(normalized, float(self.store.clock()))
        with self._personal_adapter(grant) as adapter:
            yield normalized, adapter

    def _validate_binding(self, binding):
        try:
            return self.auth_provider.validate_write_grant(binding)
        except Exception:
            raise WriteAuthorizationRequired(_AUTH_MESSAGE) from None

    def open_review(self, capability, browser, csrf):
        """Validate current authority, then bind a preview to one browser/CSRF pair."""
        record = self.store.get(capability, browser)
        self._require_enabled()
        self._validate_stored_binding(record)
        return self.store.bind(capability, browser, csrf)

    def commit(self, capability, browser, csrf):
        """Apply an approved immutable change at most once."""
        record = self.store.check(capability, browser, csrf)
        self._require_enabled()
        self._validate_stored_binding(record)
        if record.state != "pending":
            return self._safe_status(record)
        try:
            claim = self.store.claim(capability, browser, csrf)
        except TicketLockedError:
            return self._safe_status(
                record,
                outcome="conflict",
                message="Another write for this ticket is still unresolved.",
            )
        if not claim.claimed:
            return self._safe_status(claim.record)

        dispatched = False
        try:
            with self._personal_adapter(claim.record.binding) as adapter:
                try:
                    refreshed = adapter.validate(claim.record.prepared.action)
                except Exception:
                    finished = self.store.finish(
                        claim, "conflict", _CONFLICT_MESSAGE
                    )
                    return self._safe_status(finished)
                if not self._same_approved_change(claim.record.prepared, refreshed):
                    finished = self.store.finish(
                        claim, "conflict", _CONFLICT_MESSAGE
                    )
                    return self._safe_status(finished)

                try:
                    # The approval can expire, the flag can close, or its durable
                    # grant can be revoked while preflight reads are in progress.
                    self.store.check(capability, browser, csrf)
                    self._require_enabled()
                    self._validate_stored_binding(claim.record)
                except Exception:
                    finished = self.store.finish(
                        claim, "rejected", "The change no longer has current approval."
                    )
                    return self._safe_status(finished)

                try:
                    # From this point forward any exception is conservatively
                    # uncertain: apply_once owns the sole mutation transport.
                    dispatched = True
                    result = adapter.apply_once(claim.record.prepared)
                    if not isinstance(result, WriteResult):
                        raise TypeError("Invalid adapter result.")
                except Exception:
                    finished = self.store.finish(
                        claim, "unknown", _UNKNOWN_MESSAGE
                    )
                    return self._safe_status(finished)

                # The accepted/rejected/unknown dispatch result is durable before
                # any optional read-back. A read failure cannot make an accepted
                # mutation retryable.
                safe_message = {
                    "applied": "TeamDynamix accepted the change.",
                    "rejected": "TeamDynamix rejected the change.",
                    "unknown": _UNKNOWN_MESSAGE,
                }[result.outcome]
                finished = self.store.finish(
                    claim,
                    result.outcome,
                    safe_message,
                    status_code=result.status_code,
                )
                if result.outcome == "applied":
                    try:
                        adapter.snapshot(claim.record.prepared.action.ticket_id)
                    except Exception:
                        pass
                return self._safe_status(finished)
        except (WriteAuthorizationRequired, LinkedIdentityMismatch, WritesDisabled):
            finished = self.store.finish(
                claim, "rejected", "The change no longer has current authorization."
            )
            return self._safe_status(finished)
        except Exception:
            if dispatched:
                # If durable outcome recording itself failed, a second finish is
                # safe: it either records unknown or returns the already-terminal
                # record without changing it.
                finished = self.store.finish(claim, "unknown", _UNKNOWN_MESSAGE)
                return self._safe_status(finished)
            finished = self.store.finish(
                claim, "rejected", "The change could not be safely dispatched."
            )
            return self._safe_status(finished)

    def result(self, principal, operation_id):
        """Return a safe status for the exact current grant owner; never dispatch."""
        binding = self._binding_for_principal(principal, require_enabled=False)
        record = self.store.get_for_owner(operation_id, binding)
        return self._safe_status(record)

    def status_for_record(self, record):
        """Project a record returned by validated ``open_review`` into safe UI data."""
        return self._safe_status(record)

    @staticmethod
    def _same_approved_change(stored, refreshed):
        if (
            stored.action != refreshed.action
            or stored.base_url != refreshed.base_url
            or stored.app_id != refreshed.app_id
            or stored.preview != refreshed.preview
        ):
            return False
        try:
            return (
                canonical_json(json.loads(stored.baseline_json))
                == canonical_json(json.loads(refreshed.baseline_json))
                and canonical_json(json.loads(stored.payload_json))
                == canonical_json(json.loads(refreshed.payload_json))
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return False

    @staticmethod
    def _ticket_url(record):
        prepared = record.prepared
        parsed = urlsplit(prepared.base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path.lower() != "/tdwebapi"
        ):
            raise TicketWriteServiceError("Stored ticket destination is invalid.")
        ticket_id = prepared.action.ticket_id
        return (
            f"https://{parsed.netloc}/TDNext/Apps/{prepared.app_id}/Tickets/"
            f"TicketDet.aspx?TicketID={ticket_id}"
        )

    def _safe_status(self, record, *, outcome=None, message=None):
        effective = outcome or record.effective_state
        stored_result = record.result
        if message is None:
            if stored_result is not None:
                message = stored_result.message
            elif effective == "unknown":
                message = _UNKNOWN_MESSAGE
            elif effective == "pending":
                message = _PENDING_MESSAGE
            else:
                message = "The ticket-write operation is no longer pending."
        return TicketWriteStatus(
            operation_id=record.operation_id,
            outcome=effective,
            message=message,
            ticket_id=record.prepared.action.ticket_id,
            ticket_url=self._ticket_url(record),
            status_code=stored_result.status_code if stored_result else None,
        )


def create_ticket_write_service(
    settings,
    vault,
    auth_provider,
    write_runtime,
    *,
    store=None,
    connection_factory=None,
    adapter_factory=None,
):
    """Production composition point for forthcoming hosted routes and tools."""
    return TicketWriteService(
        settings,
        vault,
        auth_provider,
        write_runtime,
        store=store,
        connection_factory=connection_factory,
        adapter_factory=adapter_factory,
    )
