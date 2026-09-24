# Repair & Replacement Episodes

A repair episode reconstructs the story of one failing or replaced asset from the records that already exist: which unit failed, which unit stood in for it, and every maintenance, reservation, and disposal record that belongs to that story. This page shows how to record an episode, how to link records to it, and how to read the story on the asset pages.

## Recording an episode

1. Open the failing asset and switch to its **Timeline** tab, then choose **Add Repair Episode**. (You can also start from **Assets → Repair Episodes → Add** and pick the asset in the form.)
2. Leave **Loaner / Substitute** empty when no stand-in was involved, or pick the unit that stood in for the asset — a temporary loaner or a permanent replacement.
3. Optionally add **Notes**, then save.

An episode is a small grouping record. It does not replace the maintenance record, the reservation, or the disposal: those stay exactly where they are, with their own statuses and lifecycles.

## Linking records to an episode

All three lifecycle records carry an optional **Repair Episode** field:

| Record | Where to link it |
| --- | --- |
| Maintenance | The maintenance form / maintenance edit page |
| Reservation | The reservation form |
| Disposal | The disposal form |

The picker lists the active episodes of your tenant. An already-linked episode stays selectable even when it sits in the recycle bin, so amending a record never silently drops the story link.

## Reading the story

The **Timeline** tab on the asset detail page reconstructs the story:

* Records linked to an episode are grouped under that episode, in chronological order.
* Records without a link stay in a plain chronological list below, so nothing disappears while you migrate records into episodes.
* Status transitions such as *In Repair* come from the change log and link to the changelog tab.
* When a record lives on the loaner, it appears on both asset pages and names the asset it really belongs to.

## Good to know

* Nothing is forced into an episode: records without a link behave exactly as before.
* The episode is a grouping, not a second workflow: statuses remain on the assets and records.
* Deleting an episode moves it to the recycle bin. The records are never touched; while the episode is deleted, their entries fall back to the plain chronological list.

See [Repair Episodes](../models/assets/repair-episode.md) for the model reference.
