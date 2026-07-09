# Locations

A **Location** represents a specific room, floor, desk, server rack, or storage closet within a parent **Site**. It allows organizations to track exactly where a physical asset or bulk inventory stock is stored or deployed.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Description** | Detailed description of the location or access constraints. | Text | No |
| **Facility** | Room number, floor number, suite, or building wing. | String | No |
| **Name** | The unique, friendly name of the location (e.g. `Server Room 3B`, `IT Storage Closet`). | String | Yes |
| **Parent** | Nested location support, allowing hierarchical structures (e.g., `Rack 12` nested inside `Server Room 3B`). | Foreign Key | No |
| **Site** | The parent facility or building containing this location. | Foreign Key | Yes |
| **Slug** | A unique URL-friendly representation of the name (e.g. `server-room-3b`). Auto-generated if blank. | Slug | Yes |
| **Status** | Current operational status of the location (Active, Planned, Retired). | Choice | Yes |
| **Tenant** | Optional tenant context that owns or occupies this location. | Foreign Key | No |

## Relationships

* **Sites**: Every location belongs to a single parent Site.
* **Hierarchical Spacing (Parent/Child)**: Locations can be nested to construct hierarchical spatial layouts (e.g. `HQ Building A` -> `Floor 3` -> `Room 302` -> `Cabinet C` -> `Shelf 2`).
* **Assets & Inventory**: Hardware assets, accessories, and consumables are assigned to locations for precise tracking of storage or deployment state.

## Use Cases

Locations are the building blocks of physical asset custody. By dividing a site into locations, operators can:
* Pinpoint specific asset assignments (e.g. assigning a test server to `Rack 12` in the server room).
* Manage inventory storage levels (e.g. verifying that `IT Storage Closet B` has sufficient stock of keyboards or consumable batteries).
* Delegate room/wing occupancy contexts to specific tenant teams.
