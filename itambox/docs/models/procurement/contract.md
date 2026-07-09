# Contracts

A **Contract** represents a service agreement, hardware/software support agreement, SLA, lease, or warranty contract. It links commercial parameters with physical assets covered under the agreement.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Covered Assets** | Many-to-many list of assets covered under this contract. | Many to Many | No |
| **Auto-Renew** | Toggles whether the contract renews automatically. | Boolean | Yes |
| **Billing Cycle** | Cadence: `monthly`, `quarterly`, `annual`, `biannual`, `multi_year`, `onetime`. | Choice | Yes |
| **Contract Number** | Unique contract identifier. | String | Yes |
| **Contract Type** | Type of contract: `support`, `maintenance`, `lease`, `warranty`, `service`, `other`. | Choice | Yes |
| **Cost** | Billing cost amount. | Decimal | No |
| **Cost Center** | Scopes financial cost allocation. | Foreign Key | No |
| **Coverage Hours** | SLA support coverage (e.g. `24x7`). | String | No |
| **Currency** | Currency of the contract cost. | Currency | Yes |
| **End Date** | Contract expiration date. | Date | Yes |
| **Name** | Display name of the contract (e.g. `Laptop Lease Q3`). | String | Yes |
| **Notes** | Optional comments. | Text | No |
| **Purchase Order** | Optional linked Purchase Order. | Foreign Key | No |
| **Renewal Date** | Scheduled renewal window check date. | Date | No |
| **SLA Resolution Time** | SLA resolution metric (e.g. `1 business day`). | String | No |
| **SLA Response Time** | SLA response metric (e.g. `4 business hours`). | String | No |
| **SLA Terms** | Summary or full text of SLA rules. | Text | No |
| **Start Date** | Contract activation date. | Date | Yes |
| **Status** | Lifecycle state: `draft`, `active`, `expired`, `cancelled`. | Choice | Yes |
| **Supplier** | The vendor providing the contract. | Foreign Key | No |
| **Tenant** | Optional tenant scope. | Foreign Key | No |

## Constraints & Properties

* **Unique Contract Number**: Unique across active contracts (soft-delete-aware).
* **Date Validation Constraint**: Enforces `end_date >= start_date` at the database level.
* **Days Until Expiry**: Calculated calendar days remaining before `end_date`.
* **Is Expiring Soon**: Boolean flag returning `True` when the contract expires within 30 days.
