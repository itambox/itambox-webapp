# Saved Filters

A **Saved Filter** represents a named, reusable set of list-view query parameters, scoped to a specific Content Type (model).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Content Type** | The target model this filter applies to. | Foreign Key | Yes |
| **Created By** | User who created the filter. | Foreign Key | No |
| **Description** | Explanatory description. | Text | No |
| **Enabled** | Boolean indicating if this filter is active. | Boolean | Yes |
| **Name** | Display name of the filter (e.g. `Critical EOL Systems`). | String | Yes |
| **Parameters** | Stored query dictionary parameters. | JSON | Yes |
| **Shared** | If set, visible to all tenant members. If unset, private to creator. | Boolean | Yes |
| **Tenant** | Scoped tenant (null represents system-wide global filters). | Foreign Key | No |

## Constraints & Usage

* **Uniqueness**: A unique constraint (`unique_savedfilter_name_active`) protects name conflicts per tenant and content type.
* **View Interception**: Generic list views resolve `?filter=<pk>` into a mutable `QueryDict`, applying saved criteria directly to search filtersets.
