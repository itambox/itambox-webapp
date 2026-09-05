# Assets And Assignments

An Asset represents one uniquely tracked physical item. The Asset record combines identity, ownership, lifecycle state, location, financial data, and related operational history.

## Asset Identity

Use an Asset Tag as the ITAMbox inventory identifier and retain the manufacturer's Serial Number when available. Asset Type connects the physical item to a reusable model definition such as manufacturer/model information.

Status Labels describe operational state. Their meta-types determine which lifecycle actions are valid. Keep the label vocabulary small enough that operators can predict what a status means in practice.

## Assignments

Check-out creates an active assignment. An asset can be assigned to:

- an Asset Holder;
- a Location;
- another Asset.

The active assignment is the current custody/placement relationship. Historical assignments remain useful evidence after they are closed.

### Check Out

Open the Asset and choose **Check-out**. Select the target and any applicable status or notes. An asset in a state that cannot be deployed must be corrected before checkout.

If a supported checkout flow is used on an already assigned asset, ITAMbox can reassign it by closing the previous assignment first. Bulk checkout warns when an asset is already assigned and will be reassigned.

### Check In

Choose **Check-in** when the current assignment ends. The active assignment is closed. The workflow can return the asset to a deployable or other appropriate status and location depending on the selected values.

### Move And Reassign

Use assignment changes rather than overwriting historical facts. A move to another Location or a reassignment to another holder should leave a readable custody trail.

## Related Lifecycle Records

Use Warranties for coverage, Asset Maintenances for service events, Audits for physical verification, and Asset Disposals for end-of-life evidence. Depreciation tracks financial value independently of custody.

Continue with [Recording the Asset Lifecycle](../getting-started/asset-lifecycle.md), [Scanning Assets](../usage/scanning.md), and the [Asset Data Model](../models/assets/asset.md).
