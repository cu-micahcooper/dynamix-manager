import asyncio
import json
from unittest.mock import Mock

import pytest

import dynamix_manager.plugin as plugin
from dynamix_manager.plugin import Connection, create_server

VALUES = {"TDX_BASE_URL": "https://example.test/TDWebApi", "TDX_APP_ID": "2045",
          "WORKBENCH_PERSONAL_TOKEN": "private-token"}
ASSET = {"ID": 501, "AppID": 928, "Name": "SHACK Dell chassis", "Tag": "CU-0501", "SerialNumber": "SN-501",
         "StatusName": "In Use", "ProductModelName": "PowerEdge MX7000", "ManufacturerName": "Dell",
         "OwningCustomerName": "Alan McCain", "OwningDepartmentName": "Information Technology",
         "LocationName": "SSC", "LocationRoomName": "101", "ConfigurationItemID": 77001,
         "Attributes": [{"Name": "Warranty", "Value": "2027-01-01"}], "ModifiedDate": "2026-09-01T00:00:00Z"}


def connection(apps=None):
    client = Mock()
    client.fetch_applications.return_value = apps if apps is not None else [
        {"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"},
        {"AppID": 928, "Name": "InfoTech Assets/CIs", "AppClass": "TDAssets"},
        {"AppID": 2045, "Name": "Client Portal", "AppClass": "TDClient"},
    ]
    client.list_ticketing_applications.side_effect = lambda apps: [a for a in apps if a.get("AppClass") == "TDTickets"]
    c = Connection(values=VALUES, client=client)
    # Route raw tenant calls (session.get/post) through a recorder keyed by path.
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


def test_asset_application_is_discovered_by_class_and_cached():
    c = connection()
    assert c.asset_app_id == 928
    assert c.asset_application["Name"] == "InfoTech Assets/CIs"
    other = connection()
    assert other.asset_app_id == 928
    other.client.fetch_applications.assert_not_called()


def test_missing_or_ambiguous_asset_application_fails_closed():
    none = connection(apps=[{"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"}])
    with pytest.raises(RuntimeError, match="asset"):
        none.asset_app_id
    plugin._APPLICATIONS.clear()
    two = connection(apps=[{"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"},
                           {"AppID": 928, "Name": "A", "AppClass": "TDAssets"}, {"AppID": 929, "Name": "B", "AppClass": "TDAssets"}])
    with pytest.raises(RuntimeError, match="uniquely"):
        two.asset_app_id


def test_search_assets_maps_filters_server_side_and_projects_compactly():
    c = connection()
    c.routes[("POST", "/api/928/assets/search")] = [ASSET, {**ASSET, "ID": 502, "Name": "Second"}]
    result = call(server_for(c), "search_assets", {
        "query": "chassis", "serial_or_tag": "CU-05", "status_ids": [3], "in_service": True,
        "owner_uids": ["aaaaaaaa-0000-4000-8000-000000000001"], "owning_department_ids": [56883],
        "product_model_ids": [9], "manufacturer_ids": [4], "location_ids": [12], "room_id": 7,
        "ticket_ids": [30605254], "parent_ids": [500], "acquired_from": "2024-01-01", "acquired_to": "2025-12-31",
        "replacement_due_to": "2027-06-30", "modified_from": "2026-01-01", "limit": 5,
    })
    method, path, kwargs = c.calls[-1]
    assert (method, path) == ("POST", "/api/928/assets/search")
    assert kwargs["json"] == {
        "SearchText": "chassis", "SerialLike": "CU-05", "StatusIDs": [3], "IsInService": True,
        "OwningCustomerIDs": ["aaaaaaaa-0000-4000-8000-000000000001"], "OwningDepartmentIDs": [56883],
        "ProductModelIDs": [9], "ManufacturerIDs": [4], "LocationIDs": [12], "RoomID": 7,
        "TicketIDs": [30605254], "ParentIDs": [500], "AcquisitionDateFrom": "2024-01-01",
        "AcquisitionDateTo": "2025-12-31", "ExpectedReplacementDateTo": "2027-06-30",
        "ModifiedDateFrom": "2026-01-01", "MaxResults": 5,
    }
    assert kwargs["headers"]["Authorization"] == "Bearer private-token"
    assert result["returned"] == 2 and result["complete"] is True and result["resolved_people"] == []
    first = result["assets"][0]
    assert first == {"ID": 501, "Name": "SHACK Dell chassis", "Tag": "CU-0501", "SerialNumber": "SN-501",
                     "StatusName": "In Use", "ProductModelName": "PowerEdge MX7000", "ManufacturerName": "Dell",
                     "OwningCustomerName": "Alan McCain", "OwningDepartmentName": "Information Technology",
                     "LocationName": "SSC", "LocationRoomName": "101",
                     "url": "https://example.test/TDNext/Apps/928/Assets/AssetDet?AssetID=501"}
    assert "Attributes" not in json.dumps(result)


def test_search_assets_resolves_owner_and_user_through_the_people_api():
    c = connection()
    alan = {"UID": "aaaaaaaa-0000-4000-8000-000000000001", "FullName": "Alan McCain", "PrimaryEmail": "mccaina@cedarville.edu", "IsActive": True}
    c.routes[("GET", "/api/people/lookup")] = [alan]
    c.routes[("POST", "/api/928/assets/search")] = [ASSET]
    result = call(server_for(c), "search_assets", {"owner": "Alan McCain", "user": "mccaina", "limit": 5})
    payload = c.calls[-1][2]["json"]
    assert payload["OwningCustomerIDs"] == [alan["UID"]] and payload["UsingCustomerIDs"] == [alan["UID"]]
    assert [p["role"] for p in result["resolved_people"]] == ["owner", "user"]
    assert result["resolved_people"][0]["matched"][0]["email"] == "mccaina@cedarville.edu"

    c = connection()
    c.routes[("GET", "/api/people/lookup")] = []
    nobody = call(server_for(c), "search_assets", {"owner": "Nobody"})
    assert nobody["assets"] == [] and "no person" in nobody["warning"].lower()
    assert not any(path == "/api/928/assets/search" for _, path, _ in c.calls)


@pytest.mark.parametrize("arguments", [{"limit": 0}, {"limit": 101}, {"acquired_from": "yesterday"},
                                       {"owner_uids": ["nope"]}, {"status_ids": [0]}, {"query": "x" * 501}])
def test_search_assets_rejects_invalid_filters_before_network(arguments):
    c = connection()
    with pytest.raises(Exception):
        call(server_for(c), "search_assets", arguments)
    assert c.calls == []


def test_get_asset_returns_full_record_with_link_and_readable_attributes():
    c = connection()
    c.routes[("GET", "/api/928/assets/501")] = ASSET
    result = call(server_for(c), "get_asset", {"asset_id": 501})
    assert result["asset"]["ID"] == 501 and result["asset"]["Attributes"][0]["Name"] == "Warranty"
    assert result["url"].endswith("/TDNext/Apps/928/Assets/AssetDet?AssetID=501")
    assert result["configuration_item_id"] == 77001


def test_asset_feed_ticket_assets_and_asset_tickets_are_bounded_reads():
    c = connection()
    c.routes[("GET", "/api/928/assets/501/feed")] = [{"Body": "<p>Racked</p>", "IsPrivate": False}] * 3
    c.routes[("GET", "/api/634/tickets/30605254/assets")] = [{"ID": 77001, "Name": "SHACK Dell chassis", "BackingItemID": 501}]
    c.routes[("GET", "/api/928/assets/501")] = ASSET
    c.client.search_tickets.return_value = [{"ID": 30605254, "Title": "Chassis support"}]
    server = server_for(c)
    feed = call(server, "asset_feed", {"asset_id": 501, "limit": 2})
    assert len(feed["items"]) == 2 and feed["items"][0]["body_text"] == "Racked" and feed["complete"] is False
    linked = call(server, "ticket_assets", {"ticket_id": 30605254})
    assert linked["assets"][0]["ID"] == 77001 and linked["assets"][0]["BackingItemID"] == 501
    tickets = call(server, "asset_tickets", {"asset_id": 501, "limit": 10})
    assert tickets["tickets"][0]["ID"] == 30605254 and tickets["configuration_item_id"] == 77001
    c.client.search_tickets.assert_called_once_with(
        "private-token", {"MaxResults": 10, "ConfigurationItemIDs": [77001]}, 634, max_attempts=1)


def test_search_tickets_accepts_an_asset_filter_via_its_configuration_item():
    c = connection()
    c.routes[("GET", "/api/928/assets/501")] = ASSET
    c.client.search_tickets.return_value = []
    call(server_for(c), "search_tickets", {"asset_id": 501, "status_classes": [1, 2]})
    payload = c.client.search_tickets.call_args.args[1]
    assert payload["ConfigurationItemIDs"] == [77001] and payload["StatusClassIDs"] == [1, 2]


def test_asset_metadata_lists_statuses_and_searches_models_and_vendors():
    c = connection()
    c.routes[("GET", "/api/928/assets/statuses")] = [{"ID": 3, "Name": "In Use", "IsActive": True, "IsOutOfService": False, "Extra": "x"},
                                                       {"ID": 4, "Name": "Retired", "IsActive": False, "IsOutOfService": True}]
    c.routes[("POST", "/api/928/assets/models/search")] = [{"ID": 9, "Name": "PowerEdge MX7000", "IsActive": True, "ManufacturerName": "Dell"}]
    c.routes[("POST", "/api/928/assets/vendors/search")] = [{"ID": 4, "Name": "Dell", "IsActive": True, "IsManufacturer": True}]
    server = server_for(c)
    statuses = call(server, "asset_metadata", {"kind": "statuses"})
    assert statuses["results"] == [{"ID": 3, "Name": "In Use", "IsOutOfService": False}]
    models = call(server, "asset_metadata", {"kind": "models", "search": "PowerEdge", "limit": 5})
    assert models["results"] == [{"ID": 9, "Name": "PowerEdge MX7000", "ManufacturerName": "Dell"}]
    assert c.calls[-1][2]["json"] == {"SearchText": "PowerEdge", "IsActive": True, "MaxResults": 5}
    vendors = call(server, "asset_metadata", {"kind": "vendors", "search": "Dell", "limit": 5})
    assert vendors["results"] == [{"ID": 4, "Name": "Dell", "IsManufacturer": True}]
    with pytest.raises(Exception):
        call(server, "asset_metadata", {"kind": "models"})  # search required
