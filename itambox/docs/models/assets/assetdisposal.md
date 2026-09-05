# Asset Disposals

An **Asset Disposal** records the end-of-life process, disposal method, and data sanitization verification of a retired physical asset.

## Fields

### Asset

The physical asset being disposed of (One-to-One relationship).

**Required:** Yes.

### Currency

Currency of the transaction proceeds.

**Required:** Yes.

### Data Sanitization Method

NIST SP 800-88 Rev.1 aligned method used to sanitize storage media (e.g., Purge, Destroy, Clear, None).

**Required:** Yes.

### Disposal Date

The date on which the asset was officially disposed of.

**Required:** Yes.

### Disposal Method

The method of disposal (e.g., Destruction, Recycling, Donation, Resale).

**Required:** Yes.

### Notes

Optional comments or additional information.

### Proceeds

The financial amount received for the asset (resale or salvage value).

### Recipient

The recipient or destination recorded for the disposed asset.

### Sanitization Certificate

Certificate serial number or reference ID from the sanitization vendor.

### Sanitized By

The person or vendor who performed the data sanitization.

### WEEE Compliant

Indicates if the disposal was carried out by an authorized WEEE recycler.

**Required:** Yes.


## Features & Validation

* **Disposal history protection**: ITAMbox protects the disposal relationship so disposal history cannot be removed accidentally as a side effect of deleting an asset.
* **Environmental Compliance**: Tracks WEEE compliance for electronics recycling.
* **Data Sanitization Evidence**: Captures sanitization methods and certificates to meet organizational security requirements.
