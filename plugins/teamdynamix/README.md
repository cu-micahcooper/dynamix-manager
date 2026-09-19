# TeamDynamix plugin

Local Codex plugin for the existing dynamix-manager project. Provides eight read-only
MCP tools and an MCP Apps ticket-view resource. Credentials remain in the project's
`.env`; neither the plugin package nor tool outputs contain secrets.

The separate hosted ChatGPT personal pilot uses Railway and explicit user consent;
see [hosted connector documentation](../../docs/hosted-connector.md). The tunnel
instructions below are an alternative for this local read-only package, not a
requirement for the hosted pilot.

Install dependencies from the project root: `.venv/bin/python3.14 -m pip install -e '.[plugin]'`.
The `.mcp.json` launcher is intentionally bound to this Mac's project path and Python.
Update it for another checkout. The plugin depends on this project; it is not a
standalone distributable server.

The configured TDX_APP_ID is an API client header, not necessarily the ticketing
application ID. The connector discovers InfoTech Tickets using the applications API.
Saved WORKBENCH_PERSONAL credentials take precedence when present; otherwise it uses
the existing analytics credentials. Admin authentication supports search and reads,
but cannot identify a personal queue.

Run the read-only smoke test:

```sh
.venv/bin/python3.14 -m dynamix_manager.plugin --check
```

Ticket search is bounded and the tenant API has no total/paging cursor. Feed replies
are not expanded. Survey rows are raw, not automatically filtered to InfoTech Tickets.
No create, update, notification, or destructive tools are exposed.

## App registration

The server includes `ui://teamdynamix/tickets-v2.html`. Select a ticket title to read
its description, requester, assignment, dates, custom fields and activity inside the
app. Back returns to the existing results. The MCP Apps bridge calls get_ticket and
ticket_feed directly, with a ChatGPT callTool compatibility fallback. Failed requests
remain retryable, and late responses cannot replace a newer selection.

Run `python3 scripts/preview_teamdynamix_plugin.py` and visit http://127.0.0.1:8769
for a synthetic browser preview. It serves only two allowlisted files, no real data.

For private ChatGPT use, create a Secure MCP Tunnel in Platform tunnel settings,
associate it with the target ChatGPT workspace, and run the official tunnel-client
against this project's stdio launcher. This does not require public hosting. Setup
requires Tunnels Read + Manage to create the tunnel, Read + Use to run it, and
developer-mode access in the target ChatGPT workspace. Keep access personal: this
server uses one configured TeamDynamix identity, not per-ChatGPT-user authentication.

After installing the official tunnel-client, configure it with the real tunnel ID
and a runtime key via CONTROL_PLANE_API_KEY (never store the key in this plugin):

```sh
tunnel-client init --sample sample_mcp_stdio_local --profile teamdynamix \
  --tunnel-id YOUR_TUNNEL_ID \
  --mcp-command '/Users/micahcooper/dynamix-manager/.venv/bin/python3.14 -m dynamix_manager.plugin --project-root /Users/micahcooper/dynamix-manager'
tunnel-client doctor --profile teamdynamix --explain
tunnel-client run --profile teamdynamix
```

Then create the app at https://chatgpt.com/plugins, select Tunnel, and select the
workspace-associated tunnel. Add its real app ID to .app.json only after registration.
Refresh the connection after metadata changes. Test search, ticket selection, activity,
and Back in ChatGPT; a local preview does not prove registration or host compatibility.
Official setup: https://developers.openai.com/api/docs/guides/secure-mcp-tunnels.

This is not yet an OpenAI-registered app. A `.app.json` needs a real registered
`plugin_asdk_app...` ID; generating that file with a fictional ID would not connect
anything. Cross-product registration requires an authenticated remote MCP endpoint
or a supported private tunnel, followed by registration in ChatGPT Plugins. Do not
expose this local credential-backed server on a public unauthenticated endpoint.

References: https://developers.openai.com/plugins/build/plugins and
https://developers.openai.com/plugins/build/mcp-server.
