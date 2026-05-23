# SaaS Subscriptions

A **SaaS Subscription** represents a recurring subscription contract for SaaS platforms, tools, or cloud resources (e.g. `Figma Professional Plan`, `GitHub Enterprise Cloud`, `AWS Organization Account`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Provider** | The SaaS Provider hosting the platform (e.g., `GitHub`). | Foreign Key | Yes |
| **Name** | A clear name identifying the contract plan (e.g. `Dev DevOps Github Plan`). | String | Yes |
| **Slug** | URL-friendly identifier. | Slug | Yes |
| **Status** | The active contract state (`active`, `expired`, `cancelled`, `trial`, etc.). | Choice | Yes |
| **Renewal Cost** | Recurring pricing cost per period. | Decimal | No |
| **Currency** | Currency code for payment (e.g. `USD`, `EUR`). | String | No |
| **Billing Cycle** | Billing interval (e.g. Monthly, Annually). | Choice | Yes |
| **Term (Months)** | Duration of the active subscription period. | Integer | No |
| **Auto-Renewal** | Toggles whether this contract automatically renews. | Boolean | Yes |
| **Licensed Quantity**| Number of user seats or licenses covered under this subscription. | Integer | No |
| **Contract Reference**| Contract, PO, or agreement tracking reference. | String | No |
| **Cost Center** | Budget tracking/accounting code for cost allocations. | String | No |
| **Owner** | Person responsible for managing this subscription. | Foreign Key | No |
| **Start Date** | Contract activation date. | Date | No |
| **Next Renewal Date** | The next upcoming billing renewal date. | Date | No |
| **Cancellation Date**| Timestamp when the subscription was cancelled. | Date | No |
| **Notes** | Internal notes concerning renewal logic or terms. | Text | No |

## Allocations
SaaS subscriptions support a polymorphic generic relation allowing them to be assigned to `Asset Holders` (users) or departments (`Tenants`), enabling clear contract utilization audits.
