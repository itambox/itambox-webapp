# Software Licenses

A **Software License** represents a specific purchase agreement, product key, or contract providing a defined number of seats for a software application (e.g. `Volume License Key - Office 2021`, `50x Adobe CC Enterprise Seats`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Software** | The application title from the Software Catalog. | Foreign Key | Yes |
| **Name** | A descriptive name for this license purchase (e.g. `Office 365 E5 seats`). | String | Yes |
| **Seats** | Total number of seats available for allocation. | Integer | Yes |
| **License Key** | The cryptographic or text key needed for registration. | Text | No |
| **Reassignable** | Seat allocations can be removed and returned to the pool for redeployment. | Boolean | Yes |

## Checkout Seat Allocation
* License seats are checked out to `Asset Holders` or physical `Assets` (e.g. assigning a license seat to a developer workstation laptop).
* ITAMbox automatically tracks and displays available, checked out, and total seats.
