# Asset Roles

An **Asset Role** categorizes assets based on their operational or functional purpose in your enterprise (e.g. `Developer Laptop`, `Database Server`, `Conference Room Console`, `Network Switch`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Allows Components** | Assets with this role can have components allocated (servers, workstations, …) | Boolean | Yes |
| **Color** | Hexadecimal color code used to style tags/labels in the UI. | Hex Color | No |
| **Description** | Optional notes detailing what hardware fits this role. | Text | No |
| **Name** | Unique name of the role (e.g., `Virtualization Host`). | String | Yes |
| **Slug** | URL-safe name representation. | Slug | Yes |

## Use Cases
Asset Roles are separate from the hardware model specification. They allow you to apply consistent classification tags, organize software configurations, and query systems based on their enterprise duties.
