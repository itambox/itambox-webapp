# Software Licenses

A **Software License** represents a specific purchase agreement, product key, or contract providing a defined number of seats for a software application (e.g. `Volume License Key - Office 2021`, `50x Adobe CC Enterprise Seats`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Cost Center** | The cost center of the license. | Foreign Key | No |
| **Currency** | ISO 4217 code. Leave blank to use the tenant default currency. | Choice | No |
| **Expiration Date** | Expiration date for term licenses or software maintenance. | Date | No |
| **License Type** | The type of entitlement (e.g. perpetual seat, subscription seat). | Choice | Yes |
| **Name** | A descriptive name for this license purchase (e.g. `Office 365 E5 seats`). | String | Yes |
| **Notes** | Optional internal notes regarding this purchase. | Text | No |
| **Order Number** | Associated purchase order or invoice reference. | String | No |
| **Product Key** | The cryptographic activation code (stored symmetrically encrypted in DB). | Text | No |
| **Purchase Cost** | Total cost of license purchase. | Decimal | No |
| **Purchase Date** | Date of acquisition. | Date | No |
| **Seats** | Total number of seats available for allocation. | Integer | Yes |
| **Software** | The application title from the Software Catalog. | Foreign Key | Yes |
| **Subscription** | Optional subscription (billing agreement) that funds this license; seats roll up to it. | Foreign Key | No |
| **Supplier** | The vendor or supplier of this license. | Foreign Key | No |
| **Tenant** | Cost center tenant owning this license. | Foreign Key | No |
| **Version** | Optional version constraint for this license entitlement (e.g. '2021', '16.x'). Informational only — reconciliation is performed at the Software level (version-agnostic). | String | No |

## Checkout Seat Allocation
* License seats are checked out to `Asset Holders` or physical `Assets` (e.g. assigning a license seat to a developer workstation laptop).
* ITAMbox automatically tracks and displays available, checked out, and total seats.
