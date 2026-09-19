import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from dynamix_manager.plugin import APP_URI, Connection, TenantSession, create_server, display_text


VALUES = {"TDX_BASE_URL": "https://example.test/TDWebApi", "TDX_APP_ID": "2045",
          "TDX_USERNAME": "user", "TDX_PASSWORD": "private-password"}


def connection(values=None):
    client = Mock()
    client.authenticate.return_value = "private-token"
    client.fetch_applications.return_value = [{"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"}]
    client.list_ticketing_applications.side_effect = lambda apps: apps
    return Connection("/unused", values=values or VALUES, client=client)


def call(server, name, arguments=None):
    result = asyncio.run(server.call_tool(name, arguments or {}))
    return result[1] if isinstance(result, tuple) else result


def test_connection_discovers_ticket_app_separately_and_authenticates_once():
    c = connection()
    server = create_server("/unused", c)
    assert call(server, "connection_status")["ticket_app_id"] == 634
    assert call(server, "connection_status")["connected"]
    assert c.header_app_id == "2045"
    c.client.authenticate.assert_called_once()
    assert "private" not in json.dumps(call(server, "connection_status"))


def test_explicit_hosted_instructions_and_capability_status_do_not_change_local_defaults():
    local = create_server("/unused", connection())
    assert local.instructions.startswith("Read-only")
    assert call(local, "connection_status")["read_only"] is True

    hosted = create_server(
        "/unused",
        connection(),
        instructions="Hosted personal connector. Preparation is not a save.",
        capability_provider=lambda: {"read_only": False, "write_available": True},
    )
    assert hosted.instructions == "Hosted personal connector. Preparation is not a save."
    status = call(hosted, "connection_status")
    assert status["read_only"] is False
    assert status["write_available"] is True


@pytest.mark.parametrize("url", ["http://example.test/TDWebApi", "https://user:secret@example.test/TDWebApi",
                               "https://example.test/TDWebApi?token=secret", "https://example.test/other"])
def test_rejects_unsafe_tenant_config(url):
    with pytest.raises(ValueError):
        connection({**VALUES, "TDX_BASE_URL": url})


def test_personal_token_takes_precedence():
    c = connection({**VALUES, "WORKBENCH_PERSONAL_TOKEN": "personal-token"})
    c.ready()
    c.client.authenticate.assert_not_called()
    assert c.token == "personal-token"
    assert c.auth_mode == "user"


def test_admin_my_queue_does_not_impersonate_a_user():
    c = connection({**VALUES, "TDX_USERNAME": "00000000-0000-0000-0000-000000000001",
                    "TDX_PASSWORD": "00000000-0000-0000-0000-000000000002"})
    with pytest.raises(Exception, match="personal login"):
        call(create_server("/unused", c), "my_queue")
    c.client.search_tickets.assert_not_called()


def test_search_limits_results_and_marks_incomplete():
    c = connection()
    c.client.search_tickets.return_value = [{"ID": i, "Title": "Test"} for i in range(5)]
    result = call(create_server("/unused", c), "search_tickets", {"query": "wifi", "limit": 2, "status_ids": [1]})
    assert len(result["tickets"]) == 2
    assert result["complete"] is False
    assert "/Apps/634/" in result["tickets"][0]["url"]
    c.client.search_tickets.assert_called_once_with(
        "private-token", {"SearchText": "wifi", "MaxResults": 2, "StatusIDs": [1]}, 634, max_attempts=1)


@pytest.mark.parametrize("arguments", [{"limit": 0}, {"limit": 101}, {"status_ids": [-1]}, {"query": "x" * 501}])
def test_mcp_rejects_invalid_search_arguments_before_network(arguments):
    c = connection()
    with pytest.raises(Exception):
        call(create_server("/unused", c), "search_tickets", arguments)
    c.client.authenticate.assert_not_called()


def test_ambiguous_app_discovery_fails_closed():
    c = connection()
    c.client.fetch_applications.return_value *= 2
    with pytest.raises(RuntimeError, match="uniquely"):
        c.ready()


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
def test_http_failures_are_sanitized_and_redirects_disabled(monkeypatch, status):
    def request(self, method, url, **kwargs):
        assert kwargs["allow_redirects"] is False
        response = requests.Response()
        response.status_code = status
        response._content = b"private-password private-token"
        return response
    monkeypatch.setattr(requests.Session, "request", request)
    with pytest.raises(RuntimeError) as error:
        TenantSession(VALUES["TDX_BASE_URL"]).get(VALUES["TDX_BASE_URL"] + "/api/applications")
    assert "private" not in str(error.value)


def test_authenticated_requests_cannot_leave_tenant():
    with pytest.raises(RuntimeError, match="outside"):
        TenantSession(VALUES["TDX_BASE_URL"]).get("https://other.test/api/applications")


def test_full_stdio_protocol_and_ui_resource(tmp_path):
    async def check():
        # Empty project proves startup, discovery and UI resources do not need credentials.
        params = StdioServerParameters(command=sys.executable, args=["-m", "dynamix_manager.plugin",
                                                                   "--project-root", str(tmp_path)])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                assert len(tools) == 8
                assert all(t.annotations.readOnlyHint for t in tools)
                search = next(t for t in tools if t.name == "search_tickets")
                assert search.meta["ui"]["resourceUri"] == APP_URI
                resource = await session.read_resource(APP_URI)
                assert resource.contents[0].mimeType == "text/html;profile=mcp-app"
                assert "TeamDynamix" in resource.contents[0].text
                invalid = await session.call_tool("get_ticket", {"ticket_id": -1})
                assert invalid.isError
    asyncio.run(check())


def test_manifest_launcher_targets_real_runtime():
    manifest = json.loads(Path("plugins/teamdynamix/.mcp.json").read_text())
    target = manifest["mcpServers"]["teamdynamix"]
    assert Path(target["command"]).is_file()
    assert target["args"][1] == "dynamix_manager.plugin"


def test_detail_and_activity_provide_readable_text_and_ticket_identity():
    c = connection()
    c.client.get_ticket.return_value = {"ID": 42, "Description": "<p>Hello &amp; welcome</p><script>bad()</script>"}
    c.client.get_ticket_feed.return_value = [{"Body": "<p>Reply</p>", "IsPrivate": True}]
    server = create_server("/unused", c)
    assert call(server, "get_ticket", {"ticket_id": 42})["description_text"] == "Hello & welcome"
    feed = call(server, "ticket_feed", {"ticket_id": 42})
    assert feed["ticket_id"] == 42
    assert feed["items"][0]["body_text"] == "Reply"
    assert feed["items"][0]["IsPrivate"] is True
    assert not feed["complete"]


def test_rich_text_preserves_paragraphs_without_active_content():
    assert display_text('<p>First</p><p>Second<br>Third</p><style>bad</style>') == "First\nSecond\nThird"
