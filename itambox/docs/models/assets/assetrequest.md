# Asset Requests

An **Asset Request** represents a self-service requisition ticket submitted by a user for a physical asset or hardware category (e.g. asking for a laptop or monitor replacement).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Requester** | The Django User who initiated the request. | Foreign Key | Yes |
| **Asset** | The specific physical asset requested (e.g. `ASSET-001054`). | Foreign Key | No |
| **Asset Type** | The catalog model template requested (e.g. `MacBook Pro 16"`). | Foreign Key | No |
| **Status** | Requisition status: `Pending`, `Approved`, `Denied`, `Fulfilled`, `Cancelled`. | Choice | Yes |
| **Request Date** | Timestamp of submission. | DateTime | Yes |
| **Notes** | Justification or requirements added by the requester. | Text | No |
| **Response Notes** | Feedback supplied by the responding administrator. | Text | No |

## Validation Gating
* Either a specific `Asset` or an `Asset Type` must be declared.
* The requested items must be marked as `requestable` in their settings to prevent requests on restricted assets (e.g. critical infrastructure servers).
