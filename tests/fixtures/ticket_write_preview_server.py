"""Synthetic HTTPS server for browser QA of the human ticket-write review flow."""

from __future__ import annotations

import argparse
import copy
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import uvicorn
from cryptography.fernet import Fernet
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from dynamix_manager.hosted_vault import CredentialVault
from dynamix_manager.personal_auth_store import OAuthStore
from dynamix_manager.ticket_writes.models import (
    ChangePreview,
    PreparedChange,
    PreviewField,
    WriteResult,
    parse_action,
)
from dynamix_manager.ticket_writes.routes import create_ticket_write_routes
from dynamix_manager.ticket_writes.service import TicketWriteService
from dynamix_manager.ticket_writes.store import WriteStore


UID = "11111111-1111-4111-8111-111111111111"
BASE = "https://tenant.example/TDWebApi"


class Provider:
    def __init__(self, vault, public_url):
        self.store = OAuthStore(vault)
        self.binding = {
            "subject": UID,
            "client_id": "browser-harness",
            "resource": public_url + "/mcp",
            "family": "browser-family",
            "scopes": ["tdx.read", "tdx.write"],
            "grant_expiry": time.time() + 3600,
        }

    def validate_write_grant(self, binding):
        if binding != self.binding:
            raise RuntimeError("Synthetic grant mismatch.")
        return copy.deepcopy(self.binding)


class Runtime:
    def require_enabled(self):
        return None


class Connection:
    def __init__(self):
        self.client = SimpleNamespace(session=SimpleNamespace(close=lambda: None))

    def identity(self):
        return UID


class Adapter:
    def __init__(self, state):
        self.state = state

    def validate(self, action):
        prepared = self.state.prepared[action.ticket_id]
        if self.state.scenarios[action.ticket_id] == "conflict":
            return prepared.model_copy(
                update={
                    "preview": prepared.preview.model_copy(
                        update={"ticket_title": prepared.preview.ticket_title + " externally changed"}
                    )
                }
            )
        return prepared

    def apply_once(self, prepared):
        ticket_id = prepared.action.ticket_id
        with self.state.lock:
            self.state.apply_count[ticket_id] += 1
        if self.state.scenarios[ticket_id] == "unknown":
            raise TimeoutError("Synthetic uncertain transport.")
        return WriteResult(outcome="applied", message="ignored", status_code=200)

    def snapshot(self, ticket_id):
        return {}


def prepared(ticket_id, *, kind, title, before, after, recipients=(), notices=()):
    if kind == "comment":
        action = parse_action(
            dict(
                kind="comment",
                ticket_id=ticket_id,
                comments=after,
                is_private=False,
                notify=list(recipients),
            )
        )
        field_name = "Comment"
        visibility = "public"
    else:
        action = parse_action(dict(kind="edit", ticket_id=ticket_id, description=after))
        field_name = "Description"
        visibility = None
    preview = ChangePreview(
        application="InfoTech Tickets",
        ticket_id=ticket_id,
        ticket_title=title,
        action=kind,
        fields=(PreviewField(name=field_name, before=before, after=after),),
        visibility=visibility,
        recipients=tuple(recipients),
        notices=tuple(notices),
    )
    return PreparedChange(
        action=action,
        base_url=BASE,
        app_id=42,
        baseline_json=f'{{"ID":{ticket_id},"ModifiedDate":"v1"}}',
        payload_json='{"synthetic":true}',
        preview=preview,
    )


def build_app(public_url, database):
    vault = CredentialVault(database, Fernet.generate_key())
    vault.put(public_url, UID, UID, "synthetic-personal-token", time.time() + 3600)
    provider = Provider(vault, public_url)
    store = WriteStore(vault)
    settings = SimpleNamespace(
        public_url=public_url,
        issuer=public_url,
        tdx_url=BASE,
        tdx_client_id="8",
    )
    state = SimpleNamespace(
        lock=threading.Lock(),
        prepared={},
        scenarios={},
        capabilities={},
        apply_count={},
        requests=[],
    )
    service = TicketWriteService(
        settings,
        vault,
        provider,
        Runtime(),
        store=store,
        connection_factory=lambda values: Connection(),
        adapter_factory=lambda connection: Adapter(state),
    )

    def reset_state():
        # This is a disposable synthetic harness, so reset means a genuinely
        # blank store even after an unknown outcome leaves a durable marker.
        with vault._db() as db:
            for table in (
                "ticket_write_operations",
                "ticket_write_markers",
                "ticket_write_locks",
                "ticket_write_audit",
            ):
                db.execute(f"DELETE FROM {table}")
            db.execute("DELETE FROM personal_oauth WHERE kind='rate'")
        definitions = {
            "comment": prepared(
                1201,
                kind="comment",
                title="Public comment with exact recipients",
                before=None,
                after="A concise public update for the requester.",
                recipients=("requester@example.invalid", "observer@example.invalid"),
                notices=(
                    "Only the listed email recipients are requested through Notify.",
                    "Preflight comparison cannot prevent a last-moment external edit.",
                ),
            ),
            "long_edit": prepared(
                1202,
                kind="edit",
                title="UnbrokenTitle" * 22,
                before="Earlier description",
                after=("LongUnbrokenValue" * 700) + "\nComplete ending marker.",
                notices=("All content must remain visible before Save.",),
            ),
            "conflict": prepared(
                1203,
                kind="edit",
                title="External race conflict",
                before="Version one",
                after="Version two",
                notices=("Preflight comparison cannot prevent a last-moment external edit.",),
            ),
            "unknown": prepared(
                1204,
                kind="comment",
                title="Unknown transport outcome",
                before=None,
                after="This transport will become uncertain.",
                notices=("Unknown outcomes must never be retried automatically.",),
            ),
        }
        state.capabilities = {}
        state.requests.clear()
        for name, value in definitions.items():
            state.prepared[value.action.ticket_id] = value
            state.scenarios[value.action.ticket_id] = name
            state.apply_count[value.action.ticket_id] = 0
            issued = store.prepare(provider.binding, value)
            state.capabilities[name] = issued.capability

    reset_state()

    async def status(request):
        return JSONResponse(
            {
                "ready": True,
                "review_urls": {
                    name: public_url + "/writes/review#" + capability
                    for name, capability in state.capabilities.items()
                },
                "apply_count": {
                    str(ticket_id): count for ticket_id, count in state.apply_count.items()
                },
                "requests": list(state.requests),
            },
            headers={"Cache-Control": "no-store"},
        )

    async def reset(request):
        await run_in_threadpool(reset_state)
        return await status(request)

    routes = [
        Route("/test/status", status, methods=["GET"]),
        Route("/test/reset", reset, methods=["POST"]),
        *create_ticket_write_routes(settings, service, provider.store),
    ]
    application = Starlette(routes=routes)

    class Recorder:
        async def __call__(self, scope, receive, send):
            if scope["type"] == "http" and scope["path"].startswith("/writes/"):
                headers = {key.decode().lower(): value.decode() for key, value in scope["headers"]}
                state.requests.append(
                    {
                        "method": scope["method"],
                        "path": scope["path"],
                        "query": scope.get("query_string", b"").decode(errors="replace"),
                        "origin": headers.get("origin"),
                        "has_cookie": "__Host-tdx-write=" in headers.get("cookie", ""),
                    }
                )
            await application(scope, receive, send)

    return Recorder()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--database")
    args = parser.parse_args()
    database = Path(args.database) if args.database else Path(
        tempfile.mkdtemp(prefix="tdx-write-browser-")
    ) / "vault.sqlite"
    public_url = f"https://{args.host}:{args.port}"
    uvicorn.run(
        build_app(public_url, database),
        host=args.host,
        port=args.port,
        ssl_certfile=args.cert,
        ssl_keyfile=args.key,
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
