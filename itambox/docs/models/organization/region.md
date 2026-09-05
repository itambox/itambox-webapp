# Regions

A **Region** represents a geographic grouping of sites. Regions are hierarchical, meaning a region can have a parent region (e.g. `US-East` is a child of `United States`).

## Fields

### Description

Optional functional details about the regional boundary.

### Name

Unique regional name (e.g., `North America`).

**Required:** Yes.

### Parent

The parent region in the hierarchy for nested grouping.

### Slug

URL-safe name representation. Auto-generated if left blank.

**Required:** Yes.


## Use Cases
Regions allow organizations with globally distributed assets to perform region-based filtering and reporting, such as tracking all workstations assigned to users located within `EMEA`.
