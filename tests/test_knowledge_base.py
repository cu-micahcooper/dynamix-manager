import asyncio
import json
from unittest.mock import Mock

import pytest

from dynamix_manager.plugin import Connection, create_server

VALUES = {"TDX_BASE_URL": "https://example.test/TDWebApi", "TDX_APP_ID": "2045",
          "WORKBENCH_PERSONAL_TOKEN": "private-token"}
APPS = [
    {"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"},
    {"AppID": 928, "Name": "InfoTech Assets/CIs", "AppClass": "TDAssets"},
    {"AppID": 2045, "Name": "Client Portal", "AppClass": "TDClient"},
]


def connection(apps=None):
    client = Mock()
    client.fetch_applications.return_value = apps if apps is not None else [dict(a) for a in APPS]
    client.list_ticketing_applications.side_effect = lambda apps: [a for a in apps if a.get("AppClass") == "TDTickets"]
    c = Connection(values=VALUES, client=client)
    c.routes, c.calls = {}, []

    def request(method, url, **kwargs):
        c.calls.append((method, url.removeprefix(c.base_url), kwargs))
        value = c.routes[(method, url.removeprefix(c.base_url))]
        if isinstance(value, Exception):
            raise value
        return Mock(json=lambda: json.loads(json.dumps(value)))

    client.session.get.side_effect = lambda url, **kw: request("GET", url, **kw)
    client.session.post.side_effect = lambda url, **kw: request("POST", url, **kw)
    return c


def server_for(c):
    return create_server(connection_provider=lambda: c)


def call(server, name, arguments=None):
    result = asyncio.run(server.call_tool(name, arguments or {}))
    return result[1] if isinstance(result, tuple) else result


def test_portal_application_is_discovered_by_class_and_reported():
    c = connection()
    assert c.portal_app_id == 2045 and c.portal_application["Name"] == "Client Portal"
    assert c.article_url(95821) == "https://example.test/TDClient/2045/Portal/KB/ArticleDet?ID=95821"
    assert c.category_url(9208) == "https://example.test/TDClient/2045/Portal/KB/?CategoryID=9208"
    status = call(server_for(c), "connection_status")
    assert status["portal_app_id"] == 2045 and status["portal_app_name"] == "Client Portal"


def test_missing_portal_application_fails_closed_but_status_still_answers():
    c = connection(apps=[dict(APPS[0]), dict(APPS[1])])
    with pytest.raises(RuntimeError, match="portal"):
        c.portal_app_id
    status = call(server_for(c), "connection_status")
    assert status["portal_app_id"] is None and "portal" in status["portal_warning"]
