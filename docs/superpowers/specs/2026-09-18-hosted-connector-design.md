# Hosted TeamDynamix connector

Approved direction: one hosted read-only MCP service, individual identities and TDX
credentials, existing in-chat ticket drilldown. Never share the prototype account.

## Build boundary

Implement the resource-server foundation now. OAuth authorization and SSO login
are delegated to a configured external authorization server; do not implement a
new identity provider. Validate signed RS256 access tokens against a configured
JWKS endpoint, exact issuer and resource audience, expiry, subject and scope.
Use `(issuer, subject)` as identity, never email or caller-supplied user IDs.

An encrypted local SQLite vault maps that identity to a personal TDX token and
expected TDX UID. Bind ciphertext to the identity. Resolve it for each tool call,
validate the upstream UID, and close the upstream session after the call. No
global connection, env credential fallback, passwords, or shared ticket cache.
Expired/unlinked/revoked users fail closed. Vault writes are an operator-only
Python API for now; no public linking endpoint is shipped until the supported
SSO handoff is confirmed. This is NOT finished self-service account linking.

Use stateless MCP HTTP, OAuth resource metadata and per-tool security metadata.
Reuse the current ticket UI and tools. Protect every MCP request and resource.
Public health check must contain no identities or configuration. HTTPS termination,
rate limits, secret injection and the external authorization service are deployment
requirements, not silently provisioned cloud resources.

## Evidence and launch gates

Cedarville's public `/TDWebApi/swagger/v1/openapi.json` inspected 2026-09-18:
login accepts LoginParameters and returns bearer token; loginsso returns bearer
token for the current SSO session. This does not establish an OAuth redirect or
token exchange integration. Do not copy browser session cookies.

Before launch: select host/domain; configure OAuth provider with ChatGPT-compatible
authorization-code/PKCE and exact resource scope; verify supported personal TDX
linking/renewal; test with two real users with different ticket permissions;
verify ChatGPT-hosted UI end to end. Token-only operator provisioning is a test
bridge, not an approved long-term onboarding experience.

## Verification

Signed-token negative tests; encrypted vault identity swap, missing/revoked record
and expiry tests; unauthenticated HTTP challenge and metadata; concurrent two-user
HTTP tool calls with distinct upstream credentials; no env fallback; existing
stdio tests and frontend drilldown regressions. Synthetic integration is clearly
distinguished from production SSO and ChatGPT verification.
