# Locations

A **Location** represents a specific room, floor, shelf, bin, or inventory closet *within* a parent Site. Locations can be nested hierarchically to represent highly detailed spaces (e.g. `Floor 2` -> `Server Room A` -> `Rack 1`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Site** | The parent physical facility (Site) containing this location. | Foreign Key | Yes |
| **Name** | The unique name within the parent site (e.g., `Suite 400`). | String | Yes |
| **Slug** | URL-safe name representation. | Slug | Yes |
| **Parent** | A parent location if nested hierarchically. | Foreign Key | No |
| **Description** | Optional notes describing how to locate the space. | Text | No |

## Inventory Integration
Locations are essential for stock management inside AssetBox. Bulk items (accessories and consumables) and components are checked into specific locations where they reside in physical inventory.
