# Physical Assets

An **Asset** represents one physical, trackable item (e.g. `Laptop`, `Rack Server`, `Desktop`, `Network Switch`) that is owned, leased, or managed by your organization. Asset Tags are ITAMbox inventory identifiers; manufacturer Serial Numbers can be recorded separately when available.

---

## Status Labels & State Gating

ITAMbox utilizes a strict state-governed workflow managed via **Status Labels** and their core **Meta-Types**:

| Meta-Type | Operational Meaning | Checkout Availability |
| --- | --- | --- |
| **Deployable** | Item is available in inventory and ready to be assigned. | **Yes** |
| **Deployed** | Item is currently checked out to a user, location, or parent asset. | **No** |
| **Pending** | Item is awaiting prep, staging, OS installation, or audit. | **No** |
| **Undeployable** | Item is broken, lost, or undergoing heavy diagnostic repair. | **No** |
| **Archived** | Item is decommissioned, sold, recycled, or disposed of. | **No** |

---

## Fields

### Asset Role

The operational role used to classify the asset, when one is assigned.

### Asset Tag

A unique barcode tag (e.g. `ASSET-000102`). Auto-generated from the tag sequence if left blank.

### Asset Type

The model template from the Catalog (Manufacturer + Model details). May be blank for assets created before the type catalog is ready.

### Cost Center

Cost Center used for financial allocation of this physical asset.

### Currency

ISO 4217 code. Leave blank to use the tenant default currency.

### Current Book Value

Materialized current financial value computed via straight-line depreciation.

### Depreciation Override

Override depreciation policy: leave empty to use the tenant default or asset-type schedule.

### Depreciation Updated At

When the stored book-value calculation was last refreshed.

### Disposal Value

The value recorded when the asset was disposed of.

### Disposed At

When the asset was recorded as disposed of.

### In Service Date

Depreciation starts here; falls back to purchase date.

### Last Audited

The timestamp when the asset was last verified during an audit session.

### Last Audited By

The user who most recently verified the asset in an audit.

### Location

The physical Site / Location room where the asset resides.

### Name

A recognizable name for the asset (e.g. `Jane's Workstation`).

**Required:** Yes.

### Notes

Optional notes about this physical asset.

### Order Number

The purchase order reference number associated with this procurement.

### Purchase Cost

The total cost of acquisition.

### Purchase Date

The date the asset was purchased.

### Purchase Order Line

Purchase Order line associated with this record.

### Requestable

Toggle allowing end-users to request this asset via self-service. Defaults to enabled.

### Salvage Value

Estimated value at the end of its useful lifespan.

### Serial Number

The manufacturer's unique hardware serial number.

### Status

The current operational Status Label. Falls back to the default status label when not set explicitly.

### Supplier

The vendor or supplier from whom the asset was purchased.

### Tenant

Tenant that owns and scopes the asset.


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
