# Asset Reservations

An **Asset Reservation** holds or books a physical asset for a specific asset holder within a defined start and end date window.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Asset** | The physical asset being reserved. | Foreign Key | Yes |
| **Created By** | The user who created the reservation. | Foreign Key | No |
| **End Date** | The date the reservation ends. | Date | Yes |
| **Notes** | Optional additional comments or terms. | Text | No |
| **Purpose** | A brief explanation or reason for the reservation. | String | No |
| **Reserved For** | The asset holder who is requesting or assigned the reservation. | Foreign Key | No |
| **Start Date** | The date the reservation begins. | Date | Yes |
| **Status** | Current status of the reservation (e.g., Pending, Active, Completed, Cancelled). | Choice | Yes |

## Features & Validation

* **Double-Booking Prevention**: A database-level exclusion constraint ensures that no two active/pending reservations for the same asset can overlap in time.
* **Date Consistency**: Validates that the `end_date` is on or after the `start_date`.
* **Soft Deletion**: Supports soft-deleting reservations, which automatically releases any booked time windows from active conflicts.
