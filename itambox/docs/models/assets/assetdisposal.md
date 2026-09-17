# Asset Disposals

An **Asset Disposal** records the end-of-life process, disposal method, and data sanitization verification of a retired physical asset. The record *is* the evidence: it is never replaced, ordinary deletion is refused, and correcting a mistake is an explicit **cancellation** that keeps the record visible. Only the separately authorised purge can remove the row physically.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Asset** | The physical asset being disposed of. Immutable once the record exists. | Foreign Key | Yes |
| **Currency** | Currency of the transaction proceeds. | Choice | Yes |
| **Data Sanitization Method** | NIST SP 800-88 Rev.1 aligned method used to sanitize storage media (e.g., Purge, Destroy, Clear, None). | Choice | Yes |
| **Disposal Date** | The date on which the asset was officially disposed of. | Date | Yes |
| **Disposal Method** | The method of disposal (e.g., Destruction, Recycling, Donation, Resale). | Choice | Yes |
| **Notes** | Optional comments or additional information. | Text | No |
| **Proceeds** | The financial amount received for the asset (resale or salvage value). | Decimal | No |
| **Recipient** | The buyer, recycler, charity, or other recipient of the disposed asset. | String | No |
| **Sanitization Certificate** | Certificate serial number or reference ID from the sanitization vendor. | String | No |
| **Sanitized By** | The person or vendor who performed the data sanitization. | String | No |
| **WEEE Compliant** | Indicates if the disposal was carried out by an authorized WEEE recycler. | Boolean | Yes |
| **Cancelled At** | Timestamp of the cancellation. Read-only: written by the cancellation operation. | DateTime | No |
| **Cancelled By** | The user who cancelled the disposal. Read-only. | Foreign Key | No |
| **Cancellation Reason** | Why the disposal was recorded in error. Mandatory, read-only. | Text | On cancellation |

## Features & Validation

* **One active disposal per asset**: a conditional unique constraint on the asset applies while `Cancelled At` is empty, and it deliberately includes soft-deleted rows, so a hidden tombstone cannot release the asset. A second disposal attempt while an active record exists is rejected: the existing record is never replaced.
* **Atomic disposal**: the dedicated action, the record form, bulk disposal, the REST API and the admin all run one shared operation that creates the record, stamps the asset and archives it, closing any active assignment in the same transaction.
* **Cancellation instead of deletion**: `POST /assets/disposals/<pk>/cancel/` (UI) and `POST /api/assets/asset-disposals/{id}/cancel/` (REST) cancel a disposal. The reason is mandatory, the actor and the timestamp are persisted, and the record stays in the list and in the end-of-life report with a `Cancelled` status. Cancelling returns the asset to a `pending` status and clears the disposal freeze; it never re-deploys the asset automatically and never restores a previous assignment.
* **Evidence cannot be deleted**: ordinary soft/hard deletion of a disposal record is refused (UI, REST and the admin). The record is linked to the asset with `on_delete=models.PROTECT`, so deleting the disposed asset fails until the disposal is explicitly managed; a cancellation leaves the `PROTECT` link untouched, and only the separately authorised purge path can remove the row.
* **Authority**: recording a disposal uses the asset disposal permissions (`assets.add_assetdisposal` / `assets.change_assetdisposal`); cancelling reuses the dedicated `assets.dispose_asset` permission and is checked against the asset's own tenant.
* **Audit protection**: every disposal, amendment and cancellation is written to the change log (`ObjectChange`) with the actor; the pre-change snapshot of the record is retained.
* **Environmental compliance**: tracks WEEE compliance for electronics recycling.
* **Data sanitization evidence**: captures sanitization methods and certificates to meet organizational security requirements.

## Disposal history

Because a cancelled record is preserved and a later disposal creates a new record, an asset can have a history of disposals. Only the record with an empty `Cancelled At` value is *active*; every other row is auditable history.
