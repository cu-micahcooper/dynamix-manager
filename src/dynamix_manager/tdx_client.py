from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Any

import requests


GUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def build_auth_headers(token: str, app_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-TDClient-ID": app_id,
        "Accept": "application/json",
    }


def uses_admin_auth(username: str, password: str) -> bool:
    return bool(GUID_PATTERN.fullmatch(username) and GUID_PATTERN.fullmatch(password))


def parse_auth_token(payload: str | dict[str, Any]) -> str:
    if isinstance(payload, str):
        return payload
    if "Token" in payload:
        return payload["Token"]
    raise ValueError("Unable to locate TeamDynamix auth token in response payload")


def _retry_delay_seconds(attempt: int, response: requests.Response) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return float(retry_after)
        except ValueError:
            pass
    return min(2**attempt, 30)


@dataclass
class TeamDynamixClient:
    base_url: str
    app_id: str
    username: str
    password: str
    session: requests.Session | None = None

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if self.session is None:
            self.session = requests.Session()

    def report_endpoint(self, report_id: int) -> str:
        return f"{self.base_url}/api/reports/{report_id}"

    def ticket_search_endpoint(self, ticket_app_id: int | None = None) -> str:
        app = ticket_app_id if ticket_app_id is not None else self.app_id
        return f"{self.base_url}/api/{app}/tickets/search"

    def ticket_endpoint(self, ticket_id: int, ticket_app_id: int) -> str:
        return f"{self.base_url}/api/{ticket_app_id}/tickets/{ticket_id}"

    def ticket_feed_endpoint(self, ticket_id: int, ticket_app_id: int) -> str:
        return f"{self.ticket_endpoint(ticket_id, ticket_app_id)}/feed"

    def feed_item_endpoint(self, uri_or_path: str | int) -> str:
        if isinstance(uri_or_path, int):
            return f"{self.base_url}/api/feed/{uri_or_path}"
        if uri_or_path.startswith("http://") or uri_or_path.startswith("https://"):
            return uri_or_path
        path = uri_or_path.lstrip("/")
        return f"{self.base_url}/{path}"

    def days_off_endpoint(self) -> str:
        return f"{self.base_url}/api/daysoff"

    def auth_endpoint(self) -> str:
        return f"{self.base_url}/api/auth/loginadmin"

    def user_auth_endpoint(self) -> str:
        return f"{self.base_url}/api/auth/login"

    def authenticate(self) -> str:
        if uses_admin_auth(self.username, self.password):
            endpoint = self.auth_endpoint()
            payload = {"BEID": self.username, "WebServicesKey": self.password}
        else:
            endpoint = self.user_auth_endpoint()
            payload = {"UserName": self.username, "Password": self.password}

        response = self.session.post(
            endpoint,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type.lower():
            return parse_auth_token(response.json())
        return parse_auth_token(response.text.strip())

    def fetch_report(self, report_id: int, token: str, with_data: bool = True) -> list[dict[str, Any]]:
        response = self.session.get(
            self.report_endpoint(report_id),
            params={"withData": str(with_data).lower()},
            headers=build_auth_headers(token, self.app_id),
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        return payload.get("DataRows", payload)

    def fetch_days_off(self, token: str) -> list[dict[str, Any]]:
        response = self.session.get(
            self.days_off_endpoint(),
            headers=build_auth_headers(token, self.app_id),
            timeout=60,
        )
        response.raise_for_status()
        return response.json()

    def search_tickets(
        self,
        token: str,
        payload: dict[str, Any],
        ticket_app_id: int | None = None,
    ) -> list[dict[str, Any]]:
        response = self.session.post(
            self.ticket_search_endpoint(ticket_app_id),
            json=payload,
            headers=build_auth_headers(token, self.app_id),
            timeout=60,
        )
        response.raise_for_status()
        return response.json()

    def get_ticket_feed(
        self,
        ticket_id: int,
        token: str,
        ticket_app_id: int,
    ) -> list[dict[str, Any]]:
        response = self.session.get(
            self.ticket_feed_endpoint(ticket_id, ticket_app_id),
            headers=build_auth_headers(token, self.app_id),
            timeout=60,
        )
        response.raise_for_status()
        return response.json()

    def get_feed_item(
        self,
        uri_or_path: str | int,
        token: str,
    ) -> dict[str, Any]:
        response = self.session.get(
            self.feed_item_endpoint(uri_or_path),
            headers=build_auth_headers(token, self.app_id),
            timeout=60,
        )
        response.raise_for_status()
        return response.json()

    def fetch_applications(self, token: str) -> list[dict[str, Any]]:
        response = self.session.get(
            f"{self.base_url}/api/applications",
            headers=build_auth_headers(token, self.app_id),
            timeout=60,
        )
        response.raise_for_status()
        return response.json()

    def list_ticketing_applications(
        self,
        applications: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [app for app in applications if app.get("AppClass") == "TDTickets"]

    def get_ticket(
        self,
        ticket_id: int,
        token: str,
        ticket_app_id: int,
        max_attempts: int = 5,
    ) -> dict[str, Any]:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        last_response = None
        for attempt in range(max_attempts):
            response = self.session.get(
                self.ticket_endpoint(ticket_id, ticket_app_id),
                headers=build_auth_headers(token, self.app_id),
                timeout=60,
            )
            last_response = response
            if response.status_code != 429:
                response.raise_for_status()
                return response.json()
            if attempt + 1 < max_attempts:
                time.sleep(_retry_delay_seconds(attempt, response))

        if last_response is not None:
            last_response.raise_for_status()
        raise RuntimeError("Ticket request failed without a response")
