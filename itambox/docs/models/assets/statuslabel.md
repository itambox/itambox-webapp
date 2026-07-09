# Status Labels

**Status Labels** represent the discrete operational phases in an asset's lifecycle. Every Status Label belongs to one of five core **Meta-Types**, which govern whether an asset can be checked out or how it is treated in system calculations.

## The Five Meta-Types

1. **Deployable**: The asset is functional, in stock, and ready for checkout.
2. **Deployed**: The asset is currently checked out to a user, location, or parent asset.
3. **Pending**: The asset is undergoing imaging, provisioning, shipping, or pre-deployment checks.
4. **Undeployable**: The asset is broken, undergoing repair, lost, or otherwise unavailable.
5. **Archived**: The asset has been decommissioned, recycled, or sold, and is preserved for historical auditing.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Color** | Hexadecimal color code for visual badge indicators. | Hex Color | No |
| **Description** | Optional notes describing when to use this label. | Text | No |
| **Name** | Unique name of the status label (e.g., `Staged & Ready`). | String | Yes |
| **Slug** | URL-safe name representation. | Slug | Yes |
| **Type** | One of the five core Meta-Types. | Choice | Yes |

## Use Cases
You can create custom, highly detailed labels for your workflows while preserving global compliance:
* Label: `In Transit` (Meta-Type: `Pending`)
* Label: `Awaiting Repair` (Meta-Type: `Undeployable`)
* Label: `Ready to Deploy` (Meta-Type: `Deployable`)
