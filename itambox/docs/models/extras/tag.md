# Metadata Tags

A **Tag** represents a colored keyword label used to apply lightweight, free-form, search-indexed classification to any object in ITAMbox (e.g. `Critical`, `Remote`, `Legacy`, `In-Warranty`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Name** | Unique identifier name (e.g. `Critical Infrastructure`). | String | Yes |
| **Slug** | URL-safe representation. | Slug | Yes |
| **Color** | Hexadecimal color code used to style tags/labels in the UI. | Hex Color | No |
| **Description** | Optional details explaining when to apply the tag. | Text | No |

## Use Cases
Most models inside ITAMbox inherit from `TaggableMixin` and sport a `tags` field. This enables powerful tag-based global filtering, grouping, and search queries across heterogenous datasets.
