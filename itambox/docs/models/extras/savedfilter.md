# Saved Filters

A **Saved Filter** represents a named, reusable set of list-view query parameters, scoped to a specific Content Type (model).

## Fields

### Content Type

The target model this filter applies to.

**Required:** Yes.

### Created By

User who created the filter.

### Description

Explanatory description.

### Enabled

Boolean indicating if this filter is active.

**Required:** Yes.

### Name

Display name of the filter (e.g. `Critical EOL Systems`).

**Required:** Yes.

### Parameters

Stored query dictionary parameters.

**Required:** Yes.

### Shared

If set, visible to all tenant members. If unset, private to creator.

**Required:** Yes.

### Tenant

Scoped tenant (null represents system-wide global filters).


## Constraints & Usage

* **Unique names**: Active saved-filter names are kept unique for the relevant tenant and object type.
* **View Interception**: Generic list views resolve `?filter=<pk>` into a mutable `QueryDict`, applying saved criteria directly to search filtersets.
