# Publishing the TeamDynamix app to the Cedarville ChatGPT Edu workspace

A checklist for the workspace admin/owner account. Publishing makes the app
available to chosen members with no developer mode on their side. Estimated
time: 15 minutes.

## What you are publishing

| Item | Value |
| --- | --- |
| Name | TeamDynamix |
| MCP server URL | `https://connector-production-a492.up.railway.app/mcp` |
| Authentication | OAuth, Dynamic Client Registration (auto-discovered); default scopes `tdx.read` and `tdx.write` |
| Hosting | Railway, CU DevOps Playground workspace, project `teamdynamix-connector` |
| Who can sign in | Anyone with a TeamDynamix account, using their own credentials (password, or an SSO token they paste). What each person can see or change is governed by their TeamDynamix permissions. |
| Data at rest | Encrypted per-person TDX token; encrypted password only if the person ticks "Keep me connected" |

## Steps

1. **Find or create the draft.** Workspace settings → Apps → Drafts. The draft
   `TeamDynamix` created by micahcooper@cedarville.edu should be listed. If it
   is not: Apps → Create, paste the URL above, keep OAuth, click Scan Tools,
   complete the sign-in prompt (either login option works), then Create.
2. **Publish.** In Drafts, click Publish. Before confirming:
   - **Configure Access** → select the group(s) below. Do not leave it
     workspace-wide.
   - **Configure Actions** → enable the actions below. Write actions carry a
     risk warning; enabling them is the intended state.
3. **Set the app permission.** On the published app's plugin detail page, set
   Permissions to **Allow read actions**. ChatGPT then reads without prompting
   and asks before any write, which matches the connector's own rule that every
   write needs an explicit request in the conversation.
4. **Tell the first users.** They add TeamDynamix from Plugins → Cedarville
   University ChatGPT Edu, click Add, and sign in on the connector page.

## Access group

Start with: ________________________________ (recommended: the IT staff group
that works InfoTech Tickets). Add groups later from Workspace settings → Apps →
(…) → Configure Access.

## Actions to enable

Read (8, safe to enable all): `connection_status`, `ticket_statuses`,
`search_tickets`, `my_queue`, `get_ticket`, `ticket_feed`, `survey_report`,
`days_off`.

Read, supporting writes (4): `ticket_write_metadata`, `list_ticket_tasks`,
`ticket_create_metadata`, `ticket_write_result`.

Write (6): `add_ticket_comment`, `update_ticket_status`, `assign_ticket`,
`edit_ticket`, `complete_ticket_task`, `create_ticket`. Each is one attempt,
deduplicated by request ID, and only runs on an explicit user request.

## After publishing: keep in mind

- **Frozen tool snapshot.** ChatGPT records the tool definitions at publish
  time. When the connector ships a new or changed tool, go to Workspace
  settings → Apps → (…) → Action control → Refresh, review the diff, and
  enable new actions (they arrive disabled). Until then, new tools are
  invisible and incompatible changes error.
- **Sign-in options.** Option A stores a password only if the person opts in
  ("Keep me connected", off by default) and gives a 90-day grant. Option B
  (paste an SSO token from `…/TDWebApi/api/auth/loginsso`) stores no password
  and lasts 24 hours.
- **Revoking a person.** Remove them from the access group; their existing
  grant stops working at the next token refresh (at most 10 minutes).

## Verification after publish

1. As a member (not the admin account): Plugins → Cedarville University
   ChatGPT Edu shows TeamDynamix; Add; sign in; ask for `connection_status`.
2. Ask for "my open tickets" and for "tickets requested by <a colleague>":
   the reply should name the resolved person's email without asking.
3. Ask ChatGPT to add a private comment to a test ticket; it should ask for
   confirmation once (app permission), then report the outcome and ticket URL.
