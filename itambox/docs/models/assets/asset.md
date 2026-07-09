# Physical Assets

An **Asset** represents a physical, high-value, trackable item (e.g. `Laptop`, `Rack Server`, `Desktop`, `Network Switch`) that is owned, leased, or managed by your organization. Each asset is uniquely identified by an **Asset Tag** and a **Serial Number**.

---

## Status Labels & State Gating

Asset Box utilizes a strict state-governed workflow managed via **Status Labels** and their core **Meta-Types**:

| Meta-Type | Operational Meaning | Checkout Availability |
| --- | --- | --- |
| **Deployable** | Item is available in inventory and ready to be assigned. | **Yes** |
| **Deployed** | Item is currently checked out to a user, location, or parent asset. | **No** |
| **Pending** | Item is awaiting prep, staging, OS installation, or audit. | **No** |
| **Undeployable** | Item is broken, lost, or undergoing heavy diagnostic repair. | **No** |
| **Archived** | Item is decommissioned, sold, recycled, or disposed of. | **No** |

!!! warning "State Synchronization Gating"
    ITAMbox enforces database-level constraints preventing split-state anomalies. An asset's status cannot be set to a status of type `deployed` unless there is an active `AssetAssignment` record linked to it. Similarly, checking in an asset deletes the active assignment and returns the asset to a `deployable` or `pending` status.

---

## Attributes & Fields

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Asset Role** | The functional category of the asset (e.g. `Developer Laptop`). | Foreign Key | No |
| **Asset Tag** | A unique barcode tag (e.g. `ASSET-000102`). Auto-generated if left blank. | String | Yes |
| **Asset Type** | The model template from the Catalog (Manufacturer + Model details). | Foreign Key | Yes |
| **Cost Center** | The cost center of the asset. | Foreign Key | No |
| **Currency** | ISO 4217 code. Leave blank to use the tenant default currency. | Choice | No |
| **Current Book Value** | Materialized current financial value computed via straight-line depreciation. | Decimal | No (Auto) |
| **Depreciation Override** | Override depreciation policy — leave empty to use the tenant default or asset-type schedule. | Foreign Key | No |
| **Depreciation Updated At** | The depreciation updated at of the asset. | Date Time | No |
| **Disposal Value** | The sign-off value of the asset. | Decimal | No |
| **Disposed At** | The disposed at of the asset. | Date Time | No |
| **In Service Date** | Depreciation starts here; falls back to purchase date. | Date | No |
| **Last Audited** | The timestamp when the asset was last verified during an audit session. | DateTime | No (Auto) |
| **Last Audited By** | The user account of the auditor who last scanned the asset. | Foreign Key | No (Auto) |
| **Location** | The physical Site / Location room where the asset resides. | Foreign Key | No |
| **Name** | A recognizable name for the asset (e.g. `Jane's Workstation`). | String | Yes |
| **Notes** | The notes of the asset. | Text | No |
| **Order Number** | The purchase order reference number associated with this procurement. | String | No |
| **Purchase Cost** | The total cost of acquisition. | Decimal | No |
| **Purchase Date** | The date the asset was purchased. | Date | No |
| **Purchase Order Line** | The purchase order line of the asset. | Foreign Key | No |
| **Requestable** | Toggle allowing end-users to request this asset via self-service. | Boolean | Yes |
| **Salvage Value** | Estimated value at the end of its useful lifespan. | Decimal | No |
| **Serial Number** | The manufacturer's unique hardware serial number. | String | No |
| **Status** | The current operational Status Label. | Foreign Key | Yes |
| **Supplier** | The vendor or supplier from whom the asset was purchased. | Foreign Key | No |
| **Tenant** | Cost center department owning the asset. | Foreign Key | No |

## Lifecycle Workflows

### 1. Checkout (Assigning Custody)
Assets can be checked out polymorphicly to:
1. **Asset Holder**: An employee or contractor profile.
2. **Location**: Staged physically in a room, shelf, or building.
3. **Asset**: Modular nesting (e.g., checking out a GPU or RAM card to a parent server system).

### 2. Checkin (Return)
When an asset is returned, the checkout assignment is closed. The administrator can determine if the asset returns to `Available` stock or is marked as `Pending Repair` for maintenance triage.

### 3. Depreciation
ITAMbox calculates monthly straight-line depreciation values dynamically using the parent asset type's **Depreciation Profile**, automatically deducting value based on months held between the `Purchase Date` and the current date, down to the defined `Salvage Value`.
