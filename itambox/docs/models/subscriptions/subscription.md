# SaaS Subscriptions

!!! warning "Status: Beta"
    The Subscriptions module is Beta. Core functionality is stable; reporting and renewal
    automation features are still evolving.

A **SaaS Subscription** represents a recurring subscription contract for SaaS platforms, tools, or cloud resources (e.g. `Figma Professional Plan`, `GitHub Enterprise Cloud`, `AWS Organization Account`). Use the exclusive mapping in [Commercial Vocabulary](../../usage/commercial-vocabulary.md) to decide whether an agreement belongs here or in Contracts.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Vendor Contract Auto-Renews** | Records whether the vendor contract renews automatically; ITAMbox does not perform billing. | Boolean | Yes |
| **Billing Cycle** | Billing interval (e.g. Monthly, Annually). | Choice | Yes |
| **Cancellation Date** | Timestamp when the subscription was cancelled. | Date | No |
| **Contract Reference** | Contract, PO, or agreement tracking reference. | String | No |
| **Linked Contract** | Optional operational link to a Procurement Contract. It does not combine the two modules or change which module records the agreement. | Foreign Key | No |
| **Cost Center** | Budget tracking/accounting code for cost allocations. | String | No |
| **Currency** | Currency code for payment (e.g. `USD`, `EUR`). | String | No |
| **Description** | Optional text detailing coverage or terms. | Text | No |
| **Licensed Quantity** | Agreement Entitled Quantity: number of seats, users, or devices entitled by the vendor agreement. Independent of linked License seats. | Integer | No |
| **Name** | A clear name identifying the contract plan (e.g. `Dev DevOps Github Plan`). | String | Yes |
| **Notes** | Internal notes concerning renewal logic or terms. | Text | No |
| **Owner** | Person responsible for managing this subscription. | Foreign Key | No |
| **Supplier** | The commercial vendor of this subscription (e.g., `GitHub`). | Foreign Key | Yes |
| **Renewal Cost** | Recurring pricing cost per period. | Decimal | No |
| **Next Renewal Date** | The next upcoming billing renewal date. | Date | No |
| **Slug** | URL-friendly identifier (auto-generated if blank). | Slug | Yes |
| **Start Date** | Contract activation date. | Date | No |
| **Status** | Lifecycle state: `active`, `suspended`, `cancelled`, or `expired`; changed only through explicit lifecycle actions. | Choice | Yes |
| **Tenant** | The tenant scoping boundary for this subscription. | Foreign Key | No |
| **Term (Months)** | Duration of the active subscription period. | Integer | No |
| **Type** | The subscription type of the subscription. | Choice | Yes |

## Allocations
SaaS subscriptions support a polymorphic generic relation allowing them to be assigned to `Asset Holders` (users) or departments (`Tenants`), enabling clear contract utilization audits.

## Quantity metrics

**Agreement Entitled Quantity** is the entitlement recorded by the vendor agreement. **Linked License Seats** are computed from the Licenses linked to this subscription and are shown separately; they never change the agreement entitlement.

## Supplier and contract boundary

Every Subscription requires a Supplier from the shared commercial catalogue. A
Subscription may also link to a Procurement Contract through `linked_contract`
when the records are operationally related. Record each agreement in one
module only: the link does not merge Contracts and Subscriptions or duplicate
an agreement across them.

## Lifecycle

Use the explicit **Suspend**, **Resume**, **Renew**, and **Cancel** actions. Normal edit forms and generic REST/GraphQL updates do not accept direct status or cancellation-date writes. Renewal records contract terms and the next date only; it does not charge the vendor or perform payment processing.
