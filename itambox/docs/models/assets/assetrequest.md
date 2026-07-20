# Asset Requests

An **Asset Request** represents a self-service requisition ticket submitted by a user for a physical asset, component, accessory, consumable, or catalog asset type. Requests flow through a state machine from initial submission through approval (or denial) to eventual fulfilment.

---

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Requester** | The Django user who initiated the request. | Foreign Key | Yes |
| **Asset** | A specific physical asset being requested (e.g. `ASSET-001054`). Mutually exclusive with Asset Type, Component, Accessory, and Consumable. | Foreign Key | No |
| **Asset Type** | The catalog model template being requested (e.g. `MacBook Pro 16"`). Mutually exclusive with Asset, Component, Accessory, and Consumable. | Foreign Key | No |
| **Component** | A component catalog item being requested. Mutually exclusive with Asset, Asset Type, Accessory, and Consumable. | Foreign Key | No |
| **Accessory** | An accessory catalog item being requested. Mutually exclusive with Asset, Asset Type, Component, and Consumable. | Foreign Key | No |
| **Consumable** | A consumable catalog item being requested. Mutually exclusive with Asset, Asset Type, Component, and Accessory. | Foreign Key | No |
| **Quantity** | Number of units requested (applies to accessories, consumables, and components). | Integer | Yes |
| **Source Location** | The preferred stock location from which items should be drawn. | Foreign Key | No |
| **Status** | Requisition lifecycle state: `Pending`, `Approved`, `Awaiting Procurement`, `Denied`, `Fulfilled`, or `Cancelled`. | Choice | Yes |
| **Request Date** | Timestamp when the request was submitted (auto-set on creation). | DateTime | Yes |
| **Response Date** | Timestamp when an administrator responded to the request. | DateTime | No |
| **Responded By** | The administrator who approved, denied, or processed the request. | Foreign Key | No |
| **Response Notes** | Feedback or explanation supplied by the responding administrator. | Text | No |
| **Assigned User** | The AssetHolder the requested item should be assigned to (delegated target). | Foreign Key | No |
| **Assigned Location** | The location the requested item should be assigned to (delegated target). | Foreign Key | No |
| **Assigned Asset** | The parent asset the requested item should be assigned to (delegated target). | Foreign Key | No |
| **Parent** | Link to a parent group request for hierarchical (multi-line) requests. | Foreign Key (self) | No |
| **Is Group** | Whether this request acts as a container grouping child sub-requests. | Boolean | Yes |
| **Notes** | Justification, requirements, or context added by the requester. | Text | No |
| **Tenant** | The tenant scope of the request. | Foreign Key | No |

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

Accessory and consumable requests may be auto-approved at creation time when the requested quantity is within configured thresholds and sufficient available stock exists. This is advisory only — capacity enforcement occurs at fulfilment time.
