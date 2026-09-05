# Sites

A **Site** represents a physical facility, building, office campus, or data center where hardware assets are located or stored. It serves as the primary container for physical geography inside ITAMbox.

## Fields

### Comments

Optional internal comments or access notes.

### Description

Human-readable description of this site.

### Facility

Facility identifier or code associated with the Site.

### Group

The functional or logical site group (e.g. `Corporate Offices`).

### Latitude

Latitude coordinate of the site for mapping.

### Longitude

Longitude coordinate of the site for mapping.

### Name

The site name shown in ITAMbox.

**Required:** Yes.

### Physical Address

The postal or physical address of the site.

### Region

The geographic region where this site belongs (e.g. `Europe`).

### Shipping Address

Dedicated shipping address for hardware deliveries.

### Slug

A unique URL-friendly representation of the name (e.g. `hq-building-a`). Auto-generated if blank.

**Required:** Yes.

### Status

Current operational status of the site (Active, Planned, Retired).

**Required:** Yes.

### Tenant

Optional department or tenant that owns/occupies the site.

### Time Zone

Local timezone identifier (e.g. `America/New_York`) for scheduling audits.


## Relationships

* **Regions**: Every site can belong to a single region.
* **Locations**: Sites contain individual physical locations (e.g. storage rooms, IT desks, inventory closets).
* **Assets**: Serialized systems are assigned to sites and optionally to specific locations within them.

## Use Cases
Sites are crucial for shipping, routing, and calculating inventory levels. For example, stock levels for bulk accessories and consumables are tracked per-location within specific sites.
