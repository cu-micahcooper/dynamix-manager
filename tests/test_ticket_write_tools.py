import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult

from dynamix_manager.plugin import create_server
from dynamix_manager.ticket_writes.models import ChangePreview, PreviewField
from dynamix_manager.ticket_writes.service import (
    PreparedOperation,
    TicketPreparationRejected,
    TicketWriteStatus,
    WriteAuthorizationRequired,
    WritesDisabled,
)


class FakeService:
    def __init__(self):
        self.actions = []
        self.failure = None
        self.malformed = False
        self.thread_ids = []
        self.settings = SimpleNamespace(public_url="https://connector.example")

    def prepare(self, principal, action):
        self.thread_ids.append(threading.get_ident())
        if self.failure:
            raise self.failure
        self.actions.append(action)
        return PreparedOperation(
            operation_id="privatevalue" if self.malformed else "a" * 32,
            preview=ChangePreview(
                application="InfoTech Tickets",
                ticket_id=action.ticket_id,
                ticket_title="Synthetic ticket",
                action=action.kind,
                fields=(PreviewField(name="Comment", before=None, after="Synthetic"),),
            ),
            review_url="https://connector.example/writes/review#review-capability",
            expires_at=1234.0,
        )

    def result(self, principal, operation_id):
        if self.failure:
            raise self.failure
        return TicketWriteStatus(
            operation_id="privatevalue" if self.malformed else operation_id,
            outcome="pending",
            message="The change is awaiting explicit review and Save.",
            ticket_id=1001,
            ticket_url="https://tenant.example/TDNext/Apps/42/Tickets/TicketDet.aspx?TicketID=1001",
        )


class FakeConnection:
    base_url = "https://tenant.example/TDWebApi"
    app_id = 42
    header_app_id = "8"
    token = "private-token"
    auth_mode = "user"
    application = {"Name": "InfoTech Tickets"}
    client = SimpleNamespace(session=SimpleNamespace(close=lambda: None))

    def ready(self):
        return self


def run(server, name, arguments):
    return asyncio.run(server.call_tool(name, arguments))


def structured(result):
    if isinstance(result, CallToolResult):
        return result.structuredContent
    return result[1] if isinstance(result, tuple) else result


@pytest.fixture
def tools_server():
    service = FakeService()
    connection = FakeConnection()
    server = create_server(
        "/unused",
        connection_provider=lambda: connection,
        write_service=service,
    )
    return server, service


def test_registers_only_four_prepare_tools_and_two_bounded_read_tools(tools_server):
    server, _ = tools_server
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}
    assert set(tools) == {
        "connection_status", "ticket_statuses", "search_tickets", "my_queue",
        "get_ticket", "ticket_feed", "survey_report", "days_off",
        "prepare_ticket_comment", "prepare_ticket_status",
        "prepare_ticket_assignment", "prepare_ticket_edit",
        "ticket_write_metadata", "ticket_write_result",
    }
    for name in (
        "prepare_ticket_comment", "prepare_ticket_status",
        "prepare_ticket_assignment", "prepare_ticket_edit",
    ):
        tool = tools[name]
        assert tool.annotations.readOnlyHint is False
        assert tool.annotations.destructiveHint is True
        assert tool.annotations.idempotentHint is False
        assert tool.annotations.openWorldHint is True
        assert tool.meta["securitySchemes"][0]["scopes"] == ["tdx.read", "tdx.write"]
        assert "ui" not in tool.meta and "openai/widgetAccessible" not in tool.meta
        assert tool.parameters["additionalProperties"] is False
        assert tool.parameters["$defs"][next(iter(tool.parameters["$defs"]))]["additionalProperties"] is False
    assert tools["ticket_write_result"].annotations.readOnlyHint is True
    assert tools["ticket_write_result"].meta["securitySchemes"][0]["scopes"] == ["tdx.read", "tdx.write"]
    assert tools["ticket_write_metadata"].annotations.readOnlyHint is True
    assert tools["ticket_write_metadata"].meta["securitySchemes"][0]["scopes"] == ["tdx.read"]
    assert not any(name.startswith(("create_", "commit_", "reconcile_")) for name in tools)


@pytest.mark.parametrize(
    ("name", "action"),
    [
        ("prepare_ticket_comment", {"kind": "comment", "ticket_id": 1001, "comments": "Hello"}),
        ("prepare_ticket_status", {"kind": "status", "ticket_id": 1001, "comments": "Done", "status_id": 5}),
        ("prepare_ticket_assignment", {"kind": "assign", "ticket_id": 1001, "responsible_group_id": 4}),
        ("prepare_ticket_edit", {"kind": "edit", "ticket_id": 1001, "description": None}),
    ],
)
def test_prepare_tools_use_strict_typed_actions_and_return_not_saved_review(name, action, tools_server):
    server, service = tools_server
    result = structured(run(server, name, {"action": action}))
    expected_preview = ChangePreview(
        application="InfoTech Tickets",
        ticket_id=action["ticket_id"],
        ticket_title="Synthetic ticket",
        action=action["kind"],
        fields=(PreviewField(name="Comment", before=None, after="Synthetic"),),
    ).model_dump(mode="json")
    assert result == {
        "not_saved": True,
        "message": "NOT SAVED. Review the immutable preview and use the review link to approve Save.",
        "operation_id": "a" * 32,
        "preview": expected_preview,
        "review_url": "https://connector.example/writes/review#review-capability",
        "expires_at": 1234.0,
    }
    captured = service.actions[-1]
    assert captured.kind == action["kind"]
    if action["kind"] == "assign":
        assert captured.model_fields_set == {"kind", "ticket_id", "responsible_group_id"}
    if action["kind"] == "edit":
        assert captured.description is None
        assert "description" in captured.model_fields_set


def test_result_returns_only_safe_owner_checked_projection(tools_server):
    server, _ = tools_server
    result = structured(run(server, "ticket_write_result", {"operation_id": "b" * 32}))
    assert result == {
        "operation_id": "b" * 32,
        "outcome": "pending",
        "message": "The change is awaiting explicit review and Save.",
        "ticket_id": 1001,
        "ticket_url": "https://tenant.example/TDNext/Apps/42/Tickets/TicketDet.aspx?TicketID=1001",
        "status_code": None,
    }


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("prepare_ticket_comment", {"action": {"ticket_id": 1, "comments": "missing kind"}}),
        ("prepare_ticket_comment", {"action": {"kind": "comment", "ticket_id": 1, "comments": "x", "privatecanary": "x"}}),
        ("prepare_ticket_comment", {"action": {"kind": "comment", "ticket_id": 1, "comments": "x", "notify": ["privatevalue"]}}),
        ("prepare_ticket_assignment", {"action": {"kind": "assign", "ticket_id": 1, "responsible_uid": None}}),
        ("prepare_ticket_edit", {"action": {"kind": "edit", "ticket_id": 1}}),
        ("ticket_write_result", {"operation_id": "A" * 32}),
        ("ticket_write_result", {"operation_id": "a" * 31}),
        ("prepare_ticket_comment", ["privatevalue"]),
    ],
)
def test_real_mcp_validation_is_strict_and_redacts_inputs(name, arguments, tools_server):
    server, service = tools_server
    with pytest.raises(Exception) as error:
        run(server, name, arguments)
    serialized = str(error.value)
    assert "privatecanary" not in serialized
    assert "privatevalue" not in serialized
    assert "Invalid tool arguments" in serialized
    assert service.actions == []


def test_outer_unknown_argument_is_rejected_without_echo(tools_server):
    server, service = tools_server
    with pytest.raises(Exception) as error:
        run(server, "prepare_ticket_comment", {
            "action": {"kind": "comment", "ticket_id": 1, "comments": "x"},
            "privatecanary": "privatevalue",
        })
    assert "privatecanary" not in str(error.value)
    assert "privatevalue" not in str(error.value)
    assert service.actions == []


def test_prepare_step_up_challenge_and_safe_fixed_errors(tools_server):
    server, service = tools_server
    service.failure = WriteAuthorizationRequired("secret grant detail")
    result = run(server, "prepare_ticket_comment", {
        "action": {"kind": "comment", "ticket_id": 1, "comments": "x"},
    })
    assert isinstance(result, CallToolResult) and result.isError
    assert result.meta == {"mcp/www_authenticate": [
        'Bearer error="insufficient_scope", error_description="Additional authorization is required for ticket write review.", '
        'scope="tdx.read tdx.write", '
        'resource_metadata="https://connector.example/.well-known/oauth-protected-resource/mcp"'
    ]}
    assert "secret" not in json.dumps(result.model_dump(mode="json"))

    service.failure = WritesDisabled("secret flag detail")
    disabled = run(server, "prepare_ticket_comment", {
        "action": {"kind": "comment", "ticket_id": 1, "comments": "x"},
    })
    assert disabled.isError and disabled.meta is None
    assert "disabled" in disabled.content[0].text.lower()
    assert "secret" not in disabled.content[0].text

    service.failure = TicketPreparationRejected("secret upstream detail")
    rejected = run(server, "prepare_ticket_comment", {
        "action": {"kind": "comment", "ticket_id": 1, "comments": "x"},
    })
    assert rejected.isError and "secret" not in rejected.content[0].text


def test_malformed_service_outputs_are_safely_collapsed(tools_server):
    server, service = tools_server
    service.malformed = True
    prepared = run(server, "prepare_ticket_comment", {
        "action": {"kind": "comment", "ticket_id": 1, "comments": "x"},
    })
    assert prepared.isError
    assert "privatevalue" not in json.dumps(prepared.model_dump(mode="json"))
    result = run(server, "ticket_write_result", {"operation_id": "a" * 32})
    assert result.isError
    assert "privatevalue" not in json.dumps(result.model_dump(mode="json"))


def test_hosted_write_service_runs_off_event_loop_in_bounded_worker(tools_server):
    server, service = tools_server
    caller_thread = threading.get_ident()
    run(server, "prepare_ticket_comment", {
        "action": {"kind": "comment", "ticket_id": 1, "comments": "x"},
    })
    assert service.thread_ids == [service.thread_ids[0]]
    assert service.thread_ids[0] != caller_thread


def test_real_http_tools_list_mirrors_hosted_security_schemes_at_top_level():
    from starlette.testclient import TestClient
    from dynamix_manager.hosted import HostedFastMCP

    service = FakeService()
    server = create_server(
        "/unused",
        connection_provider=lambda: FakeConnection(),
        write_service=service,
        fastmcp_class=HostedFastMCP,
        stateless_http=True,
        json_response=True,
    )
    for tool in server._tool_manager.list_tools():
        if "securitySchemes" not in (tool.meta or {}):
            tool.meta = {**(tool.meta or {}), "securitySchemes": [
                {"type": "oauth2", "scopes": ["tdx.read"]},
            ]}
    app = server.streamable_http_app()
    with TestClient(app, base_url="http://127.0.0.1:8000") as http:
        response = http.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
        }, headers={"Accept": "application/json, text/event-stream"})
    tools = response.json()["result"]["tools"]
    assert len(tools) == 14
    for tool in tools:
        assert tool["securitySchemes"] == tool["_meta"]["securitySchemes"]


def test_real_http_call_serializes_success_challenge_and_redacted_validation_error():
    from starlette.testclient import TestClient
    from dynamix_manager.hosted import HostedFastMCP

    service = FakeService()
    server = create_server(
        "/unused",
        connection_provider=lambda: FakeConnection(),
        write_service=service,
        fastmcp_class=HostedFastMCP,
        stateless_http=True,
        json_response=True,
    )
    app = server.streamable_http_app()

    def body(arguments):
        return {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "prepare_ticket_comment", "arguments": arguments},
        }

    headers = {"Accept": "application/json, text/event-stream"}
    with TestClient(app, base_url="http://127.0.0.1:8000") as http:
        success = http.post("/mcp", json=body({
            "action": {"kind": "comment", "ticket_id": 1, "comments": "x"},
        }), headers=headers).json()["result"]
        assert success["structuredContent"]["not_saved"] is True
        assert success["structuredContent"]["message"].startswith("NOT SAVED")

        service.failure = WriteAuthorizationRequired("private grant detail")
        challenge = http.post("/mcp", json=body({
            "action": {"kind": "comment", "ticket_id": 1, "comments": "x"},
        }), headers=headers).json()["result"]
        assert challenge["isError"] is True
        authenticate = challenge["_meta"]["mcp/www_authenticate"][0]
        assert 'error="insufficient_scope"' in authenticate
        assert 'error_description=' in authenticate
        assert "private" not in json.dumps(challenge)

        service.failure = None
        invalid = http.post("/mcp", json=body({
            "action": {
                "kind": "comment", "ticket_id": 1, "comments": "x",
                "privatecanary": "privatevalue",
            },
        }), headers=headers).json()["result"]
        assert invalid["isError"] is True
        assert "Invalid tool arguments" in invalid["content"][0]["text"]
        assert "privatecanary" not in json.dumps(invalid)
        assert "privatevalue" not in json.dumps(invalid)


def test_real_http_rejects_json_encoded_action_string_without_calling_service():
    from starlette.testclient import TestClient
    from dynamix_manager.hosted import HostedFastMCP

    service = FakeService()
    server = create_server(
        "/unused",
        connection_provider=lambda: FakeConnection(),
        write_service=service,
        fastmcp_class=HostedFastMCP,
        stateless_http=True,
        json_response=True,
    )
    app = server.streamable_http_app()
    request = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "name": "prepare_ticket_comment",
            "arguments": {"action": json.dumps({
                "kind": "comment", "ticket_id": 1, "comments": "x",
            })},
        },
    }
    with TestClient(app, base_url="http://127.0.0.1:8000") as http:
        result = http.post(
            "/mcp", json=request,
            headers={"Accept": "application/json, text/event-stream"},
        ).json()["result"]
    assert result["isError"] is True
    assert "Invalid tool arguments" in result["content"][0]["text"]
    assert service.actions == []


def test_real_http_preserves_literal_null_metadata_search(monkeypatch):
    from starlette.testclient import TestClient
    from dynamix_manager.hosted import HostedFastMCP

    captured = []

    class Adapter:
        def discover_metadata(self, kind, *, search, limit):
            captured.append((kind, search, limit))
            return {"kind": kind, "results": [], "returned": 0, "complete": False}

    monkeypatch.setattr(
        "dynamix_manager.ticket_writes.tools.WriteAdapter.from_connection",
        classmethod(lambda cls, connection: Adapter()),
    )
    server = create_server(
        "/unused",
        connection_provider=lambda: FakeConnection(),
        write_service=FakeService(),
        fastmcp_class=HostedFastMCP,
        stateless_http=True,
        json_response=True,
    )
    app = server.streamable_http_app()
    request = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "name": "ticket_write_metadata",
            "arguments": {"kind": "people", "search": "null", "limit": 1},
        },
    }
    with TestClient(app, base_url="http://127.0.0.1:8000") as http:
        result = http.post(
            "/mcp", json=request,
            headers={"Accept": "application/json, text/event-stream"},
        ).json()["result"]
    assert result["isError"] is False
    assert captured == [("people", "null", 1)]


def test_metadata_tool_uses_read_scope_and_bounded_adapter(monkeypatch, tools_server):
    server, _ = tools_server

    def request(method, url, **kwargs):
        assert method == "GET"
        assert url == "https://tenant.example/TDWebApi/api/42/tickets/statuses"
        return SimpleNamespace(status_code=200, json=lambda: [
            {"ID": 5, "Name": "Done", "IsActive": True,
             "StatusClass": 3, "RequireGoesOffHold": False},
        ])

    monkeypatch.setattr("dynamix_manager.ticket_writes.adapter.requests.request", request)
    result = structured(run(server, "ticket_write_metadata", {
        "kind": "statuses", "search": None, "limit": 1,
    }))
    assert result == {
        "kind": "statuses",
        "results": [{"ID": 5, "Name": "Done", "StatusClass": 3,
                     "RequireGoesOffHold": False}],
        "returned": 1,
        "complete": False,
    }
    invalid = run(server, "ticket_write_metadata", {
        "kind": "people", "search": None, "limit": 10,
    })
    assert invalid.isError
    assert "unavailable" in invalid.content[0].text.lower()
