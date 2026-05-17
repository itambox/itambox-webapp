# Subscription Assignments

A **Subscription Assignment** links a SaaS Subscription or recurring service agreement to the specific entities it covers. This allows administrators to track who or what is utilizing a SaaS seat or service subscription, providing cost visibility and compliance checking.

---

## Supported Assignment Targets
Through generic relations, subscriptions can be assigned polymorphicly to:
- **Asset**: E.g. linking a server support subscription to a rack server.
- **Asset Holder**: E.g. checking out a Figma seat to a designer.
- **Location**: E.g. assigning a Zoom Room subscription to a conference room location.
- **Accessory / Consumable**: Operational links where needed.

---

## Attributes & Fields

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Subscription** | The parent Subscription being allocated. | Foreign Key | Yes |
| **Content Type** | The target model type (Asset, Holder, Location, etc.). | Content Type | Yes |
| **Object ID** | The unique database ID of the target entity. | Integer | Yes |
| **Assigned Date** | Timestamp when the assignment occurred. | DateTime | Yes (Auto) |
| **Assigned By** | The User who created the subscription allocation. | Foreign Key | No |
| **Notes** | Optional assignment details (e.g. usage justification). | Text | No |

---

## Constraints
To prevent seat overallocation or double-assignments, a `UniqueConstraint` enforces that a specific entity can only be assigned to a specific subscription once.
