# Asset Requests

An **Asset Request** represents a self-service request submitted by a user for a physical asset, component, accessory, consumable, or catalog asset type. Requests flow through a state machine from initial submission through approval (or denial) to eventual fulfilment.

---

## Fields

### Requester

The ITAMbox user who initiated the request.

**Required:** Yes.

### Asset

A specific physical asset being requested (e.g. `ASSET-001054`). Mutually exclusive with Asset Type, Component, Accessory, and Consumable.

### Asset Type

The catalog model template being requested (e.g. `MacBook Pro 16"`). Mutually exclusive with Asset, Component, Accessory, and Consumable.

### Component

A component catalog item being requested. Mutually exclusive with Asset, Asset Type, Accessory, and Consumable.

### Accessory

An accessory catalog item being requested. Mutually exclusive with Asset, Asset Type, Component, and Consumable.

### Consumable

A consumable catalog item being requested. Mutually exclusive with Asset, Asset Type, Component, and Accessory.

### Quantity

Number of units requested (applies to accessories, consumables, and components).

**Required:** Yes.

### Source Location

The preferred stock location from which items should be drawn.

### Status

Asset Request lifecycle state: `Pending`, `Approved`, `Awaiting Procurement`, `Denied`, `Fulfilled`, or `Cancelled`.

**Required:** Yes.

### Request Date

Timestamp when the request was submitted (auto-set on creation).

**Required:** Yes.

### Response Date

Timestamp when an administrator responded to the request.

### Responded By

The administrator who approved, denied, or processed the request.

### Response Notes

Feedback or explanation supplied by the responding administrator.

### Assigned User

The AssetHolder the requested item should be assigned to (delegated target).

### Assigned Location

The location the requested item should be assigned to (delegated target).

### Assigned Asset

The parent asset the requested item should be assigned to (delegated target).

### Parent

Link to a parent group request for hierarchical (multi-line) requests.

### Is Group

Whether this request acts as a container grouping child sub-requests.

**Required:** Yes.

### Notes

Justification, requirements, or context added by the requester.

### Tenant

Tenant that owns or scopes this asset request.


---

## State Machine

Valid status transitions are enforced at the model level:

| From | Valid Transitions To |
| --- | --- |
| **Pending** | Approved, Denied, Cancelled, Fulfilled |
| **Approved** | Fulfilled, Cancelled, Awaiting Procurement |
| **Awaiting Procurement** | Fulfilled, Cancelled, Approved |
| **Denied** | *(terminal)* |
| **Fulfilled** | *(terminal)* |
| **Cancelled** | *(terminal)* |

## Validation Gating

- **Exactly one item category** must be selected (Asset, Asset Type, Component, Accessory, or Consumable).
- **At most one assignment target** may be specified (Assigned User, Assigned Location, or Assigned Asset).
- Requested assets and asset types must be marked as **requestable** in their configuration.
- Duplicate pending/approved requests for the same item by the same requester are blocked.
- Quantity must be greater than zero.

## Auto-Approval

Automatic approval is disabled on a fresh deployment. Set
`ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS` to a JSON object containing
non-negative `accessory` and/or `consumable` thresholds, for example
`{"accessory": 3, "consumable": 5}`. A request is auto-approved at creation
only when its quantity is within the configured threshold and sufficient stock
exists. The persisted response notes record the automatic decision. Thresholds
are process-wide rather than tenant-specific, so this seam remains Beta.

The legacy `REQUISITION_AUTO_APPROVAL_THRESHOLDS` name remains a deprecated 1.x
fallback and emits a startup warning. If neither name is configured, requests
remain pending and the Asset Request procurement seam is inactive. Invalid
JSON, unknown keys, or boolean/negative thresholds do **not** fail soft: they
abort startup with a configuration error, so verify the JSON syntax before
deploying a change.
