# Personal TDX login pilot

User approved a TDX-backed connector login restricted to their account, hosted in
their personal Railway project and portable to Azure later.

## Authentication contract

Keep the existing external-issuer mode for future institutional SSO. Add an
explicit personal-pilot mode with the MCP SDK's OAuth authorization-code/PKCE
protocol handlers. The connector's authorization server authenticates against
Cedarville's TDX API; it does not claim that TDX itself supports delegated OAuth.

Identify the sole permitted user by a UID verified using the existing personal
credentials and `/api/auth/getuser`. Login accepts username/password only in an
HTTPS POST, verifies that returned UID, and discards the password immediately.
No credentials in URLs, logs, source bundles, tool arguments or chat responses.

The browser sees a consent/login page naming the connector, read-only scope and
exact callback destination. Require a browser-bound, short-lived, single-use
transaction and CSRF nonce, exact same-origin POST, no third-party resources,
frame denial, no-store and same-origin referrer headers (no cross-site referrer
disclosure; preserves Origin on native form POSTs). No durable signed-in browser
session is necessary. Apply bounded request sizes and login attempt rate limits.

Allow OAuth clients only with exact operator-configured ChatGPT callback URLs.
Use the SDK's PKCE validation, then atomically consume authorization codes.
Persist client registrations and hashed opaque connector token indexes with
encrypted payloads. Bind grants to client, resource, scopes and the allowed UID.
Access tokens expire within 10 minutes. Each refresh token is single-use and
successful refresh rotates it; replay revokes its entire grant family. Refresh
cannot extend past the upstream TDX token expiry; missing/unknown upstream expiry
fails closed. Revocation invalidates a grant family. Validate the requested
resource at both authorization and token endpoints, including refresh: do not
assume the installed SDK enforces it merely because its request model accepts it.
Upstream tokens go to the existing encrypted vault, never to ChatGPT.

## Deployment and completion gates

Single Railway replica with persistent encrypted state and externally injected
key. Runtime remains non-root after narrow volume initialization. Bundle only
the required source/configuration files, excluding local secrets and ticket data.

Verify synthetic full OAuth flow, CSRF failure, wrong user, wrong resource,
redirect rejection, PKCE failure, code replay, refresh replay, expiry, revocation,
encrypted storage, and existing ticket regressions. Obtain independent security
review before deployment. Then verify HTTPS metadata, denial without credentials
and a real personal TDX login without printing secrets or ticket contents.

ChatGPT's exact callback must come from its app-configuration interface. Do not
guess it or permit wildcard callbacks. ChatGPT registration and in-chat end-to-end
testing remain a separate verified completion gate, not implied by HTTP tests.

## Review

Independent spec review approved the design. Its clarifications on single-use
refresh tokens, replay-family revocation and unknown expiry are incorporated.
