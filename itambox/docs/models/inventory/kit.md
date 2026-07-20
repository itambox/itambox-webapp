# Kits

A **Kit** represents a preconfigured template bundle containing hardware models (Asset Types), accessories, consumables, software licenses, and components that are regularly checked out together — for example, a *Standard Developer Onboarding Kit* or a *Remote Sales Kit*.

---

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Kit Name** | Unique name identifying the kit bundle template. Globally unique across active (non-deleted) kits. | String (100) | Yes |
| **Description** | Optional notes describing the target audience, use case, or hardware configurations. | Text | No |
| **Tenant** | Owning tenant. Null denotes a shared/global kit template visible to all tenants. | Foreign Key | No |
| **Tags** | Labels for categorisation and filtering. | M2M | No |

---

## Kit Checkout Workflow

Checking out a Kit to a recipient (an **AssetHolder** or a **Location**) triggers the `checkout_to_holder` service:

1. **Stock validation**: All items inside the kit are checked for availability at the source location.
2. **Atomic fulfilment**: Each kit item is processed in a single database transaction — accessory allocations are created, consumable stocks are decremented, and software licenses are reserved.
3. **Rollback on failure**: If any item fails (out of stock, license exhausted), the entire checkout is rolled back.

Kits can be tenant-scoped (private to one tenant) or global (shared template visible across all tenants).
