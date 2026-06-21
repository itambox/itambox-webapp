# Asset Disposals

An **Asset Disposal** records the end-of-life process, disposal method, and data sanitization verification of a retired physical asset.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Asset** | The physical asset being disposed of (One-to-One relationship). | Foreign Key | Yes |
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

## Features & Validation

* **Audit Protection**: The disposal record is linked with `on_delete=models.PROTECT`. Deleting a disposed asset will fail until the disposal record itself is intentionally managed, safeguarding regulatory evidence.
* **Environmental Compliance**: Tracks WEEE compliance for electronics recycling.
* **Data Sanitization Evidence**: Captures sanitization methods and certificates to meet organizational security requirements.
