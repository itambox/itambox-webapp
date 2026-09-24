# Repair Episodes

A **Repair Episode** groups the records of one repair or replacement story: the asset that failed and, when one exists, the unit that stood in for it — a temporary loaner or a permanent replacement. The episode is deliberately not a ticket and not a workflow: it carries no status and no state machine. Existing status labels and record states stay the single source of truth; the episode only lets the lifecycle records of one story be read together on the asset detail page.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Asset** | The unit this repair or replacement episode is about. | Foreign Key | Yes |
| **Loaner / Substitute** | The unit that stood in for the asset (temporary loan or permanent replacement). Must not be the asset itself and must belong to the same tenant as the asset. | Foreign Key | No |
| **Notes** | Optional comments or additional information. | Text | No |

## Features & Validation

* **Linking is optional**: maintenance records, reservations, and disposals carry their own optional **Repair Episode** field. Records without a link stay valid and unchanged, and every existing record behaves exactly as before the feature existed.
* **One story across two assets**: when the loaner carries linked records (for example the reservation of the stand-in unit), the asset detail page of *both* units shows the whole story; borrowed records name the asset they really belong to.
* **Timeline reconstruction**: the asset detail page renders the story in its **Timeline** tab. Records linked to an episode are grouped under that episode; everything else — including transitions such as *In Repair* — remains in a plain chronological list. The builder only reads; it never writes or changes record states.
* **The link survives recycle-bin edits**: a record keeps its already-linked episode selectable even when that episode sits in the recycle bin, so editing a record never silently drops the story link.
* **Tenant scope**: an episode is scoped to the tenant of its asset, and the substitute must belong to the same tenant. Cross-tenant grouping is rejected.
* **Audit trail**: linking or unlinking a record and every change to the episode is written to the change log like any other field.

## Use Cases

A laptop fails and goes to the vendor for repair; a loaner is issued while it is away. Record a Repair Episode for the failing laptop with the loaner as the substitute, then link the maintenance record and the loaner's reservation to it. When the repair is done, link the disposal of the failed unit to the same episode. The Timeline tab of the laptop and of the loaner then tells the complete story — maintenance, reservation, and disposal in one place. See [Repair & Replacement Episodes](../../usage/repair-episodes.md).
