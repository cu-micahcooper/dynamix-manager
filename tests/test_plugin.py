import asyncio
import json
from unittest.mock import Mock

import pytest
import requests

import dynamix_manager.plugin as plugin
from dynamix_manager.plugin import APP_URI, Connection, TenantSession, create_server, display_text


VALUES = {"TDX_BASE_URL": "https://example.test/TDWebApi", "TDX_APP_ID": "2045",
          "WORKBENCH_PERSONAL_TOKEN": "private-token"}
LOGIN_VALUES = {"TDX_BASE_URL": "https://example.test/TDWebApi", "TDX_APP_ID": "2045",
                "WORKBENCH_PERSONAL_USERNAME": "user", "WORKBENCH_PERSONAL_PASSWORD": "private-password"}


def connection(values=None):
    client = Mock()
    client.authenticate.return_value = "login-token"
    client.fetch_applications.return_value = [{"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"}]
    client.list_ticketing_applications.side_effect = lambda apps: apps
    return Connection(values=values or VALUES, client=client)


def server_for(c, **kwargs):
    return create_server(connection_provider=lambda: c, **kwargs)


def call(server, name, arguments=None):
    result = asyncio.run(server.call_tool(name, arguments or {}))
    return result[1] if isinstance(result, tuple) else result


def test_connection_uses_supplied_token_and_discovers_ticket_app_separately():
    c = connection()
    server = server_for(c)
    assert call(server, "connection_status")["ticket_app_id"] == 634
    assert call(server, "connection_status")["connected"]
    assert c.header_app_id == "2045"
    c.client.authenticate.assert_not_called()
    assert "private" not in json.dumps(call(server, "connection_status"))


def test_ready_never_authenticates_without_a_personal_token():
    c = connection(LOGIN_VALUES)
    assert c.auth_mode == "user"
    with pytest.raises(RuntimeError, match="token"):
        c.ready()
    c.client.authenticate.assert_not_called()
    c.client.fetch_applications.assert_not_called()


def test_application_discovery_is_cached_per_tenant_until_ttl(monkeypatch):
    first, second = connection(), connection()
    assert first.app_id == 634
    assert second.app_id == 634
    first.client.fetch_applications.assert_called_once()
    second.client.fetch_applications.assert_not_called()

    other = connection({**VALUES, "TDX_BASE_URL": "https://other.test/TDWebApi"})
    assert other.app_id == 634
    other.client.fetch_applications.assert_called_once()

    monkeypatch.setattr(plugin.time, "monotonic", lambda: 10 ** 9)
    stale = connection()
    assert stale.app_id == 634
    stale.client.fetch_applications.assert_called_once()


def test_capability_provider_controls_read_only_status():
    local = server_for(connection())
    assert local.instructions.startswith("Read-only")
    assert call(local, "connection_status")["read_only"] is True

    hosted = server_for(
        connection(),
        instructions="Hosted personal connector.",
        capability_provider=lambda: {"read_only": False, "write_available": True},
    )
    assert hosted.instructions == "Hosted personal connector."
    status = call(hosted, "connection_status")
    assert status["read_only"] is False
    assert status["write_available"] is True


def test_server_requires_a_per_request_connection_provider():
    with pytest.raises(TypeError):
        create_server()


@pytest.mark.parametrize("url", ["http://example.test/TDWebApi", "https://user:secret@example.test/TDWebApi",
                               "https://example.test/TDWebApi?token=secret", "https://example.test/other"])
def test_rejects_unsafe_tenant_config(url):
    with pytest.raises(ValueError):
        connection({**VALUES, "TDX_BASE_URL": url})


def test_missing_credentials_are_rejected_at_construction():
    with pytest.raises(ValueError, match="credentials"):
        connection({"TDX_BASE_URL": VALUES["TDX_BASE_URL"], "TDX_APP_ID": "2045"})


def test_admin_my_queue_does_not_impersonate_a_user():
    c = connection({**LOGIN_VALUES, "WORKBENCH_PERSONAL_USERNAME": "00000000-0000-0000-0000-000000000001",
                    "WORKBENCH_PERSONAL_PASSWORD": "00000000-0000-0000-0000-000000000002"})
    c.token = "admin-token"
    with pytest.raises(Exception, match="personal login"):
        call(server_for(c), "my_queue")
    c.client.search_tickets.assert_not_called()


def test_search_limits_results_and_marks_incomplete():
    c = connection()
    c.client.search_tickets.return_value = [{"ID": i, "Title": "Test"} for i in range(5)]
    result = call(server_for(c), "search_tickets", {"query": "wifi", "limit": 2, "status_ids": [1]})
    assert len(result["tickets"]) == 2
    assert result["complete"] is False
    assert "/Apps/634/" in result["tickets"][0]["url"]
    c.client.search_tickets.assert_called_once_with(
        "private-token", {"SearchText": "wifi", "MaxResults": 2, "StatusIDs": [1]}, 634, max_attempts=1)


@pytest.mark.parametrize("arguments", [{"limit": 0}, {"limit": 101}, {"status_ids": [-1]}, {"query": "x" * 501}])
def test_mcp_rejects_invalid_search_arguments_before_network(arguments):
    c = connection()
    with pytest.raises(Exception):
        call(server_for(c), "search_tickets", arguments)
    c.client.fetch_applications.assert_not_called()


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


def test_read_tools_and_ui_resource_without_credentials():
    server = create_server(connection_provider=lambda: None)

    async def check():
        tools = await server.list_tools()
        assert len(tools) == 8
        assert all(t.annotations.readOnlyHint for t in tools)
        search = next(t for t in tools if t.name == "search_tickets")
        assert search.meta["ui"]["resourceUri"] == APP_URI
        resource = await server.read_resource(APP_URI)
        assert resource[0].mime_type == "text/html;profile=mcp-app"
        assert "TeamDynamix" in resource[0].content
    asyncio.run(check())


def test_detail_and_activity_provide_readable_text_and_ticket_identity():
    c = connection()
    c.client.get_ticket.return_value = {"ID": 42, "Description": "<p>Hello &amp; welcome</p><script>bad()</script>"}
    c.client.get_ticket_feed.return_value = [{"Body": "<p>Reply</p>", "IsPrivate": True}]
    server = server_for(c)
    assert call(server, "get_ticket", {"ticket_id": 42})["description_text"] == "Hello & welcome"
    feed = call(server, "ticket_feed", {"ticket_id": 42})
    assert feed["ticket_id"] == 42
    assert feed["items"][0]["body_text"] == "Reply"
    assert feed["items"][0]["IsPrivate"] is True
    assert not feed["complete"]


def test_rich_text_preserves_paragraphs_without_active_content():
    assert display_text('<p>First</p><p>Second<br>Third</p><style>bad</style>') == "First\nSecond\nThird"
