# Locations

A **Location** represents a specific room, floor, shelf, bin, drawer, desk, or inventory closet *within* a parent Site. Locations can be nested hierarchically to represent highly detailed spaces (e.g. `Floor 2` -> `IT Storage Room B` -> `Shelf 3` -> `Bin A`).

Locations in ITAMbox serve as physical staging and storage scopes for tracking custody and stock levels of hardware, accessories, components, and consumables.

---

## Attributes & Fields

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Site** | The parent physical facility containing this location. | Foreign Key | Yes |
| **Name** | The unique name within the parent site (e.g., `IT Storage Room`). | String | Yes |
| **Slug** | URL-safe name representation. | Slug | Yes |
| **Status** | Current state of the location (`Planned`, `Staging`, `Active`, `Decommissioning`, `Retired`). | Choice | Yes |
| **Parent** | A parent location if nested hierarchically. | Foreign Key | No |
| **Tenant** | Optional department or tenant owning or occupying this location. | Foreign Key | No |
| **Facility** | Facility-specific code or suite identifier (e.g. `Room 401`). | String | No |
| **Description** | Optional notes describing how to locate the space or access instructions. | Text | No |

---

## Inventory & Asset Integration
Locations are essential for stock management inside ITAMbox:
- **Bulk Stock**: Accessories (e.g., keyboards) and consumables (e.g., cables) are checked into specific locations where they reside in inventory.
- **Physical Assets**: Physical hardware (e.g., laptops, monitors) can be checked out directly to a staging Location (e.g., *Staging Closet A*) instead of an individual person.
- **Components**: Bulk modular items are staged at specific locations before being allocated to parent systems.
