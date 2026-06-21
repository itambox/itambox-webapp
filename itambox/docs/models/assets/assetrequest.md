# Asset Requests

An **Asset Request** represents a self-service requisition ticket submitted by a user for a physical asset or hardware category (e.g. asking for a laptop or monitor replacement).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Accessory** | The accessory of the asset request. | Foreign Key | No |
| **Asset** | The specific physical asset requested (e.g. `ASSET-001054`). | Foreign Key | No |
| **Asset Type** | The catalog model template requested (e.g. `MacBook Pro 16"`). | Foreign Key | No |
| **Assigned Asset** | The assigned asset of the asset request. | Foreign Key | No |
| **Assigned Location** | The assigned location of the asset request. | Foreign Key | No |
| **Assigned User** | The assigned user of the asset request. | Foreign Key | No |
| **Component** | The component of the asset request. | Foreign Key | No |
| **Consumable** | The consumable of the asset request. | Foreign Key | No |
| **Is Group** | The is group of the asset request. | Boolean | Yes |
| **Notes** | Justification or requirements added by the requester. | Text | No |
| **Parent** | The parent of the asset request. | Foreign Key | No |
| **Qty** | The quantity of the asset request. | Integer | Yes |
| **Request Date** | Timestamp of submission. | DateTime | Yes |
| **Requester** | The Django User who initiated the request. | Foreign Key | Yes |
| **Responded By** | The responded by of the asset request. | Foreign Key | No |
| **Response Date** | The response date of the asset request. | Date Time | No |
| **Response Notes** | Feedback supplied by the responding administrator. | Text | No |
| **Source Location** | The source location of the asset request. | Foreign Key | No |
| **Status** | Requisition status: `Pending`, `Approved`, `Denied`, `Fulfilled`, `Cancelled`. | Choice | Yes |
| **Tenant** | The tenant of the asset request. | Foreign Key | No |

## Validation Gating
* Either a specific `Asset` or an `Asset Type` must be declared.
* The requested items must be marked as `requestable` in their settings to prevent requests on restricted assets (e.g. critical infrastructure servers).
