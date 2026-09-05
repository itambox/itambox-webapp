# Purchase Order Lines

A **Purchase Order Line** represents an individual line item on a Purchase Order. It is polymorphic, mapping exactly one item type per line.

## Fields

### Accessory

Accessory catalog type being purchased.

### Asset Type

Hardware catalog type being purchased.

### Component

Component catalog type being purchased.

### Consumable

Consumable catalog type being purchased.

### License

Software license catalog type being purchased.

### Purchase Order

The parent Purchase Order.

**Required:** Yes.

### Qty Ordered

The total quantity ordered.

**Required:** Yes.

### Qty Received

The quantity received so far (defaults to 0).

**Required:** Yes.

### Tenant

Optional tenant scope.

### Unit Price

The cost per unit.


## Constraints & Properties

* **Item type**: Each line represents exactly one supported item type: asset type, component, accessory, consumable, or license.
* **Qty Outstanding**: Computed property representing `qty_ordered` minus `qty_received`.
* **Total Cost**: Computed property representing `qty_ordered` times `unit_price`.
