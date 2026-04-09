"""Gmail draft creation using the email_hoover OAuth2 token.

The token at ~/.local/state/email-hoover/gmail-token.json is reused directly —
it already has gmail.modify scope and a refresh_token, so no new browser auth
is needed.

If that token is ever revoked or missing, re-authenticate via the email_hoover
app (or set GMAIL_TOKEN_PATH in .env to point to a different token file).
"""
from __future__ import annotations

import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

_DEFAULT_TOKEN_PATH = Path.home() / ".local" / "state" / "email-hoover" / "gmail-token.json"


def _token_path(override: str | None = None) -> Path:
    return Path(override) if override else _DEFAULT_TOKEN_PATH


def credentials_available(token_path_override: str | None = None) -> bool:
    return _token_path(token_path_override).exists()


def _get_credentials(token_path: Path):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    import json

    payload = json.loads(token_path.read_text(encoding="utf-8"))
    scopes = payload.get("scopes")
    creds = Credentials.from_authorized_user_info(payload, scopes=scopes)

    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return creds


def create_draft(
    subject: str,
    html_body: str,
    *,
    to: str = "",
    token_path_override: str | None = None,
) -> dict:
    """Create a Gmail draft with an HTML body. Returns the draft resource dict."""
    from googleapiclient.discovery import build

    path = _token_path(token_path_override)
    creds = _get_credentials(path)
    service = build("gmail", "v1", credentials=creds)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    if to:
        msg["To"] = to
    msg.attach(MIMEText(html_body, "html"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return (
        service.users()
        .drafts()
        .create(userId="me", body={"message": {"raw": raw}})
        .execute()
    )
