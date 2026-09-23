"""Saved searches, attachments, templates and response templates (read-only)."""
import asyncio
import json
from unittest.mock import Mock

import pytest

from dynamix_manager.plugin import Connection, create_server

VALUES = {"TDX_BASE_URL": "https://example.test/TDWebApi", "TDX_APP_ID": "634",
          "WORKBENCH_PERSONAL_TOKEN": "private-token"}
APPS = [{"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"}, {"AppID": 1072, "Name": "CTL Tickets", "AppClass": "TDTickets"}]
TICKET = {"ID": 30605254, "AppID": 634, "Title": "Dell support", "StatusName": "Open", "ModifiedDate": "2026-09-01T00:00:00Z",
          "Attachments": [{"ID": "3346ea38-2325-47c0-b27f-1bc18f0cbe02", "Name": "Quote.pdf", "Size": 723801, "AttachmentType": 9,
                           "IsPrivate": False, "CreatedFullName": "Seth Highfield", "CreatedDate": "2026-08-10T00:00:00Z"},
                          {"ID": "aa0fa81a-3ecc-428b-9bc1-7c93db09b5ae", "Name": "notes.txt", "Size": 12, "AttachmentType": 9,
                           "IsPrivate": True, "CreatedFullName": "Micah Cooper", "CreatedDate": "2026-08-11T00:00:00Z"}]}


def connection():
    client = Mock()
    client.fetch_applications.return_value = [dict(a) for a in APPS]
    client.list_ticketing_applications.side_effect = lambda apps: [a for a in apps if a.get("AppClass") == "TDTickets"]
    client.get_ticket.side_effect = lambda ticket_id, token, app_id, max_attempts=5: json.loads(json.dumps(TICKET)) if ticket_id == 30605254 else (_ for _ in ()).throw(RuntimeError("nope"))
    c = Connection(values=VALUES, client=client)
    c.routes, c.calls, c.raw = {}, [], {}

    def request(method, url, **kwargs):
        path = url.removeprefix(c.base_url)
        c.calls.append((method, path, kwargs))
        if path in c.raw:
            body, ctype = c.raw[path]
            return Mock(status_code=200, content=body, headers={"Content-Type": ctype}, json=Mock(side_effect=ValueError))
        value = c.routes[(method, path)]
        if isinstance(value, Exception):
            raise value
        return Mock(status_code=200, json=lambda: json.loads(json.dumps(value)))

    client.session.get.side_effect = lambda url, **kw: request("GET", url, **kw)
    client.session.post.side_effect = lambda url, **kw: request("POST", url, **kw)
    return c


def call(c, name, arguments=None):
    result = asyncio.run(create_server(connection_provider=lambda: c).call_tool(name, arguments or {}))
    return result[1] if isinstance(result, tuple) else result


def test_saved_searches_list_and_run_with_paging():
    c = connection()
    c.routes[("GET", "/api/634/tickets/searches")] = [{"ID": 86907, "Name": "TechStop - All Open", "ComponentName": "Ticket",
                                                       "CreatedFullName": "A", "AppName": "InfoTech Tickets", "AppID": 634}]
    listed = call(c, "saved_searches")
    assert listed["searches"] == [{"ID": 86907, "Name": "TechStop - All Open", "ComponentName": "Ticket", "CreatedFullName": "A"}]
    assert listed["application"] == {"AppID": 634, "Name": "InfoTech Tickets"}
    c.routes[("POST", "/api/634/tickets/searches/86907/results")] = {
        "Data": [dict(TICKET), {**TICKET, "ID": 2, "Title": "Second"}], "TotalCount": 6, "CurrentPageIndex": 1, "PageSize": 2}
    result = call(c, "run_saved_search", {"search_id": 86907, "page": 2, "page_size": 2, "only_open": True, "text": "dell"})
    assert c.calls[-1][2]["json"] == {"Page": {"PageIndex": 1, "PageSize": 2}, "OnlyOpen": True, "SearchText": "dell"}
    assert [t["ID"] for t in result["tickets"]] == [30605254, 2] and result["total"] == 6 and result["page"] == 2
    assert result["pages"] == 3 and result["complete"] is False and result["tickets"][0]["url"].endswith("TicketID=30605254")
    assert "Attachments" not in json.dumps(result)


def test_ticket_attachments_lists_metadata_from_the_ticket():
    c = connection()
    result = call(c, "ticket_attachments", {"ticket_id": 30605254})
    assert result["attachments"] == [
        {"ID": "3346ea38-2325-47c0-b27f-1bc18f0cbe02", "Name": "Quote.pdf", "Size": 723801, "IsPrivate": False,
         "CreatedFullName": "Seth Highfield", "CreatedDate": "2026-08-10T00:00:00Z"},
        {"ID": "aa0fa81a-3ecc-428b-9bc1-7c93db09b5ae", "Name": "notes.txt", "Size": 12, "IsPrivate": True,
         "CreatedFullName": "Micah Cooper", "CreatedDate": "2026-08-11T00:00:00Z"}]
    assert result["application"]["AppID"] == 634 and result["returned"] == 2


def test_read_attachment_extracts_text_and_refuses_oversized_or_binary_content():
    c = connection()
    meta = {"ID": "aa0fa81a-3ecc-428b-9bc1-7c93db09b5ae", "Name": "notes.txt", "Size": 12, "IsPrivate": True, "CreatedFullName": "Micah Cooper"}
    c.routes[("GET", "/api/attachments/aa0fa81a-3ecc-428b-9bc1-7c93db09b5ae")] = meta
    c.raw["/api/attachments/aa0fa81a-3ecc-428b-9bc1-7c93db09b5ae/content"] = (b"hello <b>bold</b>", "text/plain; charset=utf-8")
    result = call(c, "read_attachment", {"attachment_id": "aa0fa81a-3ecc-428b-9bc1-7c93db09b5ae"})
    assert result["attachment"]["Name"] == "notes.txt" and result["text"] == "hello <b>bold</b>" and result["truncated"] is False
    c.routes[("GET", "/api/attachments/3346ea38-2325-47c0-b27f-1bc18f0cbe02")] = {"ID": "3346ea38-2325-47c0-b27f-1bc18f0cbe02", "Name": "photo.png", "Size": 5000}
    c.raw["/api/attachments/3346ea38-2325-47c0-b27f-1bc18f0cbe02/content"] = (b"\x89PNG...", "image/png")
    binary = call(c, "read_attachment", {"attachment_id": "3346ea38-2325-47c0-b27f-1bc18f0cbe02"})
    assert binary["text"] is None and "image/png" in binary["warning"]
    c.routes[("GET", "/api/attachments/11111111-2222-4333-8444-555555555555")] = {"ID": "11111111-2222-4333-8444-555555555555", "Name": "big.csv", "Size": 50_000_000}
    huge = call(c, "read_attachment", {"attachment_id": "11111111-2222-4333-8444-555555555555"})
    assert huge["text"] is None and "too large" in huge["warning"] and not any("/content" in p for _, p, _ in c.calls if "11111111" in p)
    with pytest.raises(Exception):
        call(c, "read_attachment", {"attachment_id": "not-a-guid"})


def test_templates_and_response_templates_are_read_per_application():
    c = connection()
    c.routes[("GET", "/api/634/tickets/templates")] = [{"ID": 16266, "Name": "CedarPrint (Getting Started)", "IsGlobal": True,
                                                        "ClassificationName": "Service Request", "Description": "Printing setup"}]
    listed = call(c, "ticket_templates", {"search": "cedarprint"})
    assert listed["templates"] == [{"ID": 16266, "Name": "CedarPrint (Getting Started)", "ClassificationName": "Service Request",
                                    "IsGlobal": True, "Description": "Printing setup"}]
    assert call(c, "ticket_templates", {"search": "zzz"})["templates"] == []
    c.routes[("GET", "/api/634/tickets/templates/16266")] = {"ID": 16266, "Name": "CedarPrint (Getting Started)", "Title": "Printing help",
                                                              "TypeID": 23279, "FormID": 1, "StatusID": 2, "PriorityID": 3, "ResponsibleGroupID": 9,
                                                              "Description": "<p>Steps</p>", "Attributes": [], "TaskTemplateName": None}
    detail = call(c, "get_ticket_template", {"template_id": 16266})
    assert detail["template"]["TypeID"] == 23279 and detail["description_text"] == "Steps"
    c.routes[("GET", "/api/1072/tickets/responseTemplates")] = [{"ID": 2338, "Name": "Thank you and close-out", "CategoryName": "TechStop",
                                                                 "CategoryID": 5, "Description": "", "Comments": "<p>Thanks, closing.</p>"}]
    canned = call(c, "response_templates", {"app": "CTL Tickets", "search": "thank"})
    assert c.calls[-1][2]["params"] == {"searchText": "thank"}
    assert canned["templates"][0]["text"] == "Thanks, closing." and canned["templates"][0]["CategoryName"] == "TechStop"
    assert canned["application"] == {"AppID": 1072, "Name": "CTL Tickets"}


def minimal_pdf(text):
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def test_read_attachment_extracts_pdf_text():
    c = connection()
    pdf_id = "3346ea38-2325-47c0-b27f-1bc18f0cbe02"
    c.routes[("GET", f"/api/attachments/{pdf_id}")] = {"ID": pdf_id, "Name": "Quote.pdf", "Size": 900}
    c.raw[f"/api/attachments/{pdf_id}/content"] = (minimal_pdf("Hello PDF quote"), "application/pdf")
    result = call(c, "read_attachment", {"attachment_id": pdf_id})
    assert "Hello PDF quote" in result["text"] and result["truncated"] is False
