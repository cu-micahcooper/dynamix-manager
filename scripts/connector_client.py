"""Persistent OAuth test client for the hosted TeamDynamix connector.

State (client id, refresh/access tokens) lives in .local/connector-test-client-<host>.json, which
is git-ignored. Never prints secrets. Set CONNECTOR_URL to target another instance. Usage:

  python scripts/connector_client.py start            # register (once) and print the login URL
  python scripts/connector_client.py exchange <cb-url> # finish the sign-in with the callback URL
  python scripts/connector_client.py tools            # list tools (refreshes tokens as needed)
  python scripts/connector_client.py call <name> '<json args>'
"""
import base64
import hashlib
import json
import secrets
import sys
from pathlib import Path

import httpx

import os

# Production (CU DevOps Playground) by default; CONNECTOR_URL selects another instance, e.g. the pilot.
BASE = os.environ.get("CONNECTOR_URL", "https://connector-production-a492.up.railway.app").rstrip("/")
CALLBACK = "https://localhost:8443/callback"
STATE = (Path(__file__).resolve().parents[1] / ".local"
         / f"connector-test-client-{BASE.removeprefix('https://').split('.')[0]}.json")
http = httpx.Client(base_url=BASE, follow_redirects=False, timeout=60)


def load():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save(state):
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state))
    STATE.chmod(0o600)


def start(scope="tdx.read tdx.write"):
    state = load()
    if "client_id" not in state:
        reg = http.post("/register", json={"redirect_uris": [CALLBACK], "token_endpoint_auth_method": "none",
                                            "grant_types": ["authorization_code", "refresh_token"],
                                            "response_types": ["code"], "scope": scope})
        reg.raise_for_status()
        state["client_id"] = reg.json()["client_id"]
        save(state)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    oauth_state = secrets.token_urlsafe(16)
    r = http.get("/authorize", params={"client_id": state["client_id"], "response_type": "code",
                                        "redirect_uri": CALLBACK, "scope": scope, "state": oauth_state,
                                        "resource": BASE + "/mcp", "code_challenge": challenge,
                                        "code_challenge_method": "S256"})
    assert r.status_code == 302, f"authorize returned HTTP {r.status_code}"
    state.update(verifier=verifier, state=oauth_state, scope=scope)
    save(state)
    print("LOGIN_URL", r.headers["location"])


def exchange(callback_url):
    from urllib.parse import parse_qs, urlsplit
    state = load()
    q = parse_qs(urlsplit(callback_url).query)
    assert q["state"] == [state["state"]], "state mismatch"
    r = http.post("/token", data={"client_id": state["client_id"], "grant_type": "authorization_code",
                                  "code": q["code"][0], "code_verifier": state["verifier"],
                                  "redirect_uri": CALLBACK, "resource": BASE + "/mcp"})
    body = r.json()
    print("token: HTTP", r.status_code, {k: v for k, v in body.items() if k in ("token_type", "expires_in", "scope")})
    r.raise_for_status()
    state.update(access_token=body["access_token"], refresh_token=body["refresh_token"])
    state.pop("verifier", None)
    save(state)


def refresh():
    state = load()
    r = http.post("/token", data={"client_id": state["client_id"], "grant_type": "refresh_token",
                                  "refresh_token": state["refresh_token"], "resource": BASE + "/mcp"})
    if r.status_code != 200:
        sys.exit(f"refresh failed: HTTP {r.status_code}; run 'start' and sign in again")
    body = r.json()
    state.update(access_token=body["access_token"], refresh_token=body["refresh_token"])
    save(state)


def call(method, params=None, _retry=True):
    state = load()
    r = http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                  headers={"Accept": "application/json, text/event-stream",
                           "Authorization": "Bearer " + state.get("access_token", "")})
    if r.status_code == 401 and _retry:
        refresh()
        return call(method, params, _retry=False)
    return r


def tool(name, arguments):
    """Return the tool's structured content, or {'error': text} for an error result."""
    res = call("tools/call", {"name": name, "arguments": arguments}).json()["result"]
    if res.get("isError"):
        return {"error": res["content"][0]["text"], "_meta": res.get("_meta")}
    return res["structuredContent"]


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "start":
        start(*sys.argv[2:3])
    elif cmd == "exchange":
        exchange(sys.argv[2])
    elif cmd == "tools":
        for t in call("tools/list").json()["result"]["tools"]:
            print(f"  {t['name']:24s} readOnly={t['annotations']['readOnlyHint']} scopes={t.get('securitySchemes', [{}])[0].get('scopes')}")
    elif cmd == "call":
        print(json.dumps(tool(sys.argv[2], json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}), indent=1)[:4000])
