# Asset Assignments

An **Asset Assignment** tracks the active checkout custody, historical duration, and return details for physical assets allocated to users, locations, or parent systems.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Asset** | The physical serialized asset being assigned. | Foreign Key | Yes |
| **Assigned To (Polymorphic)** | The destination recipient (AssetHolder, Location, or another parent Asset) using a Generic Foreign Key. | GFK | Yes |
| **Checked Out By** | The administrative user who authorized the checkout transaction. | Foreign Key | No |
| **Checked Out At** | Timestamp of transaction activation. | DateTime | Yes |
| **Expected Checkin Date** | Optional expected return deadline for temporary checkouts. | Date | No |
| **Is Active** | True if custody is current. Closed return actions switch this to False. | Boolean | Yes |
| **Checked In At** | Timestamp of return completion. | DateTime | No |
| **Checked In By** | The administrator who processed the return. | Foreign Key | No |
| **Notes** | Transaction remarks. | Text | No |

## Unique Active Assignment Constraint
To prevent database-level double-allocation failures, a strict unique constraint enforces that an individual `Asset` can have at most one single active `AssetAssignment` record (`is_active=True`).
