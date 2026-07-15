import base64
from email import message_from_bytes
from email.policy import default

from dynamix_manager import gmail


class _FakeDraftCreate:
    def __init__(self, capture: dict[str, object], body: dict[str, object]):
        self._capture = capture
        self._body = body

    def execute(self):
        self._capture["body"] = self._body
        return {"id": "draft-123"}


class _FakeDrafts:
    def __init__(self, capture: dict[str, object]):
        self._capture = capture

    def create(self, *, userId: str, body: dict[str, object]):
        self._capture["userId"] = userId
        return _FakeDraftCreate(self._capture, body)


class _FakeUsers:
    def __init__(self, capture: dict[str, object]):
        self._capture = capture

    def drafts(self):
        return _FakeDrafts(self._capture)


class _FakeService:
    def __init__(self, capture: dict[str, object]):
        self._capture = capture

    def users(self):
        return _FakeUsers(self._capture)


def test_create_draft_embeds_inline_attachments(tmp_path, monkeypatch):
    token_path = tmp_path / "gmail-token.json"
    token_path.write_text("{}", encoding="utf-8")
    capture: dict[str, object] = {}

    monkeypatch.setattr(gmail, "_get_credentials", lambda path: object())

    import googleapiclient.discovery

    monkeypatch.setattr(
        googleapiclient.discovery,
        "build",
        lambda service_name, version, credentials: _FakeService(capture),
    )

    draft = gmail.create_draft(
        subject="CFO Update",
        html_body='<img src="cid:cfo-header-burst">',
        to="cfo@example.test",
        token_path_override=str(token_path),
        inline_attachments=[
            {
                "filename": "cfo-header-burst.png",
                "content_type": "image/png",
                "content_id": "cfo-header-burst",
                "data": b"png",
            }
        ],
    )

    assert draft["id"] == "draft-123"
    assert capture["userId"] == "me"
    raw = capture["body"]["message"]["raw"]  # type: ignore[index]
    message = message_from_bytes(base64.urlsafe_b64decode(raw), policy=default)
    attachments = [
        part for part in message.walk() if part.get_content_disposition() == "inline"
    ]
    assert message["Subject"] == "CFO Update"
    assert message["To"] == "cfo@example.test"
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "cfo-header-burst.png"
    assert attachments[0]["Content-ID"] == "<cfo-header-burst>"
    assert attachments[0].get_content_type() == "image/png"
    assert attachments[0].get_payload(decode=True) == b"png"
