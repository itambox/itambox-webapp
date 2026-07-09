# Kits

A **Kit** represents a preconfigured template bundle containing hardware models (Asset Types), accessories, consumables, and software licenses that are regularly checked out together (e.g. `Standard Developer Onboarding Kit`, `Remote Sales Kit`, `Standard Conference Room Staging Kit`).

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Description** | Optional notes describing the target audience or hardware configurations. | Text | No |
| **Kit Name** | Unique name identifying the kit bundle template. | String | Yes |
| **Tenant** | Cost center department. | Foreign Key | No |

## Polymorphic Checkout Fulfillment
Checking out a Kit to a recipient (an `Asset Holder` or a `Location`) triggers a dynamic fulfillment service:
1. Validates that all items inside the kit are in stock at the source location.
2. Performs atomic checkouts for each item inside the kit in a single transaction block.
3. Automatically maps accessory allocations, decrements consumable stocks, and reserves software licenses.
