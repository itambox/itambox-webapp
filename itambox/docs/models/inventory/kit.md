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

Checking out a Kit to an **AssetHolder** or a **Location** runs the kit checkout service.
In tenant-group and All-accessible scopes, choose the required **Target tenant** first;
the form refreshes its device, holder and source-location choices for that tenant.
In a concrete tenant scope, the current tenant is authoritative.

1. **Explicit device selection**: Each hardware row must name the concrete device to hand
   out (asset tag / serial), one distinct device per row. A device that is reserved for
   another holder today, already assigned, of the wrong type, or no longer deployable is rejected
   — never silently substituted.
2. **Stock validation**: Stock items are checked at the selected source location; selected
   hardware is validated by device identity, not inferred from that stock location.
3. **Atomic fulfilment**: Each kit item is processed in a single database transaction — hardware
   rows are checked out through the individual-asset operation (custody receipt and signature
   request, reservation and lifecycle guards, loan fields), accessory allocations are created,
   consumable stocks are decremented, and software licenses are reserved.
4. **Rollback on failure**: If any item fails (selection no longer valid, device reserved,
   out of stock, license exhausted), the entire checkout is rolled back — including created
   custody receipts — and scheduled custody notification e-mails are discarded.

Kits can be tenant-scoped (private to one tenant) or global (shared template visible across all tenants).
