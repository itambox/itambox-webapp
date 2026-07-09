# Site Groups

A **Site Group** provides a way to logically cluster sites under a flat or hierarchical category, separate from geographic regions. For example, sites can be grouped by facility type: `Data Centers`, `Branch Offices`, or `Retail Stores`.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Description** | Optional descriptive comments. | Text | No |
| **Name** | Unique group name (e.g., `Branch Offices`). | String | Yes |
| **Parent** | Hierarchical parent site group. | Foreign Key | No |
| **Slug** | URL-safe name representation. | Slug | Yes |

## Use Cases
Site Groups allow you to manage and filter assets by the functional operational environment of their sites, rather than strictly by geography.
