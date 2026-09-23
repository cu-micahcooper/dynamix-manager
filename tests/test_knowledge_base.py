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


ARTICLE = {"ID": 95821, "AppID": 2045, "Subject": "Mac password doesn't match", "Summary": "Sync your Mac login",
           "Body": "<p>Did you change your <b>CedarNet</b> password?</p><script>x()</script>" + "<p>" + "x" * 500 + "</p>",
           "CategoryID": 9208, "CategoryName": "Office Devices", "Tags": ["macos", "password"], "Status": 5,
           "StatusName": "Archived", "DraftStatus": None, "IsPublished": False, "IsPublic": True,
           "RevisionNumber": 4, "ReviewDateUtc": "2027-02-01T00:00:00Z", "OwnerUid": "aaaaaaaa-0000-4000-8000-000000000001",
           "OwnerFullName": "David Mortenson", "OwningGroupID": None, "OwningGroupName": None,
           "ModifiedDate": "2026-09-01T00:00:00Z", "Attachments": [{"ID": "att-1", "Name": "screen.png", "Size": 1200}],
           "Attributes": [{"Name": "Audience", "ValueText": "Staff"}]}
CATEGORIES = [
    {"ID": 9208, "Name": "Office Devices", "ParentID": 0, "ParentName": None, "IsPublic": True, "Order": 1.0, "Subcategories": []},
    {"ID": 9106, "Name": "Software", "ParentID": 0, "ParentName": None, "IsPublic": True, "Order": 2.0, "Subcategories": [
        {"ID": 9107, "Name": "Adobe", "ParentID": 9106, "ParentName": "Software", "IsPublic": False, "Order": 1.0, "Subcategories": []}]},
]


def test_search_articles_maps_filters_and_returns_text_snippets():
    c = connection()
    c.routes[("POST", "/api/2045/knowledgebase/search")] = [ARTICLE]
    result = call(server_for(c), "search_articles", {"text": "password", "status": "archived", "is_public": True,
                                                     "category_id": 9208, "limit": 5})
    method, path, kwargs = c.calls[-1]
    assert kwargs["json"] == {"ReturnCount": 5, "SearchText": "password", "Status": 5, "IsPublic": True, "CategoryID": 9208}
    row = result["articles"][0]
    assert row["ID"] == 95821 and row["StatusName"] == "Archived" and row["Tags"] == ["macos", "password"]
    assert row["body_text"].startswith("Did you change your CedarNet password?") and "x()" not in row["body_text"]
    assert row["body_truncated"] is True and len(row["body_text"]) == 400
    assert row["url"] == "https://example.test/TDClient/2045/Portal/KB/ArticleDet?ID=95821"
    assert "Body" not in row and result["complete"] is True and result["resolved_people"] == []


def test_search_articles_resolves_the_author_and_reports_it():
    c = connection()
    c.routes[("GET", "/api/people/lookup")] = [{"UID": "aaaaaaaa-0000-4000-8000-000000000001", "FullName": "David Mortenson",
                                                "PrimaryEmail": "mortensond@example.test", "IsActive": True}]
    c.routes[("POST", "/api/2045/knowledgebase/search")] = []
    result = call(server_for(c), "search_articles", {"author": "David Mortenson"})
    assert c.calls[-1][2]["json"]["AuthorUID"] == "aaaaaaaa-0000-4000-8000-000000000001"
    assert result["resolved_people"][0]["matched"][0]["email"] == "mortensond@example.test"
    c.routes[("GET", "/api/people/lookup")] = []
    result = call(server_for(c), "search_articles", {"author": "Nobody Here"})
    assert result["articles"] == [] and "No article search was run" in result["warning"]


def test_get_article_returns_text_or_html_with_attachments():
    c = connection()
    c.routes[("GET", "/api/2045/knowledgebase/95821")] = ARTICLE
    server = server_for(c)
    text = call(server, "get_article", {"article_id": 95821})
    assert text["article"]["Subject"] == "Mac password doesn't match" and "Body" not in text["article"]
    assert text["body_text"].startswith("Did you change") and text["attachments"] == [{"ID": "att-1", "Name": "screen.png", "Size": 1200}]
    html = call(server, "get_article", {"article_id": 95821, "format": "html"})
    assert html["body_html"].startswith("<p>Did you change") and "body_text" not in html
    c.routes[("GET", "/api/2045/knowledgebase/1")] = {"ID": 2}
    with pytest.raises(Exception, match="could not be read"):
        call(server, "get_article", {"article_id": 1})


def test_article_categories_flattens_the_tree_with_depth_and_optional_subtree():
    c = connection()
    c.routes[("GET", "/api/2045/knowledgebase/categories")] = CATEGORIES
    server = server_for(c)
    result = call(server, "article_categories")
    assert [(r["ID"], r["depth"], r["ParentName"]) for r in result["categories"]] == [(9208, 0, None), (9106, 0, None), (9107, 1, "Software")]
    assert result["categories"][2]["url"] == "https://example.test/TDClient/2045/Portal/KB/?CategoryID=9107"
    sub = call(server, "article_categories", {"parent_id": 9106})
    assert [r["ID"] for r in sub["categories"]] == [9107]


def test_related_reads_use_their_endpoints():
    c = connection()
    c.routes[("GET", "/api/2045/knowledgebase/95821/related")] = [ARTICLE]
    c.routes[("GET", "/api/2045/knowledgebase/95821/relatedservices")] = [{"ID": 5, "Name": "Email", "IsActive": True, "Type": 1}]
    c.routes[("GET", "/api/928/assets/1973209/articles")] = [ARTICLE]
    server = server_for(c)
    assert call(server, "related_articles", {"article_id": 95821})["articles"][0]["ID"] == 95821
    assert call(server, "article_services", {"article_id": 95821})["services"] == [{"ID": 5, "Name": "Email", "IsActive": True, "Type": 1}]
    linked = call(server, "asset_articles", {"asset_id": 1973209})
    assert linked["articles"][0]["ID"] == 95821 and "Body" not in linked["articles"][0]
