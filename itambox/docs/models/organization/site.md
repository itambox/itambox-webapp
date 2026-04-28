# Sites

A **Site** represents a physical facility, building, office campus, or data center where hardware assets are located or stored. It serves as the primary container for physical geography inside ITAMbox.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Name** | The unique, friendly name of the facility (e.g. `HQ Building A`). | String | Yes |
| **Slug** | A unique URL-friendly representation of the name (e.g. `hq-building-a`). Auto-generated if blank. | Slug | Yes |
| **Region** | The geographic region where this site belongs (e.g. `Europe`). | Foreign Key | No |
| **Group** | The functional or logical site group (e.g. `Corporate Offices`). | Foreign Key | No |
| **Status** | Current operational status of the site (Active, Planned, Retired). | Choice | Yes |
| **Physical Address** | The full mailing address of the facility. | Text | No |
| **Tenant** | Optional department or tenant that owns/occupies the site. | Foreign Key | No |

## Relationships

* **Regions**: Every site can belong to a single region.
* **Locations**: Sites contain individual physical locations (e.g. server rooms, inventory closets).
* **Assets**: Serialized systems are assigned to sites and optionally to specific locations within them.

## Use Cases
Sites are crucial for shipping, routing, and calculating inventory levels. For example, stock levels for bulk accessories and consumables are tracked per-location within specific sites.
