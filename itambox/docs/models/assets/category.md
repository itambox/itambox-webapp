# Categories

A **Category** defines administrative, legal, and operational rules governing assets, accessories, or consumables assigned to it.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Name** | Unique name of the category (e.g. `Corporate Laptops`). | String | Yes |
| **Slug** | URL-safe name representation. | Slug | Yes |
| **Description** | Optional descriptive details. | Text | No |
| **Email on Checkout** | Send automated email receipts to recipients upon asset checkout. | Boolean | Yes |
| **Email on Checkin** | Send automated return receipts upon checkin. | Boolean | Yes |
| **Require Acceptance** | Requires the holder to sign a digital Custody Receipt (EULA) before receiving the asset. | Boolean | Yes |
| **EULA Text** | Optional category-specific End User License Agreement terms. | Text | No |
| **Applies To** | JSON configuration dict stating if this category governs: `{'asset': True, 'accessory': True, 'component': True, 'consumable': True}`. | JSON | Yes |

## Digital Signature / EULA Gating
If **Require Acceptance** is enabled, checkout triggers the creation of a `Custody Receipt` with a secure email verification token. The asset status remains restricted until the recipient signs the receipt via their web portal.
