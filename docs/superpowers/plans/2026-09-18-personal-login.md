# Personal TDX Login Implementation Plan

> Execute with superpowers:subagent-driven-development and test-driven-development.

**Goal:** An account-restricted, TDX-backed OAuth pilot on Railway.

**Architecture:** Preserve external OAuth mode. Personal mode uses MCP SDK OAuth
handlers with an encrypted SQLite grant store, exact callback allowlist, TDX login
and verified UID. Existing per-request ticket isolation remains unchanged.

**Tech Stack:** Python, FastMCP, Starlette, cryptography, SQLite, Docker, Railway.

## Task 1: Personal OAuth and login

Files: new `src/dynamix_manager/personal_auth.py`, optionally separate
`personal_auth_store.py` and `personal_login.html`; modify `hosted.py` factory
and `pyproject.toml` package data; tests `tests/test_personal_auth.py`.

- [x] Write failing synthetic protocol/security tests and run `.venv/bin/python3.14
  -m pytest tests/test_personal_auth.py -q` to establish red.
- [x] Implement SDK provider with encrypted durable registrations/grants,
  atomic code consumption, rotating refresh and replay-family revocation.
  Require exact `/mcp` resource at authorize and token endpoints.
- [x] Implement GET/POST login with browser-bound single-use transaction, CSRF,
  exact Origin, bounded requests/rate limits, consent, no-store/same-origin-referrer/CSP.
  Validate the upstream UID against the configured sole allowed UID and refuse
  admin credentials. Discard passwords; expiry must be known and bounded.
- [x] Factory dispatch: `TDX_HOSTED_AUTH_MODE=personal`, public URL, vault path/key,
  `TDX_HOSTED_ALLOWED_UID`, JSON `TDX_HOSTED_REDIRECT_URIS`. An empty redirect list
  safely denies all OAuth registrations/authorizations until ChatGPT supplies its
  exact callback. Do not invent a callback to make the deployment look complete.
- [x] Run tests for success, wrong UID/origin/resource/redirect/PKCE, replay,
  refresh expiry/revocation, encrypted persistence and sanitized errors.
- [x] Independent spec and security review; fix issues and rerun tests.

## Task 2: Portable startup and deployment packaging

Files: `src/dynamix_manager/hosted_start.py`, `deploy/hosted.Dockerfile`,
`tests/test_hosted_start.py`, `docs/hosted-connector.md`.

- [x] Test PORT validation, exact `/data/private` initialization, rejection of
  symlinks, privilege drop ordering and uvicorn invocation before implementation.
- [x] For Railway's root-owned volume, start a minimal launcher as root only to
  create/chown `/data/private`, then clear groups, setgid/setuid 10001 and exec
  the app. Refuse unexpected writable paths; never recursive chown.
- [x] Preserve external/non-Railway operation. No credentials in image layers.
- [x] Create a temporary allowlisted source staging bundle with only required
  source, metadata, Dockerfile and Railway configuration. No `.env`, ticket data,
  notebooks, reports or Git history. Validate bundle entries before upload.

## Task 3: Deployment and verification

- [x] Run complete Python/frontend suites, Ruff and diff checks before release.
- [x] Configure approved Railway project/service only. Generate encryption key
  directly into Railway's stdin secret setter; never print it. Derive allowed
  UID using existing personal TDX credentials, without printing credentials.
- [x] Deploy allowlisted bundle and verify build/startup, health, public discovery,
  denial of unauthenticated MCP and rejection of unapproved callbacks.
- [ ] Obtain exact callback from ChatGPT app configuration; enable it only after
  verification. Finish live personal login and in-chat drilldown if available.
- [x] Report deployment and ChatGPT integration separately; leave no implication
  that a healthy but unlinked endpoint is a finished ChatGPT app.

Approved Railway project: `cfba9747-5b49-4852-b798-4b8a75a5af1a`;
service `cd3c8a3d-412e-4506-affe-d3b37e72fd87`;
environment `6ff3107f-b5e7-4370-a01f-03d40030a3fb`.

## Verification checkpoint

2026-09-18: 344 Python tests, 14 frontend tests, focused Ruff and diff checks pass.
Independent auth reviews approved the fixed implementation. Live deployment
`b8da231b-41f3-400c-bc8d-da9fff282a04` passed health, discovery, unauthenticated MCP
denial and callback rejection probes. Restart health/denial also passed.
ChatGPT is still signed out, so exact callback and end-to-end login/drilldown are
pending. Callback allowlist stays empty. Direct SSH file-mode inspection was
unavailable without a registered key; no new SSH access was created.
