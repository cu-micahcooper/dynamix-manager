import json
from unittest.mock import Mock

import pytest
import requests


def adapter():
    from dynamix_manager.workbench.tdx import TDXAdapter

    a = TDXAdapter("https://example.teamdynamix.com/TDWebApi", 7, token="secret")
    a.client.session = Mock()
    return a


def response(payload, status=200):
    r = Mock(status_code=status, headers={"Content-Type": "application/json"})
    r.json.return_value = payload
    return r


def test_login_requires_personal_identity_and_never_admin():
    from dynamix_manager.workbench.tdx import TDXAdapter

    guid = "11111111-1111-1111-1111-111111111111"
    with pytest.raises(ValueError, match="personal"):
        TDXAdapter("https://example.com/TDWebApi", 7, guid, guid).login()
    a = adapter()
    a.client.session.request.return_value = response(
        {"UID": "me", "FullName": "Person", "Email": "me@example.com"}
    )
    assert a.login()["id"] == "me"
    assert a.client.session.request.call_args.args[1].endswith("/api/auth/getuser")


def test_endpoint_rejects_credentials_and_insecure_urls():
    from dynamix_manager.workbench.tdx import TDXAdapter

    for url in [
        "http://example.com/TDWebApi",
        "https://user:pass@example.com/TDWebApi",
        "https://example.com/TDWebApi?key=bad",
    ]:
        with pytest.raises(ValueError):
            TDXAdapter(url, 7)


def test_queue_filters_direct_assignment_and_metadata_classes():
    a = adapter()
    a.identity = {"id": "me"}
    a.client.session.request.side_effect = [
        response([{"ID": 1, "Name": "Working", "StatusClass": 2, "IsActive": True}]),
        response(
            [
                {"ID": 1, "StatusID": 1, "ResponsibleUid": "me"},
                {"ID": 2, "StatusID": 1, "ResponsibleUid": "other"},
                {"ID": 3, "StatusID": 9, "ResponsibleUid": "me"},
            ]
        ),
    ]
    result = a.queue()
    assert [t["id"] for t in result["tickets"]] == [1]
    assert result["complete"] is False and result["warning"]


def test_history_expands_replies_and_inherits_private_visibility():
    a = adapter()
    a.identity = {"id": "me"}
    a.statuses = [{"ID": 1, "Name": "Working", "StatusClass": 2, "IsActive": True}]
    a.client.session.request.side_effect = [
        response({"ID": 1, "StatusID": 1, "RequestorUid": "requester"}),
        response(
            [
                {
                    "ID": 10,
                    "Body": "note",
                    "IsPrivate": True,
                    "RepliesCount": 1,
                    "Uri": "https://example.teamdynamix.com/TDWebApi/api/feed/10",
                }
            ]
        ),
        response(
            {
                "ID": 10,
                "Body": "note",
                "IsPrivate": True,
                "RepliesCount": 1,
                "Replies": [{"ID": 11, "Body": "reply", "CreatedUid": "requester"}],
            }
        ),
    ]
    t = a.ticket(1)
    assert t["history_complete"] is True
    assert len(t["history"]) == 2
    assert all(h["private"] for h in t["history"])


def test_history_refuses_cross_origin_uri_without_leaking_token():
    a = adapter()
    a.statuses = []
    a.client.session.request.side_effect = [
        response({"ID": 1}),
        response(
            [
                {
                    "ID": 10,
                    "Body": "note",
                    "RepliesCount": 1,
                    "Uri": "https://evil.example/api/feed/10",
                }
            ]
        ),
    ]
    assert a.ticket(1)["history_complete"] is False
    assert a.client.session.request.call_count == 2


def test_live_write_gate_and_redacted_auth_errors():
    a = adapter()
    assert not a.can_submit
    with pytest.raises(ValueError, match="verified"):
        a.submit(1, {"text": "never sent"})
    assert a.client.session.request.call_count == 0
    a.client.session.request.side_effect = requests.ConnectionError("secret raw body")
    with pytest.raises(RuntimeError, match="unavailable") as e:
        a.login()
    assert "secret" not in str(e.value)


def ticket():
    return {
        "id": 1,
        "title": "Title",
        "description": "Description",
        "history_complete": True,
        "requester": {"email": "private@example.com"},
        "assessment": "internal assessment",
        "history": [
            {"id": "a", "text": "public fact", "private": False},
            {"id": "b", "text": "private fact", "private": True},
            {"id": "c", "text": "unknown privacy"},
        ],
    }


def test_public_ai_context_excludes_private_and_extra_fields():
    from dynamix_manager.workbench.ai import build_context

    data = json.dumps(build_context(ticket(), "public", "reply instructions"))
    assert "public fact" in data
    for private in [
        "private fact",
        "unknown privacy",
        "internal assessment",
        "private@example.com",
    ]:
        assert private not in data
    assert "private fact" in json.dumps(build_context(ticket(), "internal", "notes"))


def test_ai_rejects_incomplete_and_oversized_context():
    from dynamix_manager.workbench.ai import build_context

    t = ticket()
    t["history_complete"] = False
    with pytest.raises(ValueError, match="complete"):
        build_context(t, "public", "")
    t["history_complete"] = True
    t["description"] = "x" * 100001
    with pytest.raises(ValueError, match="large"):
        build_context(t, "public", "")


def test_ai_responses_schema_store_false_and_reference_validation(monkeypatch):
    from dynamix_manager.workbench.ai import suggest

    output = {
        "summary": "fact",
        "unresolved": [],
        "next_steps": [],
        "questions": [],
        "draft": "Hello",
        "references": ["a"],
    }
    post = Mock(
        return_value=response(
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": json.dumps(output)}
                        ],
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(requests, "post", post)
    assert suggest(ticket(), "public", "", "secret", "gpt-4.1-mini")["draft"] == "Hello"
    payload = post.call_args.kwargs["json"]
    assert payload["store"] is False
    assert payload["text"]["format"]["type"] == "json_schema"
    output["references"] = ["b"]
    post.return_value = response(
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(output)}],
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="reference"):
        suggest(ticket(), "public", "", "secret", "gpt-4.1-mini")


def test_ai_refusal_and_network_error_preserve_secrets(monkeypatch):
    from dynamix_manager.workbench.ai import suggest

    post = Mock(
        return_value=response(
            {
                "status": "completed",
                "output": [{"content": [{"type": "refusal", "refusal": "no"}]}],
            }
        )
    )
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(ValueError, match="refused"):
        suggest(ticket(), "public", "", "secret", "model")
    post.side_effect = requests.Timeout("secret")
    with pytest.raises(RuntimeError) as e:
        suggest(ticket(), "public", "", "secret", "model")
    assert "secret" not in str(e.value)


def test_login_accepts_documented_plaintext_token():
    from dynamix_manager.workbench.tdx import TDXAdapter

    a = TDXAdapter("https://example.com/TDWebApi", 7, "person", "password")
    token = response(None)
    token.headers = {"Content-Type": "text/plain"}
    token.text = "opaque-token"
    token.json.side_effect = ValueError()
    a.client.session = Mock()
    a.client.session.request.side_effect = [token, response({"UID": "me"})]
    assert a.login()["id"] == "me"
    assert a.token == "opaque-token"


def test_verified_feed_write_uses_single_operation_without_retries():
    a = adapter()
    a.can_submit = True
    a.client.session.request.return_value = response(
        {"ID": 42, "CreatedDate": "2026-09-17T12:00:00Z"}
    )
    receipt = a.submit(
        1,
        {
            "text": "Hello",
            "visibility": "public",
            "recipients": ["person@example.com"],
            "status_id": 2,
        },
    )
    assert receipt["id"] == "42"
    assert a.client.session.request.call_args.kwargs["json"] == {
        "Comments": "Hello",
        "IsPrivate": False,
        "IsRichHtml": False,
        "Notify": ["person@example.com"],
        "NewStatusID": 2,
    }
    a.client.session.request.side_effect = requests.Timeout("secret")
    with pytest.raises(RuntimeError, match="uncertain"):
        a.submit(1, {"text": "Hello", "visibility": "internal", "recipients": []})
    assert a.client.session.request.call_count == 2


def test_write_rejection_and_missing_receipt_are_distinct():
    a = adapter()
    a.can_submit = True
    a.client.session.request.return_value = response({}, 400)
    with pytest.raises(ValueError, match="rejected"):
        a.submit(1, {"text": "Hello", "visibility": "internal", "recipients": []})
    a.client.session.request.return_value = response({})
    with pytest.raises(RuntimeError, match="uncertain"):
        a.submit(1, {"text": "Hello", "visibility": "internal", "recipients": []})


@pytest.mark.parametrize(
    "status,code,match",
    [
        (401, "invalid_api_key", "API key"),
        (429, "insufficient_quota", "billing"),
        (429, "rate_limit_exceeded", "rate limit"),
        (404, "model_not_found", "model"),
        (403, "permission_denied", "permission"),
    ],
)
def test_ai_reports_actionable_safe_errors(monkeypatch, status, code, match):
    from dynamix_manager.workbench.ai import suggest

    r = response(
        {"error": {"code": code, "message": "SECRET_KEY private ticket contents"}}
    )
    r.status_code = status
    monkeypatch.setattr(requests, "post", Mock(return_value=r))
    with pytest.raises(RuntimeError, match=match) as error:
        suggest(ticket(), "public", "", "SECRET_KEY", "gpt-5.4-mini")
    assert "SECRET_KEY" not in str(error.value)
    assert "private ticket" not in str(error.value)


def test_status_options_include_completion_class_and_exclude_unavailable():
    a = adapter()
    a.statuses = [
        {"ID": 88, "Name": "Closed", "StatusClass": 3, "IsActive": True},
        {"ID": 9, "Name": "Cancelled", "StatusClass": 4, "IsActive": True},
        {"ID": 10, "Name": "Old closed", "StatusClass": 3, "IsActive": False},
    ]
    assert a._normalize({"ID": 1})["statuses"] == [
        {"id": 88, "name": "Closed", "status_class": "completed"},
        {"id": 9, "name": "Cancelled", "status_class": "cancelled"},
    ]


def test_pdf_metadata_and_authenticated_download():
    a = adapter()
    identity = "11111111-1111-1111-1111-111111111111"
    t = a._normalize(
        {
            "ID": 1,
            "Attachments": [
                {"ID": identity, "Name": "Bill.PDF", "Size": 20, "IsPrivate": True},
                {"ID": "other", "Name": "photo.jpg"},
            ],
        }
    )
    assert t["attachments"] == [
        {"id": identity, "name": "Bill.PDF", "size": 20, "private": True}
    ]
    r = Mock(status_code=200)
    r.iter_content.return_value = [b"%PDF-1.4\n", b"test"]
    a.client.session.request.return_value = r
    assert a.pdf_attachment(identity).startswith(b"%PDF-")
    call = a.client.session.request.call_args
    assert call.args[1].endswith(f"/api/attachments/{identity}/content")
    assert call.kwargs["allow_redirects"] is False
    assert call.kwargs["stream"] is True


def test_pdf_download_rejects_non_pdf_and_redirect():
    a = adapter()
    r = Mock(status_code=200)
    r.iter_content.return_value = [b"<html>Login required</html>"]
    a.client.session.request.return_value = r
    with pytest.raises(ValueError, match="PDF"):
        a.pdf_attachment("11111111-1111-1111-1111-111111111111")
    r.status_code = 302
    with pytest.raises(RuntimeError):
        a.pdf_attachment("11111111-1111-1111-1111-111111111111")


def test_pdf_download_is_bounded_and_response_closed():
    a = adapter()
    r = Mock(status_code=200)
    r.iter_content.return_value = [b"%PDF-" + b"x" * (30 * 1024 * 1024)]
    a.client.session.request.return_value = r
    with pytest.raises(ValueError, match="30 MB"):
        a.pdf_attachment("11111111-1111-1111-1111-111111111111")
    r.close.assert_called_once()
