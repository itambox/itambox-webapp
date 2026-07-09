# Categories

A **Category** defines administrative, legal, and operational rules governing assets, accessories, or consumables assigned to it.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Applies To** | JSON configuration dict stating if this category governs: `{'asset': True, 'accessory': True, 'component': True, 'consumable': True}`. | JSON | Yes |
| **Audit Interval Months** | How often assets in this category must be physically audited, in months. Leave blank for no required cadence. | Integer | No |
| **Color** | RGB color in hexadecimal (e.g. 00ff00) | String | No |
| **Description** | Optional descriptive details. | Text | No |
| **Name** | Unique name of the category (e.g. `Corporate Laptops`). | String | Yes |
| **Slug** | URL-safe name representation. | Slug | Yes |

## Digital Signature / EULA Gating
If **Require Acceptance** is enabled, checkout triggers the creation of a `Custody Receipt` with a secure email verification token. The asset status remains restricted until the recipient signs the receipt via their web portal.
