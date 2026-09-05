# Metadata Tags

A **Tag** represents a colored keyword label used to apply lightweight, free-form, search-indexed classification to any object in ITAMbox (e.g. `Critical`, `Remote`, `Legacy`, `In-Warranty`).

## Fields

### Color

Hexadecimal color code used to style tags/labels in the UI.

### Description

Optional details explaining when to apply the tag.

### Name

Unique identifier name (e.g. `Critical Infrastructure`).

**Required:** Yes.

### Slug

URL-safe representation.

**Required:** Yes.


## Use Cases
Most models inside ITAMbox inherit from `TaggableMixin` and expose a `tags`
field. Tags support global filtering, grouping, and search across models.
