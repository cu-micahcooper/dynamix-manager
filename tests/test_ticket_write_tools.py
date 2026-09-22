import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult

from dynamix_manager.plugin import create_server
from dynamix_manager.ticket_writes.service import (
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

    def submit(self, principal, action, request_id):
        self.thread_ids.append(threading.get_ident())
        if self.failure:
            raise self.failure
        self.actions.append(action)
        assert request_id == "request-1"
        created = action.kind == "create"
        return TicketWriteStatus(
            operation_id="privatevalue" if self.malformed else "a" * 32,
            outcome="applied", message="TeamDynamix accepted the change.",
            ticket_id=5555 if created else action.ticket_id,
            detail={"ticket_id": 5555, "status": "New"} if created else None,
            ticket_url="https://tenant.example/TDNext/Apps/42/Tickets/TicketDet.aspx?TicketID=1001",
            status_code=201,
        )

    def result(self, principal, operation_id):
        if self.failure:
            raise self.failure
        return TicketWriteStatus(
            operation_id="privatevalue" if self.malformed else operation_id,
            outcome="pending",
            message="The change was recorded but not yet sent to TeamDynamix.",
            ticket_id=1001,
            ticket_url="https://tenant.example/TDNext/Apps/42/Tickets/TicketDet.aspx?TicketID=1001",
        )


class FakeConnection:
    people = {}
    base_url = "https://tenant.example/TDWebApi"
    app_id = 42
    header_app_id = "8"
    token = "private-token"
    auth_mode = "user"
    application = {"Name": "InfoTech Tickets"}
    client = SimpleNamespace(session=SimpleNamespace(close=lambda: None))

    def ready(self):
        return self

    def resolve_person(self, role, search):
        matched = self.people.get(search, [])
        chosen = matched if len(matched) == 1 else []
        return {"role": role, "search": search, "matched": matched}, [m["uid"] for m in chosen]


def run(server, name, arguments):
    if name in {"add_ticket_comment", "update_ticket_status", "assign_ticket", "edit_ticket",
                "complete_ticket_task", "create_ticket"} and isinstance(arguments, dict):
        arguments = {"request_id": "request-1", **arguments}
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
        connection_provider=lambda: connection,
        write_service=service,
    )
    return server, service


def test_registers_only_four_direct_tools_and_two_bounded_read_tools(tools_server):
    server, _ = tools_server
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}
    assert set(tools) == {
        "connection_status", "ticket_statuses", "search_tickets", "my_queue",
        "get_ticket", "ticket_feed", "survey_report", "days_off", "show_tickets",
        "search_assets", "get_asset", "asset_feed", "ticket_assets", "asset_tickets", "asset_metadata",
        "add_ticket_comment", "update_ticket_status",
        "assign_ticket", "edit_ticket", "complete_ticket_task", "create_ticket",
        "ticket_write_metadata", "ticket_write_result", "list_ticket_tasks", "ticket_create_metadata",
    }
    for name in (
        "add_ticket_comment", "update_ticket_status",
        "assign_ticket", "edit_ticket", "complete_ticket_task", "create_ticket",
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
    assert tools["list_ticket_tasks"].annotations.readOnlyHint is True
    assert tools["list_ticket_tasks"].meta["securitySchemes"][0]["scopes"] == ["tdx.read"]
    assert tools["list_ticket_tasks"].parameters["additionalProperties"] is False
    assert tools["ticket_create_metadata"].annotations.readOnlyHint is True
    assert tools["ticket_create_metadata"].meta["securitySchemes"][0]["scopes"] == ["tdx.read"]
    assert not any(name.startswith(("prepare_", "commit_", "reconcile_")) for name in tools)


@pytest.mark.parametrize(
    ("name", "action"),
    [
        ("add_ticket_comment", {"kind": "comment", "ticket_id": 1001, "comments": "Hello"}),
        ("update_ticket_status", {"kind": "status", "ticket_id": 1001, "comments": "Done", "status_id": 5}),
        ("assign_ticket", {"kind": "assign", "ticket_id": 1001, "responsible_group_id": 4}),
        ("edit_ticket", {"kind": "edit", "ticket_id": 1001, "description": None}),
    ],
)
def test_direct_tools_use_strict_typed_actions_and_return_result(name, action, tools_server):
    server, service = tools_server
    result = structured(run(server, name, {"action": action}))
    assert result == {
        "outcome": "applied",
        "message": "TeamDynamix accepted the change.",
        "operation_id": "a" * 32,
        "detail": None,
        "ticket_id": action["ticket_id"],
        "ticket_url": "https://tenant.example/TDNext/Apps/42/Tickets/TicketDet.aspx?TicketID=1001",
        "status_code": 201,
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
        "message": "The change was recorded but not yet sent to TeamDynamix.",
        "ticket_id": 1001,
        "ticket_url": "https://tenant.example/TDNext/Apps/42/Tickets/TicketDet.aspx?TicketID=1001",
        "status_code": None,
        "detail": None,
    }


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("add_ticket_comment", {"action": {"ticket_id": 1, "comments": "missing kind"}}),
        ("add_ticket_comment", {"action": {"kind": "comment", "ticket_id": 1, "comments": "x", "privatecanary": "x"}}),
        ("add_ticket_comment", {"action": {"kind": "comment", "ticket_id": 1, "comments": "x", "notify": ["privatevalue"]}}),
        ("assign_ticket", {"action": {"kind": "assign", "ticket_id": 1, "responsible_uid": None}}),
        ("edit_ticket", {"action": {"kind": "edit", "ticket_id": 1}}),
        ("ticket_write_result", {"operation_id": "A" * 32}),
        ("ticket_write_result", {"operation_id": "a" * 31}),
        ("add_ticket_comment", ["privatevalue"]),
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
        run(server, "add_ticket_comment", {
            "action": {"kind": "comment", "ticket_id": 1, "comments": "x"},
            "privatecanary": "privatevalue",
        })
    assert "privatecanary" not in str(error.value)
    assert "privatevalue" not in str(error.value)
    assert service.actions == []


@pytest.mark.parametrize("request_id", [None, "", " ", "private canary", "a" * 201, 12])
def test_direct_tools_reject_invalid_request_id(tools_server, request_id):
    server, service = tools_server
    with pytest.raises(Exception, match="Invalid tool arguments"):
        run(server, "add_ticket_comment", {
            "action": {"kind": "comment", "ticket_id": 1, "comments": "x"},
            "request_id": request_id,
        })
    assert service.actions == []


def test_direct_tools_require_request_id_and_old_prepare_names_are_absent(tools_server):
    server, service = tools_server
    with pytest.raises(Exception, match="Invalid tool arguments"):
        asyncio.run(server.call_tool("add_ticket_comment", {
            "action": {"kind": "comment", "ticket_id": 1, "comments": "x"},
        }))
    assert service.actions == []
    assert not any(t.name.startswith("prepare_ticket_") for t in server._tool_manager.list_tools())


def test_prepare_step_up_challenge_and_safe_fixed_errors(tools_server):
    server, service = tools_server
    service.failure = WriteAuthorizationRequired("secret grant detail")
    result = run(server, "add_ticket_comment", {
        "action": {"kind": "comment", "ticket_id": 1, "comments": "x"},
    })
    assert isinstance(result, CallToolResult) and result.isError
    assert result.meta == {"mcp/www_authenticate": [
        'Bearer error="insufficient_scope", error_description="Additional authorization is required for ticket writes.", '
        'scope="tdx.read tdx.write", '
        'resource_metadata="https://connector.example/.well-known/oauth-protected-resource/mcp"'
    ]}
    assert "secret" not in json.dumps(result.model_dump(mode="json"))

    service.failure = WritesDisabled("secret flag detail")
    disabled = run(server, "add_ticket_comment", {
        "action": {"kind": "comment", "ticket_id": 1, "comments": "x"},
    })
    assert disabled.isError and disabled.meta is None
    assert "disabled" in disabled.content[0].text.lower()
    assert "secret" not in disabled.content[0].text

    service.failure = TicketPreparationRejected("secret upstream detail")
    rejected = run(server, "add_ticket_comment", {
        "action": {"kind": "comment", "ticket_id": 1, "comments": "x"},
    })
    assert rejected.isError and "secret" not in rejected.content[0].text


def test_malformed_service_outputs_are_safely_collapsed(tools_server):
    server, service = tools_server
    service.malformed = True
    prepared = run(server, "add_ticket_comment", {
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
    run(server, "add_ticket_comment", {
        "action": {"kind": "comment", "ticket_id": 1, "comments": "x"},
    })
    assert service.thread_ids == [service.thread_ids[0]]
    assert service.thread_ids[0] != caller_thread


def test_real_http_tools_list_mirrors_hosted_security_schemes_at_top_level():
    from starlette.testclient import TestClient
    from dynamix_manager.hosted import HostedFastMCP

    service = FakeService()
    server = create_server(
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
    assert len(tools) == 25
    for tool in tools:
        assert tool["securitySchemes"] == tool["_meta"]["securitySchemes"]


def test_real_http_call_serializes_success_challenge_and_redacted_validation_error():
    from starlette.testclient import TestClient
    from dynamix_manager.hosted import HostedFastMCP

    service = FakeService()
    server = create_server(
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
            "params": {"name": "add_ticket_comment", "arguments": {"request_id": "request-1", **arguments}},
        }

    headers = {"Accept": "application/json, text/event-stream"}
    with TestClient(app, base_url="http://127.0.0.1:8000") as http:
        success = http.post("/mcp", json=body({
            "action": {"kind": "comment", "ticket_id": 1, "comments": "x"},
        }), headers=headers).json()["result"]
        assert success["structuredContent"]["outcome"] == "applied"
        assert "review_url" not in success["structuredContent"]

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
            "name": "add_ticket_comment",
            "arguments": {"request_id": "request-1", "action": json.dumps({
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


def test_request_id_reuse_and_unresolved_equivalent_get_specific_guidance(tools_server):
    from dynamix_manager.ticket_writes.store import EquivalentWriteBlocked, WriteBindingError

    server, service = tools_server
    service.failure = WriteBindingError("The request ID was already used with different arguments.")
    reused = run(server, "add_ticket_comment", {"action": {"kind": "comment", "ticket_id": 1, "comments": "x"}})
    assert reused.isError and reused.meta is None
    text = reused.content[0].text.lower()
    assert "already used" in text and "new request id" in text
    assert "do not create a new request id" not in text

    service.failure = EquivalentWriteBlocked("An equivalent write has an unresolved outcome.")
    blocked = run(server, "add_ticket_comment", {"action": {"kind": "comment", "ticket_id": 1, "comments": "x"}})
    assert blocked.isError and "unresolved" in blocked.content[0].text.lower()
    assert "ticket_write_result" in blocked.content[0].text


def test_complete_ticket_task_tool_submits_task_action(tools_server):
    from dynamix_manager.ticket_writes.models import TaskAction

    server, service = tools_server
    result = structured(run(server, "complete_ticket_task", {"action": {"kind": "task", "ticket_id": 1001, "task_id": 77}}))
    assert result["outcome"] == "applied" and result["ticket_id"] == 1001
    assert isinstance(service.actions[-1], TaskAction) and service.actions[-1].task_id == 77
    with pytest.raises(Exception):
        run(server, "complete_ticket_task", {"action": {"kind": "comment", "ticket_id": 1001, "comments": "x"}})


def test_list_ticket_tasks_uses_bounded_adapter_and_collapses_failures(monkeypatch, tools_server):
    server, _ = tools_server
    seen = []

    class FakeAdapter:
        def list_tasks(self, ticket_id, *, limit=25):
            seen.append((ticket_id, limit))
            if ticket_id == 9:
                raise RuntimeError("private upstream body")
            return {"ticket_id": ticket_id, "tasks": [{"ID": 77, "Title": "Approve", "IsActive": True,
                    "PercentComplete": 0, "CompletedDate": None, "ResponsibleFullName": None,
                    "ResponsibleGroupName": None, "TypeID": 1}], "returned": 1, "complete": True}

    monkeypatch.setattr("dynamix_manager.ticket_writes.tools.WriteAdapter.from_connection",
                        staticmethod(lambda connection: FakeAdapter()))
    result = structured(run(server, "list_ticket_tasks", {"ticket_id": 1001, "limit": 5}))
    assert result["tasks"][0]["ID"] == 77 and result["complete"] is True
    assert seen == [(1001, 5)]
    failure = run(server, "list_ticket_tasks", {"ticket_id": 9})
    assert failure.isError and "private" not in failure.content[0].text
    with pytest.raises(Exception):
        run(server, "list_ticket_tasks", {"ticket_id": 1001, "limit": 0})


ALAN = {"uid": "aaaaaaaa-0000-4000-8000-000000000001", "name": "Alan McCain", "email": "mccaina@cedarville.edu"}
MICAH = {"uid": "aaaaaaaa-0000-4000-8000-000000000002", "name": "Micah Cooper", "email": "micahcooper@cedarville.edu"}


def test_create_ticket_resolves_people_first_and_reports_them_with_the_new_ticket(tools_server):
    from dynamix_manager.ticket_writes.models import CreateAction

    server, service = tools_server
    FakeConnection.people = {"Alan McCain": [ALAN], "Micah Cooper": [MICAH]}
    result = structured(run(server, "create_ticket", {"ticket": {
        "title": "Replace projector", "description": "Room 101", "type_id": 3, "account_id": 11,
        "requestor": "Alan McCain", "responsible": "Micah Cooper", "priority_id": 7}}))
    action = service.actions[-1]
    assert isinstance(action, CreateAction)
    assert str(action.requestor_uid) == ALAN["uid"] and str(action.responsible_uid) == MICAH["uid"]
    assert action.priority_id == 7 and action.form_id is None and "form_id" not in action.model_fields_set
    assert result["ticket_id"] == 5555 and result["outcome"] == "applied"
    assert result["detail"] == {"ticket_id": 5555, "status": "New"}
    assert result["resolved_people"] == [
        {"role": "requestor", "search": "Alan McCain", "matched": [ALAN]},
        {"role": "responsible", "search": "Micah Cooper", "matched": [MICAH]},
    ]


def test_create_ticket_accepts_uids_directly_and_stops_on_ambiguous_people(tools_server):
    server, service = tools_server
    FakeConnection.people = {"McCain": [ALAN, {**MICAH, "name": "Other McCain"}]}
    count = len(service.actions)
    result = structured(run(server, "create_ticket", {"ticket": {
        "title": "T", "type_id": 3, "account_id": 11, "requestor_uid": ALAN["uid"]}}))
    assert str(service.actions[-1].requestor_uid) == ALAN["uid"] and len(service.actions) == count + 1
    assert result["resolved_people"] == []

    ambiguous = run(server, "create_ticket", {"ticket": {"title": "T", "type_id": 3, "account_id": 11, "requestor": "McCain"}})
    assert ambiguous.isError and "ambiguous" in ambiguous.content[0].text.lower()
    assert "mccaina@cedarville.edu" in ambiguous.content[0].text
    assert len(service.actions) == count + 1

    nobody = run(server, "create_ticket", {"ticket": {"title": "T", "type_id": 3, "account_id": 11, "requestor": "Nobody"}})
    assert nobody.isError and "no person" in nobody.content[0].text.lower()
    with pytest.raises(Exception):
        run(server, "create_ticket", {"ticket": {"title": "T", "type_id": 3, "account_id": 11}})
    with pytest.raises(Exception):
        run(server, "create_ticket", {"ticket": {"title": "T", "type_id": 3, "account_id": 11,
                                                  "requestor": "Alan McCain", "requestor_uid": ALAN["uid"]}})


def test_ticket_create_metadata_uses_bounded_adapter(monkeypatch, tools_server):
    server, _ = tools_server
    seen = []

    class FakeAdapter:
        def discover_create_metadata(self, kind, *, search=None, limit=10):
            seen.append((kind, search, limit))
            return {"kind": kind, "results": [{"ID": 3, "Name": "Hardware", "CategoryName": "Support"}],
                    "returned": 1, "complete": True}

    monkeypatch.setattr("dynamix_manager.ticket_writes.tools.WriteAdapter.from_connection",
                        staticmethod(lambda connection: FakeAdapter()))
    result = structured(run(server, "ticket_create_metadata", {"kind": "types", "search": "hard", "limit": 5}))
    assert result["results"][0]["ID"] == 3 and seen == [("types", "hard", 5)]
    with pytest.raises(Exception):
        run(server, "ticket_create_metadata", {"kind": "people"})
