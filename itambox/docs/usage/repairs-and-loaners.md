# Repairs and Loaners

A repair is one record — the **repair maintenance** — and it is the anchor of the whole story. There is no separate grouping object to maintain: you record the repair, optionally issue a loaner to the person who held the unit, and the Timeline groups the loan and, later, the disposal under that repair.

## Recording a repair with a loaner

1. Open the failing asset and switch to its **Timeline** tab, then choose **Log repair**. The maintenance form opens with the type **Repair** preselected. (You can also start from **Assets → Maintenances → Add** and pick **Repair** as the type.)
2. Fill in the repair itself: start date, expected completion, supplier, cost and notes.
3. Open the collapsed **Issue loaner** section and pick the stand-in unit. The due date defaults to the expected completion; change it if the loan is shorter or longer.
4. Save. One submission records the repair *and* checks the loaner out to the asset's current holder as a loan tied to that repair.

The loaner section only appears when you are allowed to check assets out, and it only lists units of the same tenant. It fails closed — nothing is written — when the unit is already assigned, sits outside a deployable status, belongs to another tenant, or the failing asset has no holder to lend to.

## Completing the repair

Open the repair maintenance again once it is done. While the repair still has an open loaner, the **Complete repair** section offers three deliberate outcomes:

| Outcome | What happens |
| --- | --- |
| **Returned** | The loaner is checked back in and the repaired unit is checked out to the same holder, so responsibility returns to where it was. |
| **Replace permanently** | The loan becomes a regular assignment (its loan flag and due date are cleared) and the loaner stays with the holder. Tick **Start the disposal of the unit that was under repair**, pick the disposal method and date, and the original unit is disposed of in the same step, linked to this repair. |
| **Do nothing** | Both units stay exactly as they are; you handle them manually. |

Each outcome reuses the existing checkout, check-in and disposal operations, so the status handling, custody receipts and audit trail are the same as anywhere else in the product. An action you are not allowed to perform is not offered, and a refused action writes nothing at all.

## Reading the story on the Timeline

The **Timeline** tab of the asset detail page reconstructs the story:

* The repair maintenance groups the records that belong to it — the loan issued for it and the disposal that closed it out — in one block, in chronological order.
* The same block appears on the loaner's page and names the unit that was under repair, so both sides of the story stay readable.
* Assignments, loans, warranties, reservations and status transitions appear as timeline events; a loan shows its due date, its overdue state and whether it is active or already returned.
* Records that belong to no repair stay in the plain chronological list below.

## Good to know

* A reservation never links to a repair: a reservation books a unit for later, a loan is an actual handover.
* A lost unit is handled by assigning a new asset; there is no incident grouping without a repair record.
* Nothing is created automatically beyond what the checkout, check-in and disposal operations already do — no status is changed and no label is invented.
* Upgrading from `v1.0.0-beta.3` or earlier migrates the beta-era repair episodes onto this model: disposals linked to an episode move to its repair maintenance, a linked stand-in becomes the repair's loan, and everything that cannot be mapped unambiguously is preserved in the maintenance notes or as a journal entry on the asset.
