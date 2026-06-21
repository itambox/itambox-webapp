# SaaS Subscriptions

!!! warning "Status: Beta"
    The Subscriptions module is Beta. Core functionality is stable; reporting and renewal
    automation features are still evolving.

A **SaaS Subscription** represents a recurring subscription contract for SaaS platforms, tools, or cloud resources (e.g. `Figma Professional Plan`, `GitHub Enterprise Cloud`, `AWS Organization Account`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Auto-Renewal** | Toggles whether this contract automatically renews. | Boolean | Yes |
| **Billing Cycle** | Billing interval (e.g. Monthly, Annually). | Choice | Yes |
| **Cancellation Date** | Timestamp when the subscription was cancelled. | Date | No |
| **Contract Reference** | Contract, PO, or agreement tracking reference. | String | No |
| **Cost Center** | Budget tracking/accounting code for cost allocations. | String | No |
| **Currency** | Currency code for payment (e.g. `USD`, `EUR`). | String | No |
| **Description** | Optional text detailing coverage or terms. | Text | No |
| **Licensed Quantity** | Number of user seats or licenses covered under this subscription. | Integer | No |
| **Name** | A clear name identifying the contract plan (e.g. `Dev DevOps Github Plan`). | String | Yes |
| **Notes** | Internal notes concerning renewal logic or terms. | Text | No |
| **Owner** | Person responsible for managing this subscription. | Foreign Key | No |
| **Provider** | The SaaS Provider hosting the platform (e.g., `GitHub`). | Foreign Key | Yes |
| **Renewal Cost** | Recurring pricing cost per period. | Decimal | No |
| **Next Renewal Date** | The next upcoming billing renewal date. | Date | No |
| **Slug** | URL-friendly identifier (auto-generated if blank). | Slug | Yes |
| **Start Date** | Contract activation date. | Date | No |
| **Status** | The active contract state (`active`, `expired`, `cancelled`, `trial`, etc.). | Choice | Yes |
| **Tenant** | The tenant scoping boundary for this subscription. | Foreign Key | No |
| **Term (Months)** | Duration of the active subscription period. | Integer | No |
| **Type** | The subscription type of the subscription. | Choice | Yes |

## Allocations
SaaS subscriptions support a polymorphic generic relation allowing them to be assigned to `Asset Holders` (users) or departments (`Tenants`), enabling clear contract utilization audits.
