# Publishing Dynamix to the Cedarville ChatGPT Edu workspace

Record of the publish done on 2026-09-23 from the workspace admin account, plus
the steps to repeat it (for example after a rename or a tool change). Publishing
makes the app available to chosen roles with no developer mode on their side.

## What is published

| Item | Value |
| --- | --- |
| Name | Dynamix (the word users say in chat; the MCP server also identifies itself as Dynamix) |
| App ID | `asdk_app_6ab3d9c95e0c8191840d7e7a41ce79a8` |
| MCP server URL | `https://connector-production-a492.up.railway.app/mcp` |
| Authentication | OAuth, Dynamic Client Registration (auto-discovered); scopes `tdx.read`, `tdx.write` |
| Tools at publish | 64 (all enabled; "Enable all new tools" left on) |
| Access | Role **Advanced Model user** only. Workspace default and every other role off. |
| App permission | **Allow read tools**: ChatGPT reads without asking and asks before writes |
| Hosting | Railway, CU DevOps Playground workspace, project `teamdynamix-connector` |
| Who can sign in | Anyone in an enabled role with a TeamDynamix account, using their own credentials (password, or an SSO token they paste). TDX permissions govern what they see and change. Publishing shares the app definition, never the admin's connection. |

## Steps (what actually worked)

1. **Admin, Apps, Create.** Accept the "Enable developer mode" dialog if it
   appears (it is an account setting for the admin, not a workspace change).
   Fill Name `Dynamix`, a description, the MCP URL, keep OAuth. The advanced
   OAuth panel should show DCR discovered with both scopes. Tick the risk
   acknowledgement and Create. **There is no "Scan tools" button**; the app is
   created with an empty tool list.
2. **Connect once as the admin to populate the tools.** Plugins (user side),
   search Dynamix, the "+" or Manage, Connect another account. ChatGPT redirects
   to the connector sign-in page. This first connection requests **read-only**
   (`tdx.read`) because ChatGPT has not seen the tools yet; sign in anyway.
   Afterwards the Manage panel lists Read tools and Write tools, and a second
   connection requests read and write (verified on the sign-in page text).
   Optional: connect again so the admin's own connection has write scope.
3. **Publish.** Manage panel, "…" menu, Publish (this opens a new tab; the
   Browser pane only allows that from a human click). In the dialog:
   - Configure access: **Deselect all**, then tick the roles that should see it.
     The default is every role, which is the whole workspace.
   - Configure tools: all tools enabled.
   - Review the two potential-risk items, then Publish.
4. **Set the app permission.** Admin, Plugins, catalog tab, Dynamix:
   Permissions, **Allow read tools**. Confirm Role access shows only the intended
   roles and Tools shows "All enabled".

## Things that went wrong and how to avoid them

- **A draft created without the connect step is stuck.** It shows "No tools are
  available", the connect dialog says "Dynamix is disabled for your workspace",
  and Create then fails with 409 "Connector with name 'Dynamix' already exists".
  Such a draft lives under the admin's *Personal* plugins, not in Admin, Apps.
  Delete it there (Manage, "…", Delete) before creating again.
- **Admin pages that spin or say "Unable to load role overrides" / "Failed to
  load subscription"** are the admin session needing a fresh OpenAI login (the
  Permissions page triggers it). Complete it and reload.
- Do not scroll inside the role-toggle dialog with the pointer over the
  toggles; if Save becomes active unexpectedly, Cancel and discard.

## After publishing

- **Frozen tool snapshot.** ChatGPT records the tool definitions at publish time.
  After the connector ships a new or changed tool, open Admin, Plugins, Dynamix,
  Tools, Refresh, review the diff, and enable new actions (with "Enable all new
  tools" on they arrive enabled).
- **Widening access.** Admin, Plugins, Dynamix, Role access: toggle roles on and
  Save. Workspace default stays off until the owner decides otherwise.
- **Sign-in options.** Password login stores a password only if the person opts in
  ("Keep me connected", off by default) and gives a 90-day grant. SSO token paste
  stores no password and lasts 24 hours.
- **Revoking a person.** Remove them from the enabled role; their grant stops at
  the next token refresh (at most 10 minutes).

## Verification after publish

1. As a member of Advanced Model user (not the admin): Plugins shows Dynamix
   under Cedarville University ChatGPT Edu; Add; sign in; ask for
   `connection_status`.
2. Ask for "my open tickets" and "tickets requested by <a colleague>": the reply
   should name the resolved person's email without asking.
3. Ask ChatGPT to add a private comment to a test ticket; it should ask once
   (app permission), then report the outcome and ticket URL.
