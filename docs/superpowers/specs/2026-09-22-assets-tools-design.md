# Assets tools for the hosted connector — design notes

Status: implemented 2026-09-22 (reads live-verified; writes awaiting a live test). Written so the work can be
picked up cold. Source of API facts: the tenant's own OpenAPI document
(`https://cedarville.teamdynamix.com/TDWebApi/swagger/v1/openapi.json`, v1);
no live asset call has been made yet.

## Tenant facts

- The owner's account lists application 928, **InfoTech Assets/CIs**, alongside
  634 InfoTech Tickets. The connector currently discovers only the ticket app
  (`Connection._discover_application`, filtered by `AppClass == "TDTickets"` and
  name `InfoTech Tickets`, cached per tenant for an hour).
- Asset application class in the applications list is expected to be
  `TDAssets`; confirm on first live call and pin the name the same way.
- TDNext asset URL shape to verify live before shipping links:
  `https://cedarville.teamdynamix.com/TDNext/Apps/{appId}/Assets/AssetDet?AssetID={id}`.

## Verified API surface (from the spec)

Reads (all authenticated, 60 req/min/IP):

| Operation | Notes |
| --- | --- |
| `POST /api/{appId}/assets/search` (`AssetSearch`) | Server-side filters: `SearchText` (relevance-sorted), `SerialLike` (serial and service tag), `StatusIDs`, `IsInService`, `OwningCustomerIDs`, `UsingCustomerIDs`, `OwningDepartmentIDs`, `UsingDepartmentIDs`, `ProductModelIDs`, `ManufacturerIDs`, `SupplierIDs`, `LocationIDs`, `RoomID`, `ParentIDs`, `OnlyParentAssets`, `TicketIDs`, `ExcludeTicketIDs`, `ContractIDs`, `ExternalIDs`, `FormIDs`, `MaxResults`, and date ranges for acquisition, contract end, created, modified, expected replacement. Results omit `Attributes` and `Attachments`. No total or paging cursor. |
| `GET /api/{appId}/assets/{id}` | Full `Asset` incl. custom `Attributes`, `ConfigurationItemID`, read-only names (`StatusName`, `ProductModelName`, `ManufacturerName`, `SupplierName`, `OwningCustomerName`, `OwningDepartmentName`, `LocationName`, `LocationRoomName`, `ParentName`), `ModifiedDate`. |
| `GET /api/{appId}/assets/{id}/feed` | Feed entries; same shape as ticket feed. |
| `GET /api/{appId}/tickets/{id}/assets` | Configuration items attached to a ticket. |
| `GET /api/{appId}/assets/statuses`, `POST .../assets/models/search` (`ProductModelSearch`), `POST .../assets/vendors/search` (`VendorSearch`) | Metadata for resolving IDs. |
| Ticket search `ConfigurationItemIDs` | "Tickets about this asset": an asset's `ConfigurationItemID` feeds the existing `search_tickets`. |

Writes:

| Operation | Notes |
| --- | --- |
| `POST /api/{appId}/assets/{id}/feed` (`FeedEntry`) | Comment on an asset; 200 returns the created comment. Same body as ticket comments (`Comments`, `IsPrivate`, `IsRichHtml`, `Notify`). |
| `POST /api/{appId}/tickets/{id}/assets/{assetId}` | Link asset to ticket; 200 with a response message. Also mirrored at `POST /assets/{id}/tickets/{ticketId}`. |
| `PATCH /api/{appId}/assets/{id}` (`JsonPatchDocumentOfAsset`) | "Only supports patching the asset itself and custom attributes; other collections are not supported." 200 returns the updated `Asset`. `LocationID`/`LocationRoomID` of -1 clears. |
| `POST /api/{appId}/assets` (create), `DELETE /api/{appId}/assets/{id}` | Deferred: create needs form/attribute contracts (same gap as ticket creation); delete "cannot be undone". |

## Proposed tools

Read, scope `tdx.read`:

1. `search_assets` — one-to-one mapping of the filters above. `owner` and `user`
   accept a name/email/username and resolve through `Connection.resolve_person`
   (stated back in `resolved_people`, never confirmed; ambiguous → candidates and
   no search, exactly like `search_tickets`). Compact projection: `ID`, `Name`,
   `Tag`, `SerialNumber`, `StatusName`, `ProductModelName`, `ManufacturerName`,
   `OwningCustomerName`, `OwningDepartmentName`, `LocationName`,
   `LocationRoomName`, `url`. `complete` = fewer rows than `limit`.
2. `get_asset` — full record with attributes, plus `asset_feed(asset_id, limit)`,
   `asset_tickets(asset_id, limit)` (via `ConfigurationItemIDs` on ticket
   search), `ticket_assets(ticket_id)`.
3. `asset_metadata(kind, search, limit)` — `statuses` (list), `models` and
   `vendors` (server-side search).
4. `search_tickets` gains `asset_id` (resolved to the asset's
   `ConfigurationItemID`).

Write, scope `tdx.write`, through the existing validate → claim → one attempt →
finish pipeline with request-ID deduplication:

5. `add_asset_comment(action{asset_id, comments, is_private, notify}, request_id)`.
6. `link_asset_to_ticket(action{asset_id, ticket_id}, request_id)` — preflight
   verifies both exist and the asset is not already linked (`GET
   tickets/{id}/assets`); an existing link is reported as applied without a call.
7. `edit_asset(action{asset_id, name?, tag?, serial_number?, status_id?,
   owner_uid?, owning_department_id?, location_id?, location_room_id?,
   external_id?, expected_replacement_date?}, request_id)` — JSON Patch
   `replace` per supplied field; preflight snapshots the asset (`ModifiedDate`)
   so a concurrent change conflicts; status verified active; owner verified via
   people API; department via `GET /api/accounts/{id}`; location/room via
   `GET /api/locations/{id}` (endpoint to confirm in spec).

## Store and service changes

- `WriteStore` keys locks and identity on `action.ticket_id`. Generalize to an
  item identity `(domain, id)` where domain is `ticket`, `asset`, or `create`;
  `_ticket_hash`, tombstones, `DirectReplayResult.ticket_id`, audit rows and
  `TicketWriteStatus.ticket_id/ticket_url` need an item-aware equivalent
  (`item_type`, `item_id`, `item_url`). Keep existing field names for ticket
  records so stored rows stay readable; add the new fields with defaults.
- `WriteAdapter` gets asset snapshot/patch/feed methods parallel to the ticket
  ones; `apply_once` dispatches on action kind. Accept 200 (spec) and 201
  (tenant has returned 201 for ticket feeds) for the asset feed.
- Asset app discovery: second cached entry beside the ticket app; `Connection`
  exposes `asset_app_id`. Tools fail closed with a clear message if the person's
  account has no asset application.

## Open questions to settle live before shipping

1. Exact `AppClass` value and application name for assets.
2. Whether `GET /api/locations/{id}` and room lookups exist and are permitted
   for technicians (needed only for `edit_asset` location fields; can ship
   without them).
3. Whether asset feed POST returns 200 or 201 on this tenant.
4. Whether asset PATCH honors `ModifiedDate`-style optimistic checks (it does
   not; conflict detection is our own preflight compare, as with tickets).
5. A safe asset for the first live write test (a comment on a test asset, then
   a harmless field edit such as `ExpectedReplacementDate`).

## Publish implications

Once the app is admin-published in ChatGPT, tool definitions are frozen; every
new asset tool arrives disabled until the admin refreshes Action control. Ship
assets before publishing if possible; otherwise plan the refresh.

## Estimate

Roughly one to two hours of session time for all seven tools including tests,
deploy and live verification; reads are the smaller half.
