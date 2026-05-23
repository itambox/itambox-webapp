# Software Licenses

A **Software License** represents a specific purchase agreement, product key, or contract providing a defined number of seats for a software application (e.g. `Volume License Key - Office 2021`, `50x Adobe CC Enterprise Seats`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Software** | The application title from the Software Catalog. | Foreign Key | Yes |
| **Name** | A descriptive name for this license purchase (e.g. `Office 365 E5 seats`). | String | Yes |
| **License Type** | The type of entitlement (e.g. perpetual seat, subscription seat). | Choice | Yes |
| **Product Key** | The cryptographic activation code (stored symmetrically encrypted in DB). | Text | No |
| **Seats** | Total number of seats available for allocation. | Integer | Yes |
| **Purchase Date** | Date of acquisition. | Date | No |
| **Purchase Cost** | Total cost of license purchase. | Decimal | No |
| **Order Number** | Associated purchase order or invoice reference. | String | No |
| **Expiration Date**| Expiration date for term licenses or software maintenance. | Date | No |
| **Supplier** | The vendor or supplier of this license. | Foreign Key | No |
| **Tenant** | Cost center tenant owning this license. | Foreign Key | No |
| **Notes** | Optional internal notes regarding this purchase. | Text | No |

## Checkout Seat Allocation
* License seats are checked out to `Asset Holders` or physical `Assets` (e.g. assigning a license seat to a developer workstation laptop).
* ITAMbox automatically tracks and displays available, checked out, and total seats.
