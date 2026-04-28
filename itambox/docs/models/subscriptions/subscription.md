# SaaS Subscriptions

A **SaaS Subscription** represents a recurring subscription contract for SaaS platforms, tools, or cloud resources (e.g. `Figma Professional Plan`, `GitHub Enterprise Cloud`, `AWS Organization Account`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Provider** | The SaaS Provider hosting the platform (e.g., `GitHub`). | Foreign Key | Yes |
| **Name** | A clear name identifying the contract plan (e.g. `Dev DevOps Github Plan`). | String | Yes |
| **Cost** | Recurring pricing cost. | Decimal | Yes |
| **Billing Frequency** | Billing interval (e.g. Monthly, Annually). | Choice | Yes |
| **Start Date** | Contract activation date. | Date | Yes |
| **Next Renewal Date** | The next upcoming billing renewal date. | Date | No |

## Allocations
SaaS subscriptions support a polymorphic generic relation allowing them to be assigned to `Asset Holders` (users) or departments (`Tenants`), enabling clear contract utilization audits.
