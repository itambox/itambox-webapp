# Developer Integration Guide

## Introduction

ITAMbox exposes REST APIs across its application modules and a narrower GraphQL schema for assets, software, licenses, inventory, and subscriptions. Check the generated REST schema and the source-backed GraphQL fields for the revision you deploy; this is a prerelease interface and may change.

- **REST API**: A traditional, resource-oriented API with namespaced routes, pagination, and filtering.
- **GraphQL API**: Queries and mutations for assets, software, licenses, inventory, subscriptions, and registered plugin extensions.

Both APIs enforce the authenticated user's active tenant and permissions. Integrations should use least-privilege service accounts and test against a pinned ITAMbox revision.

## Authentication

ITAMbox uses token-based authentication for API access. All API requests must include a valid token in the HTTP header.

### Token Authentication

- **Header**: `Authorization: Token ***`
- **Example**:

  ```http
  Authorization: Token ***
  ```

### Session Authentication (Fallback)

For users accessing the API via the same browser session as the ITAMbox UI, session authentication is also supported. This is primarily intended for internal integrations or testing.

> **Note**: For production integrations, always use token authentication.

## REST API

The REST API is built with Django REST Framework (DRF) and is accessible under the base path `/api/`. All endpoints are namespace-scoped by module.

### Base Path

```text
https://your-itambox-instance.com/api/
```

### Key Endpoints

| Module            | Endpoint Path                                       | Description                                             |
|-------------------|-----------------------------------------------------|---------------------------------------------------------|
| **API Root**      | `/api/`                                             | API root — lists all available endpoints                |
| **Status**        | `/api/status/`                                      | Deployment status (version, revision info)              |
| **Auth Check**    | `/api/auth-check/`                                  | Authentication verification endpoint                    |
| **Core**          | `/api/core/object-changes/`                         | Audit changelog (ObjectChange records)                  |
| **Assets**        | `/api/assets/assets/`                               | List, create, update, and delete assets                 |
|                   | `/api/assets/asset-roles/`                          | Manage asset roles                                      |
|                   | `/api/assets/asset-assignments/`                    | Manage assignment records and targets                   |
|                   | `/api/assets/manufacturers/`                        | Manage manufacturers                                    |
|                   | `/api/assets/asset-types/`                          | Manage asset types                                      |
|                   | `/api/assets/status-labels/`                        | Manage asset status labels                              |
|                   | `/api/assets/depreciations/`                        | Manage depreciation schedules                           |
|                   | `/api/assets/suppliers/`                            | Manage suppliers                                        |
|                   | `/api/assets/categories/`                           | Manage asset categories                                 |
|                   | `/api/assets/asset-requests/`                       | Manage asset requests                                   |
|                   | `/api/assets/asset-tag-sequences/`                  | Manage asset tag sequences                              |
|                   | `/api/assets/asset-disposals/`                      | Manage asset disposals                                  |
|                   | `/api/assets/warranties/`                           | Manage warranties                                       |
|                   | `/api/assets/asset-reservations/`                   | Manage asset reservations                               |
| **Compliance**    | `/api/compliance/custody-templates/`                | Custody / handover templates                            |
|                   | `/api/compliance/custody-receipts/`                 | Custody receipts                                        |
|                   | `/api/compliance/asset-maintenances/`               | Asset maintenance records                               |
|                   | `/api/compliance/audit-sessions/`                   | Audit sessions                                          |
|                   | `/api/compliance/asset-audits/`                     | Per-asset audit records                                 |
| **Extras**        | `/api/extras/tags/`                                 | Manage tags                                             |
|                   | `/api/extras/dashboards/`                           | Manage dashboards                                       |
|                   | `/api/extras/custom-fields/`                        | Manage custom fields                                    |
|                   | `/api/extras/custom-fieldsets/`                     | Manage custom field sets                                |
|                   | `/api/extras/event-rules/`                          | Manage event rules                                      |
|                   | `/api/extras/webhook-endpoints/`                    | Manage webhook endpoints                                |
|                   | `/api/extras/notification-channels/`                | Manage notification channels                            |
|                   | `/api/extras/alert-rules/`                          | Manage alert rules                                      |
|                   | `/api/extras/journal-entries/`                      | Journal entries                                         |
| **Inventory**     | `/api/inventory/accessories/`                       | Manage accessories                                      |
|                   | `/api/inventory/accessory-stocks/`                  | Accessory stock levels                                  |
|                   | `/api/inventory/accessory-assignments/`             | Accessory assignments                                   |
|                   | `/api/inventory/consumables/`                       | Manage consumables                                      |
|                   | `/api/inventory/consumable-stocks/`                 | Consumable stock levels                                 |
|                   | `/api/inventory/consumable-assignments/`            | Consumable assignments                                  |
|                   | `/api/inventory/kits/`                              | Manage kits                                             |
|                   | `/api/inventory/kit-items/`                         | Kit items                                               |
|                   | `/api/inventory/components/`                        | Manage components                                       |
|                   | `/api/inventory/component-stocks/`                  | Component stock levels                                  |
|                   | `/api/inventory/component-allocations/`             | Component allocations                                   |
| **Licenses**      | `/api/licenses/licenses/`                           | Manage licenses                                         |
|                   | `/api/licenses/assignments/`                        | License seat assignments                                |
| **Organization**  | `/api/organization/sites/`                          | Manage sites                                            |
|                   | `/api/organization/regions/`                        | Manage regions                                          |
|                   | `/api/organization/site-groups/`                    | Manage site groups                                      |
|                   | `/api/organization/locations/`                      | Manage locations                                        |
|                   | `/api/organization/tenants/`                        | Manage tenants                                          |
|                   | `/api/organization/tenant-groups/`                  | Manage tenant groups                                    |
|                   | `/api/organization/cost-centers/`                   | Manage cost centers                                     |
|                   | `/api/organization/asset-holders/`                  | Manage people who can hold assigned assets              |
|                   | `/api/organization/contacts/`                       | Manage contacts                                         |
|                   | `/api/organization/contact-roles/`                  | Manage contact roles                                    |
|                   | `/api/organization/contact-assignments/`            | Manage contact assignments                              |
| **Procurement**   | `/api/procurement/contracts/`                       | Manage contracts                                        |
|                   | `/api/procurement/purchase-orders/`                 | Manage purchase orders                                  |
|                   | `/api/procurement/purchase-order-lines/`            | Purchase order line items                               |
| **Plugins**       | `/api/plugins/...`                                  | Plugin-registered dynamic endpoints                     |
| **Software**      | `/api/software/software/`                           | Software catalog entries                                |
|                   | `/api/software/installed-software/`                 | Installed software records                              |
| **Subscriptions** | `/api/subscriptions/subscriptions/`                 | Manage subscriptions                                    |
|                   | `/api/subscriptions/assignments/`                   | Subscription assignments                                |
|                   | `/api/subscriptions/providers/`                     | Subscription providers                                  |
| **Users**         | `/api/users/users/`                                 | Manage users                                            |
|                   | `/api/users/groups/`                                | Manage groups                                           |
|                   | `/api/users/tokens/`                                | Manage API tokens                                       |
|                   | `/api/users/config/`                                | User configuration                                      |
| **SCIM**          | `/api/tenants/<slug>/scim/v2/`                      | Tenant-scoped SCIM 2.0 provisioning                     |
|                   | `/api/providers/<slug>/scim/v2/`                    | Provider-scoped SCIM 2.0 provisioning                   |

### Pagination

DRF model-viewset list endpoints use limit/offset pagination. Custom endpoints such as SCIM use their own response contracts.

**Request**:

```http
GET /api/assets/assets/?limit=50&offset=50
```

**Response Format**:

```json
{
  "count": 100,
  "count_capped": false,
  "next": "https://itam.example.com/api/assets/assets/?limit=50&offset=100",
  "previous": "https://itam.example.com/api/assets/assets/?limit=50&offset=0",
  "results": [
    { "id": 51 }
  ]
}
```

- `count`: Exact result count unless the configured count cap was reached; `null` in cursor mode.
- `count_capped`: `true` when the real count exceeds `ITAMBOX_PAGINATOR_COUNT_CAP`.
- `next`: URL for the next page (`null` on the last page).
- `previous`: URL for the previous offset page (`null` on the first page and in cursor mode).
- `results`: Array of objects for the current page.

For bulk export or large collections, use keyset pagination with `?limit=50&start=<primary-key>`. `start` and `offset` are mutually exclusive. Cursor responses skip the count, return no previous link, and encode the next `start` value in `next`; follow that URL rather than calculating the cursor yourself.

### Query Filters

You can filter results by appending endpoint-specific query parameters. For example, `assigned_user_id` identifies an `AssetHolder`, not an authentication user:

**Request**:

```http
GET /api/assets/asset-assignments/?assigned_user_id=42&is_active=true
```

**Abridged response**:

```json
{
  "count": 5,
  "count_capped": false,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 101,
      "asset": { "id": 1, "name": "Laptop-001", "asset_tag": "IT-001" },
      "assigned_user": 42,
      "assigned_location": null,
      "assigned_asset": null,
      "assigned_to_type": "assetholder",
      "assigned_to_name": "Jane Doe",
      "is_active": true,
      "checked_out_at": "2024-01-15T10:00:00Z"
    }
  ]
}
```

> **Tip**: Use the OpenAPI schema for the exact filter set of the deployed revision. Asset assignments currently accept `asset_id`, `is_active`, `checked_out_by_id`, and `assigned_user_id`.

## GraphQL API

The GraphQL endpoint is `/graphql/`. POST requests accept the same `Authorization: Token ***` header as REST. GraphiQL and schema introspection are available only with development settings; production disables both.

The built-in schema is list-based rather than Relay connection-based. Asset queries accept `limit`, `offset`, `sortBy`, and explicit filters such as `name`, `assetTag`, `serialNumber`, `statusId`, and `locationId`.

### Query example: retrieve assets

```graphql
query {
  assets(limit: 10, offset: 0, sortBy: "name", statusId: "2") {
    id
    name
    assetTag
    serialNumber
    status {
      id
      name
    }
    location {
      id
      name
    }
  }
}
```

**Response**:

```json
{
  "data": {
    "assets": [
      {
        "id": "1",
        "name": "Laptop-001",
        "assetTag": "IT-001",
        "serialNumber": "SN123456",
        "status": { "id": "2", "name": "In Use" },
        "location": { "id": "3", "name": "Headquarters" }
      }
    ]
  }
}
```

### Mutation example: create an asset

The built-in asset mutations are `createAsset`, `updateAsset`, and `deleteAsset`. Assignment and check-out workflows are not exposed as GraphQL asset mutations.

```graphql
mutation {
  createAsset(
    name: "Laptop-002"
    assetTag: "IT-002"
    serialNumber: "SN123457"
    statusId: "2"
    locationId: "3"
  ) {
    asset {
      id
      name
      assetTag
      serialNumber
    }
  }
}
```

Validation and authorization failures are returned through the standard GraphQL top-level `errors` array; there is no mutation-specific `errors` field in the built-in asset schema.

## Destructive workflow safety

Resource identifiers are model-specific. A user ID, membership ID, AssetHolder ID, and assignment ID are not interchangeable even when two values happen to match in one database. Resolve each target through its own endpoint, verify tenant scope and permissions, and log the exact object IDs before automating check-in, deletion, or deactivation workflows.

---

For REST details, use the OpenAPI schema and Swagger/ReDoc views exposed by the deployed revision. For GraphQL, inspect the schema in a development environment or review the registered schema modules in `core/schema.py`.
