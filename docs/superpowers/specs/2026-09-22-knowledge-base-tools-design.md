# Knowledge base tools for the hosted connector — design

Status: implemented and live-verified 2026-09-22 (commits 5cc9427..6ec1893). Deviations found live: creation requires exactly one owner; IsPublished/IsPublic are ignored by the API (flags removed); duplicate links return 400/500 (link-state preflight added). See docs/tdx-write-api-contracts.md. Source of API facts: the
tenant's OpenAPI document (`https://cedarville.teamdynamix.com/TDWebApi/swagger/v1/openapi.json`)
plus a live read probe with the analytics service account on 2026-09-22.

## Decisions taken with the owner

- Scope: **full writes** (create, edit, publish and status changes, links, category
  create and edit), but **no deletes** of articles or categories. "Remove this
  article" is a status change to Archived plus unpublish, which TDNext can undo.
- Categories are managed from the connector (create, edit); category delete stays
  in TDNext.
- The pipeline is the existing one: validate → claim (per-item lock) → one attempt →
  finish, request-ID dedup for 30 days, owner-scoped results, `item` projection.

## Tenant facts (live probe)

- One Client Portal application, `AppClass == "TDClient"`, **AppID 2045, "Client
  Portal"**. Every knowledge base route is `/api/2045/...`.
- 24 top-level categories (`GET .../knowledgebase/categories` returns the tree with
  `Subcategories`). Examples: "Accounts and Access" 9212, "Tech FAQ" 10548.
- Search returns `Body` (HTML, thousands of characters) whether or not
  `IncludeArticleBodies` is set; text search ranks archived articles alongside
  approved ones, so status filtering matters.
- Article shape: `ID`, `Subject`, `Summary`, `Body` (HTML), `Tags` (list of
  strings), `CategoryID`/`CategoryName`, `Status` (int) with `StatusName`,
  `DraftStatus` (1 pending, 2 rejected, or null), `IsPublished`, `IsPublic`,
  `RevisionNumber`, `ReviewDateUtc`, `OwnerUid`/`OwnerFullName`,
  `OwningGroupID`/`OwningGroupName`, `Attachments`, `Attributes`,
  `ModifiedDate`, `Uri` (`api/2045/knowledgebase/{id}`).
- `ArticleStatus`: 1 Not Submitted, 2 Submitted, 3 Approved, 4 Rejected,
  5 Archived (0 = no filter, search only).
- `GET .../knowledgebase/{id}/assetscis` hung past 60 s on the first article tried.
  It is excluded; the asset side (`GET /api/{assetApp}/assets/{id}/articles`) is used
  instead.
- Portal URL shape to verify live before shipping links:
  `https://cedarville.teamdynamix.com/TDClient/2045/Portal/KB/ArticleDet?ID={id}`.
  Category: `.../TDClient/2045/Portal/KB/?CategoryID={id}`.

## API surface used

Reads (authenticated, 60 req/min/IP):

| Operation | Notes |
| --- | --- |
| `POST /api/2045/knowledgebase/search` (`ArticleSearch`) | `SearchText`, `CategoryID`, `AuthorUID`, `Status`, `IsPublished`, `IsPublic`, `IncludeShortcuts`, `CustomAttributes`, `ReturnCount` (default 50). Returns `Article[]` with bodies. |
| `GET /api/2045/knowledgebase/{id}` | Current revision of one article. |
| `GET /api/2045/knowledgebase/categories`, `.../categories/{id}` | Tree; single category. |
| `GET /api/2045/knowledgebase/{id}/related` | Related articles. |
| `GET /api/2045/knowledgebase/{id}/relatedservices` | Related services and offerings. |
| `GET /api/{assetApp}/assets/{id}/articles` | Articles linked to an asset. |

Writes:

| Operation | Notes |
| --- | --- |
| `POST /api/2045/knowledgebase` (`Article`) | Create; 201 returns the article. |
| `PATCH /api/2045/knowledgebase/{id}` (`JsonPatchDocumentOfArticle`) | Edit; 200 returns the article. |
| `POST` / `DELETE /api/{assetApp}/assets/{id}/articles/{articleId}` | Link / unlink article and asset. |
| `POST` / `DELETE /api/2045/knowledgebase/{id}/related/{relatedArticleId}` | Link / unlink two articles. |
| `POST /api/2045/knowledgebase/categories` (`ArticleCategory`) | Create category; returns it. |
| `PUT /api/2045/knowledgebase/categories/{id}` (`ArticleCategory`) | Edit category; full object, 200 returns it. |

Not exposed: article delete, category delete, attachments (binary upload),
service/offering links (no service tools exist yet), `assetscis`.

## App discovery

`Connection._discover_application("portal")` selects applications with
`AppClass == "TDClient"`; exactly one must exist (the tenant has one; a name
preference constant `PORTAL_APPLICATION_NAME = "Client Portal"` breaks ties the
way the asset preference does). `Connection.portal_app_id`, `article_url(id)`,
`category_url(id)`. `connection_status` reports `portal_app_id` and name.

## Body handling

- Stored as HTML. Tools accept `body` as HTML, or as plain text (no `<` tags),
  which is wrapped paragraph by paragraph in `<p>`.
- The adapter strips `<script>`, `<iframe>`, `<object>`, `<embed>` and `on*`
  attributes before sending and adds a preview notice when it did.
- Reads return `body_text` (tags stripped with `html.parser`, whitespace
  collapsed). `search_articles` truncates to 400 characters and sets
  `body_truncated`. `get_article(format="html")` returns the raw HTML instead.

## Read tools (scope `tdx.read`)

1. `search_articles(text, category_id, author, status, is_published, is_public,
   include_shortcuts, limit)` — one-to-one with `ArticleSearch`. `status` is a
   name (`not_submitted`, `submitted`, `approved`, `rejected`, `archived`);
   omitted means no filter. `author` resolves through the people API like ticket
   searches and is reflected back in `resolved_people`; ambiguity returns
   candidates and no search. Projection: `ID`, `Subject`, `Summary`,
   `StatusName`, `IsPublished`, `IsPublic`, `CategoryID`, `CategoryName`, `Tags`,
   `OwnerFullName`, `ModifiedDate`, `RevisionNumber`, `body_text`,
   `body_truncated`, `url`. `complete` = fewer rows than `limit`.
2. `get_article(article_id, format="text"|"html")` — the full record with
   `body_text` or `body_html`, attachments (name, size, ID), custom attributes,
   `url`.
3. `article_categories(parent_id=None)` — flattened tree: `ID`, `Name`,
   `ParentID`, `ParentName`, `IsPublic`, `Order`, `depth`, `url`. With
   `parent_id`, the subtree under that category.
4. `related_articles(article_id)` — projection as in 1 without bodies.
5. `article_services(article_id)` — related services and offerings: ID, name,
   type, `IsActive`.
6. `asset_articles(asset_id)` — articles linked to an asset (asset app).

## Write tools (scope `tdx.write`)

All go through `_submit`; each takes `request_id`. Actions live in
`ticket_writes/models.py` beside the asset actions:

| Tool | Action kind | Item / lock | Payload |
| --- | --- | --- | --- |
| `create_article(article, request_id)` | `article_create` | `("create", 0)`: identical payloads serialize, as ticket creation does | `Article` with `Subject`, `Body`, `CategoryID`; optional `Summary`, `Tags`, `OwnerUid` (from `owner` name/email via people API, or `owner_uid`), `OwningGroupID`, `ReviewDateUtc`, `IsPublic`, `IsPublished`, `Status`, `NotifyOwner`, `NotifyOwnerOfReviewDate`, `Order`. Defaults: `Status` 1 Not Submitted, `IsPublished` false, `IsPublic` false. |
| `edit_article(action, request_id)` | `article_edit` | `("article", id)` | JSON Patch `replace` on `Subject`, `Summary`, `Body`, `Tags`, `CategoryID`, `OwnerUid`, `OwningGroupID`, `ReviewDateUtc`, `IsPublic`, `IsPublished`, `Status` (by name), `NotifyOwner`, `NotifyOwnerOfReviewDate`, `Order`. Baseline: `ModifiedDate` + `RevisionNumber`; a differing baseline at apply time is a conflict. |
| `link_article(action, request_id)` | `article_link` | `("article", id)` | Exactly one of `asset_id` or `related_article_id`. |
| `unlink_article(action, request_id)` | `article_unlink` | `("article", id)` | Same targets. |
| `create_article_category(category, request_id)` | `category_create` | `("create", 0)`, same rule | `Name`; optional `Description`, `ParentID`, `Order`, `IsPublic`, `InheritPermissions`. |
| `edit_article_category(action, request_id)` | `category_edit` | `("category", id)` | Fetch, apply changes to `Name`, `Description`, `ParentID`, `Order`, `IsPublic`, then `PUT` the full object. Baseline `ModifiedDate`. |

Validation (adapter): article exists and is in app 2045; category exists;
owner UID resolves to a person; status name maps to an integer; for links, the
asset (asset app) or related article exists; a link to itself is rejected.
Preview notices: "publishes the article in the portal" when `IsPublished`
flips to true, "makes the article visible without signing in" when `IsPublic`
flips to true, "archives the article" when status becomes Archived, "script or
frame markup was removed from the body" when the sanitizer changed anything.

Apply classification: 201 create (article or category) with `ID` and `AppID`
verified; 200 edit/PUT verified by `ID`; link 200 applied, 204 or a "already"
message treated as applied ("already linked"); unlink 200 applied, 404 applied
("was not linked"); 4xx other than 408 rejected; anything else unknown.
Results carry `item = {"type": "article"|"category", "id", "url"}` and
`detail` with `status`, `is_published`, `is_public`, `revision`.

## Store

Item identity generalises as it did for assets: `_ticket_hash` locks per
`(domain, id)` for `article` and `category`; equivalence includes the item.
Creation actions reuse the existing `create` domain, which locks on an identical
payload, so two different articles can be created concurrently while a repeated
identical creation waits. No store schema change.

## Tool count

29 → 42 (6 reads, 7 writes). Descriptions stay one sentence plus the
must-know rules, so the model can pick between `edit_article` and
`edit_article_category` without reading paragraphs.

## Testing

- Unit (TDD): models (parsing, defaults, one-of constraints), adapter
  (validation, payloads, sanitiser, previews, status classification), store
  (item locks for `article`/`category`), service (`_item` URLs), tools
  (registration, hardening, redaction), plugin reads (projections, people
  resolution, tree flattening, body stripping).
- Live, with the persistent test client on production: create a scratch
  category under "Tech FAQ"; create a draft article in it; `get_article` in both
  formats; `edit_article` (summary, tags); publish then unpublish; link and unlink
  to asset 1973209; `related_articles`; move the article to Archived. The scratch
  category and archived article are left for the owner to delete in TDNext.
- Verify the portal URL shape on the first live article.

## Out of scope

Article and category deletion, attachments, service/offering links, shortcuts
creation, article feedback, revision history, permissions management, a widget.
