# Asset Assignments

An **Asset Assignment** tracks the active checkout custody, historical duration, and return details for physical assets allocated to users, locations, or parent systems.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Asset** | The physical serialized asset being assigned. | Foreign Key | Yes |
| **Assigned Asset** | The assigned asset of the asset assignment. | Foreign Key | No |
| **Assigned Location** | The assigned location of the asset assignment. | Foreign Key | No |
| **Assigned User** | The assigned user of the asset assignment. | Foreign Key | No |
| **Checked In At** | Timestamp of return completion. | DateTime | No |
| **Checked In By** | The administrator who processed the return. | Foreign Key | No |
| **Checked Out At** | Timestamp of transaction activation. | DateTime | Yes |
| **Checked Out By** | The administrative user who authorized the checkout transaction. | Foreign Key | No |
| **Due Date** | Mandatory return date for loaner assets. | Date | No |
| **Expected Checkin Date** | Optional expected return deadline for temporary checkouts. | Date | No |
| **Is Active** | True if custody is current. Closed return actions switch this to False. | Boolean | Yes |
| **Is Loan** | Mark this assignment as a temporary loan with a mandatory return date. | Boolean | Yes |
| **Notes** | Transaction remarks. | Text | No |
| **Pre Checkout Status** | Preserved status label to revert to upon checkin. | Foreign Key | No |
| **Repair Maintenance** | The repair maintenance this loan or handover belongs to, when the assignment is part of a repair story. | Foreign Key | No |
| **Returned At** | Date the loaned asset was physically returned. | Date | No |

## Repair loans

A loan issued for a repair is an ordinary assignment with `Is Loan` set, a due date and a **Repair Maintenance** link. The asset timeline groups it under that repair, on the failed unit's page and on the loaner's. Completing the repair either returns the loaner (checked back in) or keeps it with the holder as a regular assignment (the loan flag and due date are cleared).

## Unique Active Assignment Constraint
To prevent database-level double-allocation failures, a strict unique constraint enforces that an individual `Asset` can have at most one single active `AssetAssignment` record (`is_active=True`).
