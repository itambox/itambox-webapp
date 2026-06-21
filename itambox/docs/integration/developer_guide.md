# Developer Integration Guide

## Introduction

ITAMbox provides a comprehensive set of APIs for integrating with your IT Asset Management workflows. The platform supports both **REST** and **GraphQL** APIs, allowing you to programmatically manage assets, licenses, compliance, users, and more.

- **REST API**: A traditional, resource-oriented API with namespaced routes, pagination, and filtering.
- **GraphQL API**: A flexible, query-based API for fetching and mutating data with precise control over the response structure.

Both APIs are designed to be secure, performant, and easy to integrate with any modern programming language or tool.

## Authentication

ITAMbox uses token-based authentication for API access. All API requests must include a valid token in the HTTP header.

### Token Authentication

- **Header**: `Authorization: Token <your_api_token>`
- **Example**:
  ```
  Authorization: Token 9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b
  ```

### Session Authentication (Fallback)

For users accessing the API via the same browser session as the ITAMbox UI, session authentication is also supported. This is primarily intended for internal integrations or testing.

> **Note**: For production integrations, always use token authentication.

## REST API

The REST API is built with Django REST Framework (DRF) and is accessible under the base path `/api/`. All endpoints are namespace-scoped by module.

### Base Path

```
https://your-itambox-instance.com/api/
```

### Key Endpoints

| Module        | Endpoint Path                                      | Description                              |
|---------------|----------------------------------------------------|------------------------------------------|
| **Assets**    | `/api/assets/assets/`                              | List, create, update, and delete assets  |
|               | `/api/assets/asset-roles/`                         | Manage asset roles                       |
|               | `/api/assets/asset-assignments/`                   | Manage asset assignments to users        |
| **Compliance**| `/api/compliance/...`                              | Compliance checks and policies           |
| **Inventory** | `/api/inventory/...`                               | Inventory management (components, accessories, consumables, kits) |
| **Licenses**  | `/api/licenses/...`                                | License management                       |
| **Organization**| `/api/organization/sites/`                       | Manage sites                             |
|               | `/api/organization/locations/`                     | Manage locations                         |
|               | `/api/organization/asset-holders/`                 | Manage asset holders (departments, teams)|
| **Software**  | `/api/software/...`                                | Software catalog and entitlements        |
| **Subscriptions**| `/api/subscriptions/...`                         | Subscription management                  |
| **Users**     | `/api/users/...`                                   | User management                          |

### Pagination

All list endpoints return paginated results using DRF's default pagination format.

**Response Format**:
```json
{
  "count": 100,
  "next": "http://localhost:8000/api/assets/assets/?page=2",
  "previous": null,
  "results": [...]
}
```

- `count`: Total number of results.
- `next`: URL for the next page (null if on last page).
- `previous`: URL for the previous page (null if on first page).
- `results`: Array of objects for the current page.

### Query Filters

You can filter results by appending query parameters to the endpoint URL. For example, to retrieve all active asset assignments for a specific user:

**Request**:
```
GET /api/assets/asset-assignments/?assigned_user_id=42&is_active=true
```

**Response**:
```json
{
  "count": 5,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 101,
      "asset": { "id": 1, "name": "Laptop-001" },
      "assigned_user": { "id": 42, "email": "user@example.com" },
      "is_active": true,
      "assigned_date": "2024-01-15T10:00:00Z"
    }
  ]
}
```

> **Tip**: Refer to the specific endpoint's schema for available filter fields. Common filters include `id`, `name`, `is_active`, `created_at`, and foreign key IDs.

## GraphQL API

The GraphQL API is available at the `/graphql` endpoint. It allows you to query and mutate data with a single request, fetching only the fields you need.

### Endpoint

```
POST https://your-itambox-instance.com/graphql
```

### Authentication

Same as REST API: include the `Authorization: Token <your_api_token>` header.

### Query Example: Retrieve Assets

```graphql
query {
  assets(first: 10, isActive: true) {
    edges {
      node {
        id
        name
        assetTag
        serialNumber
        status
        assignedTo {
          email
          fullName
        }
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
    totalCount
  }
}
```

**Response**:
```json
{
  "data": {
    "assets": {
      "edges": [
        {
          "node": {
            "id": "1",
            "name": "Laptop-001",
            "assetTag": "IT-001",
            "serialNumber": "SN123456",
            "status": "ACTIVE",
            "assignedTo": {
              "email": "user@example.com",
              "fullName": "John Doe"
            }
          }
        }
      ],
      "pageInfo": {
        "hasNextPage": false,
        "endCursor": "YXJyYXljb25uZWN0aW9uOjA="
      },
      "totalCount": 1
    }
  }
}
```

### Mutation Example: Check Out an Asset

```graphql
mutation {
  createAssetAssignment(input: {
    assetId: "1",
    assignedUserId: "42",
    notes: "Checked out for project Alpha"
  }) {
    assetAssignment {
      id
      asset {
        name
      }
      assignedUser {
        email
      }
      assignedDate
      isActive
    }
    errors {
      field
      messages
    }
  }
}
```

**Response**:
```json
{
  "data": {
    "createAssetAssignment": {
      "assetAssignment": {
        "id": "101",
        "asset": { "name": "Laptop-001" },
        "assignedUser": { "email": "user@example.com" },
        "assignedDate": "2025-04-08T14:30:00Z",
        "isActive": true
      },
      "errors": null
    }
  }
}
```

## Integration Examples

### Automated User Offboarding Script

ITAMbox provides a Python client-side script (`offboard_user.py`) to automate the offboarding process. This script uses the REST API to:

1. Query all active asset assignments for a user using `assigned_user_id` and `is_active` filters.
2. Check in each assigned asset to reclaim it.
3. Delete the user's asset holder profile to complete the offboarding.

**Usage**:
```bash
# Export the API token and base URL (if not default)
export ITAMBOX_API_TOKEN="your_api_token_here"
export ITAMBOX_BASE_URL="http://localhost:8000"

# Execute the script for user ID 42
python offboard_user.py 42
```

The script is built using only Python's standard libraries (`urllib.request`), making it highly portable and dependency-free.

---

For further details, refer to the complete API documentation or contact ITAMbox support.
