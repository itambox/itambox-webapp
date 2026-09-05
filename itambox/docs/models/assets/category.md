# Categories

A **Category** defines administrative, legal, and operational rules governing assets, accessories, or consumables assigned to it.

## Fields

### Applies To

JSON configuration dict stating if this category governs: `{'asset': True, 'accessory': True, 'component': True, 'consumable': True}`.

**Required:** Yes.

### Audit Interval Months

How often assets in this category must be physically audited, in months. Leave blank for no required cadence.

### Color

RGB color in hexadecimal (e.g. 00ff00)

### Description

Optional descriptive details.

### Name

Unique name of the category (e.g. `Corporate Laptops`).

**Required:** Yes.

### Slug

URL-safe name representation.

**Required:** Yes.


## Digital Signature / EULA Gating
If **Require Acceptance** is enabled, checkout triggers the creation of a `Custody Receipt` with a secure email verification token. The asset status remains restricted until the recipient signs the receipt via their web portal.
