"""Direct orchestration for personal TeamDynamix ticket writes.

A submission durably claims its operation before refreshing the baseline, then makes at
most one mutation attempt.  The service does not claim external exactly-once delivery:
an interrupted request remains an unknown outcome and is never automatically retried.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from dynamix_manager.plugin import Connection

from .adapter import WriteAdapter, canonical_json
from .models import (
    AssetAction,
    AssignAction,
    CommentAction,
    CreateAction,
    EditAction,
    StatusAction,
    TaskAction,
    WriteResult,
)
from .store import DirectReplayResult, GrantBinding, WriteStore


class TicketWriteServiceError(RuntimeError):
    """Base class for safe orchestration failures."""


class WriteAuthorizationRequired(TicketWriteServiceError):
    """The caller or stored operation lacks current durable write consent."""


class WritesDisabled(TicketWriteServiceError):
    """The dynamic hosted-write feature gate is closed."""


class LinkedIdentityMismatch(TicketWriteServiceError):
    """The personal TeamDynamix credential is not the approved grant subject."""


class TicketPreparationRejected(TicketWriteServiceError):
    """A dependency could not produce a safe, complete validated change."""


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
    detail: dict | None = None
    item: dict | None = None  # non-ticket record the write applied to, e.g. an asset


_ACTION_TYPES = (CommentAction, StatusAction, AssignAction, EditAction, TaskAction, CreateAction, AssetAction)
_UNKNOWN_MESSAGE = "The upstream outcome is unknown; do not retry."
_PENDING_MESSAGE = ("The change was recorded but not yet sent to TeamDynamix; "
                    "resubmit it with the same request ID and arguments.")
_CONFLICT_MESSAGE = "The ticket or selected metadata changed after approval."
_REJECTED_MESSAGES = {
    401: "TeamDynamix denied permission for the change; nothing was changed.",
    403: "TeamDynamix denied permission for the change; nothing was changed.",
    429: "TeamDynamix rate limit reached; nothing was changed. Try again later with a new request ID.",
}
_REJECTED_MESSAGE = "TeamDynamix rejected the change."
_AUTH_MESSAGE = "Current write authorization is required."
_DISABLED_MESSAGE = "Hosted ticket writes are disabled."
_PREPARATION_MESSAGE = "The ticket change could not be safely prepared."


class TicketWriteService:
    """Coordinate validated, one-attempt personal ticket writes with durable deduplication."""

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
        credential_provider=None,
    ):
        self.settings = settings
        self.vault = vault
        self.auth_provider = auth_provider
        self.write_runtime = write_runtime
        self.store = store or WriteStore(vault)
        self.connection_factory = connection_factory or self._default_connection
        self.adapter_factory = adapter_factory or WriteAdapter.from_connection
        self.credential_provider = credential_provider or self._default_credential

    def _default_credential(self, subject):
        return self.vault.get(self.settings.issuer, subject)

    def _default_connection(self, values):
        return Connection(values)

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

    def _validate_binding(self, binding):
        try:
            return self.auth_provider.validate_write_grant(binding)
        except Exception:
            raise WriteAuthorizationRequired(_AUTH_MESSAGE) from None

    def _validate_stored_binding(self, record):
        try:
            current = self.auth_provider.validate_write_grant(record.binding.as_json())
            if GrantBinding.validate(current, float(self.store.clock())) != record.binding:
                raise ValueError("Grant changed.")
            return current
        except Exception:
            raise WriteAuthorizationRequired(_AUTH_MESSAGE) from None

    @contextmanager
    def _personal_adapter(self, binding):
        connection = None
        try:
            try:
                credential = self.credential_provider(binding.subject)
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

    def submit(self, principal, action, request_id):
        """Submit an explicitly requested change with durable request deduplication."""
        if not isinstance(action, _ACTION_TYPES):
            raise TypeError("A supported typed ticket action is required.")
        # write_grant_binding already re-validates the durable grant; the store repeats
        # its own strict shape check at every persistence point.
        binding = self._binding_for_principal(principal)
        original = GrantBinding.validate(binding, float(self.store.clock()))
        record = self.store.lookup_direct(binding, action, request_id)
        if record is None:
            with self._personal_adapter(original) as adapter:
                try:
                    prepared = adapter.validate(action)
                except Exception:
                    raise TicketPreparationRejected(_PREPARATION_MESSAGE) from None
            # Live reads may take long enough for the gate or grant to change.
            self._require_enabled()
            current = self._validate_binding(binding)
            if GrantBinding.validate(current, float(self.store.clock())) != original:
                raise WriteAuthorizationRequired(_AUTH_MESSAGE)
            record = self.store.prepare_direct(current, prepared, request_id)
        if isinstance(record, DirectReplayResult) or record.state != "pending":
            return self._safe_status(record)
        claim = self.store.claim_direct(record.operation_id, binding)
        if not claim.claimed:
            return self._safe_status(claim.record)

        def check_current():
            current = self.store.get_for_owner(claim.record.operation_id, binding)
            if current.state != "sending" or float(self.store.clock()) >= current.expires_at:
                raise WriteAuthorizationRequired(_AUTH_MESSAGE)

        return self._dispatch_claim(claim, check_current)

    def _dispatch_claim(self, claim, check_current):
        """Shared preflight and sole mutation attempt for a durable claim."""
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
                    # The claim can expire, the flag can close, or its durable
                    # grant can be revoked while preflight reads are in progress.
                    check_current()
                    self._require_enabled()
                    self._validate_stored_binding(claim.record)
                except Exception:
                    finished = self.store.finish(
                        claim, "rejected", "The change no longer has current authorization."
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

                # Only fixed, safe messages are persisted; adapter text never escapes.
                safe_message = {
                    "applied": ("TeamDynamix created the ticket." if claim.record.prepared.action.kind == "create"
                                else "TeamDynamix accepted the change."),
                    "rejected": _REJECTED_MESSAGES.get(result.status_code, _REJECTED_MESSAGE),
                    "unknown": _UNKNOWN_MESSAGE,
                }[result.outcome]
                finished = self.store.finish(
                    claim,
                    result.outcome,
                    safe_message,
                    status_code=result.status_code,
                    detail=result.detail if result.outcome == "applied" else None,
                )
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
    def _ticket_identity(record):
        """Ticket ID and result detail for a record; a created ticket's ID comes from the result."""
        if isinstance(record, DirectReplayResult):
            ticket_id, detail = record.ticket_id, record.result.detail
        else:
            ticket_id = record.prepared.action.ticket_id
            detail = record.result.detail if record.result else None
        if isinstance(detail, dict) and type(detail.get("ticket_id")) is int and detail["ticket_id"] > 0:
            ticket_id = detail["ticket_id"]
        return ticket_id, detail

    @classmethod
    def _ticket_url(cls, record):
        destination = record if isinstance(record, DirectReplayResult) else record.prepared
        parsed = urlsplit(destination.base_url)
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
        ticket_id, _ = cls._ticket_identity(record)
        base = f"https://{parsed.netloc}/TDNext/Apps/{destination.app_id}/Tickets/"
        return base + f"TicketDet.aspx?TicketID={ticket_id}" if ticket_id > 0 else base

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
        ticket_id, detail = self._ticket_identity(record)
        return TicketWriteStatus(
            operation_id=record.operation_id,
            outcome=effective,
            message=message,
            ticket_id=ticket_id,
            ticket_url=self._ticket_url(record),
            status_code=stored_result.status_code if stored_result else None,
            detail=detail,
            item=self._item(record),
        )

    @staticmethod
    def _item(record):
        if isinstance(record, DirectReplayResult) or not isinstance(record.prepared.action, AssetAction):
            return None
        prepared = record.prepared
        parsed = urlsplit(prepared.base_url)
        url = (f"https://{parsed.netloc}/TDNext/Apps/{prepared.asset_app_id}/Assets/AssetDet?AssetID={prepared.action.asset_id}"
               if prepared.asset_app_id else None)
        return {"type": "asset", "id": prepared.action.asset_id, "url": url}


def create_ticket_write_service(
    settings,
    vault,
    auth_provider,
    write_runtime,
    *,
    store=None,
    connection_factory=None,
    adapter_factory=None,
    credential_provider=None,
):
    """Production composition point for the hosted write tools."""
    return TicketWriteService(
        settings,
        vault,
        auth_provider,
        write_runtime,
        store=store,
        connection_factory=connection_factory,
        adapter_factory=adapter_factory,
        credential_provider=credential_provider,
    )
