# Hosted Connector Implementation Plan

**Goal:** Build and test the multi-user resource-server foundation without exposing
the existing single-account prototype.

**Architecture:** External OAuth authorization server; stateless authenticated
MCP HTTP; encrypted personal TDX token mapping; per-call upstream connection.

**Tech Stack:** Python, FastMCP, PyJWT, cryptography, SQLite, Starlette/uvicorn.

- [x] Write failing tests in `tests/test_hosted_connector.py` for signed-token
  verification, encrypted vault and user isolation. Run with `.venv/bin/python3.14
  -m pytest tests/test_hosted_connector.py -q` and verify feature-missing failure.
- [x] Add `src/dynamix_manager/hosted.py` for validated configuration, OAuth
  resource server and per-call resolver; `hosted_vault.py` for encrypted mappings.
- [x] Extend `plugin.create_server` with optional connection provider and FastMCP
  settings, preserving stdio defaults. Test scope metadata and tool isolation.
- [x] Add ASGI HTTP integration tests with signed synthetic identities and mocked
  TDX boundary. Test unauthorized, wrong issuer/audience, expired, insufficient
  scope and identity mismatch. Test revocation and concurrent users.
- [x] Add explicit hosted dependencies, container recipe and setup/launch checklist.
- [x] Run focused tests, full Python suite, frontend tests, Ruff and diff checks.
  Review authentication boundaries. Report incomplete SSO/deployment gates honestly.

No cloud deployment, production app registration or identity-provider changes
without a known destination and the required administrative authority.

## Verification outcome

288 Python tests and 14 frontend tests passed. Ruff and diff checks passed.
Review found and tests reproduced a blocking-event-loop issue; hosted tools now
use a bounded eight-thread pool and a delayed-upstream test proves responsiveness.
Malformed JWKS handling is covered. Docker is unavailable here, so the container
recipe has not been built. Real SSO linking/renewal, deployment and ChatGPT host
verification remain open gates documented in `docs/hosted-connector.md`.
