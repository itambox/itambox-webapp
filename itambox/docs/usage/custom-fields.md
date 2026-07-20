# Custom Fields

Custom fields allow you to extend ITAMbox models with user-defined attributes
without modifying the database schema or source code. They are the primary
mechanism for capturing organisation-specific data — anything from per-device
SIM card numbers and warranty tiers to department-specific approval flags.

Unlike hard-coded model fields which require a developer, custom fields are
created and managed entirely through the UI by administrators.

---

## Creating Custom Fields

Navigate to **Extras → Custom Fields** and click **Add**. Every custom field
has a core set of attributes:

| Attribute | Description |
|-----------|-------------|
| **Field Name** | Database-safe slug identifier (e.g. `sim_card_number`). Lowercase, no spaces. Used in templates and API queries. |
| **Display Label** | Human-friendly label shown in forms, list headers, and filters. |
| **Field Type** | Data type the field stores. Controls input widget and validation. |
| **Choices** | Newline-separated list of allowed values — only applicable for Selection list fields. |
| **Required** | If checked, validation requires a non-empty value when saving an object. |

### Field Types

| Type | Slug | Input Widget | Example Use |
|------|------|-------------|-------------|
| Text | `text` | Single-line text input | Asset tag aliases, notes |
| Number | `number` | Number input | CPU core count, rack unit position |
| Date | `date` | Date picker | Warranty end, inspection date |
| Boolean | `boolean` | Checkbox / toggle | "Under support contract", "Hazardous material" |
| Select / Dropdown | `select` | Dropdown list | Tier level, department, site code |

### Configuring Choices

When the field type is **Select / Dropdown**, use the **Choices** field to
define the allowed values. Enter one choice per line:

```
Platinum
Gold
Silver
Bronze
```

The first value is not treated as a default — the dropdown will render an
empty option unless the field is marked **Required**.

> [!IMPORTANT]
> Changing the choices list after data has been entered does **not** migrate
> existing values. If you remove a choice that is currently assigned to an
> object, that value is preserved in the database but the field will display
> as a plain text value rather than a dropdown selection on the next edit.

---

## Binding to Object Types

Custom fields must be associated with one or more **object types**
(ContentTypes — the Django model classes in ITAMbox). This controls which
forms display the field and which objects store its values.

### Supported Models

| Model | Content Type | What the field represents |
|-------|-------------|--------------------------|
| **Asset** | `assets.asset` | Per-device attributes (SIM number, desk location, cost centre) |
| **Asset Type** | `assets.assettype` | Hardware specifications shared by all assets of that type (form factor, TDP, PoE support) |
| **Tenant** | `organization.tenant` | Tenant-level configuration (region, billing code) |
| **Location** | `organization.location` | Site-level attributes (floor, building access code) |
| **Component** | `inventory.component` | Per-component specs (firmware version, slot position) |
| **Accessory** | `inventory.accessory` | Per-accessory attributes (adapter type, cable length) |
| **Consumable** | `inventory.consumable` | Per-consumable attributes (batch number, hazard class) |
| **License** | `licenses.license` | Per-license metadata (activation key, concurrent seat limit) |
| **Subscription** | `subscriptions.subscription` | Per-subscription attributes (renewal contact, PO number) |
| **Contract** | `procurement.contract` | Per-contract attributes (vendor ref, payment terms) |

> [!TIP]
> Binding a field to **Asset Type** creates a **hardware specification** —
> a property shared by all assets of that type (e.g. "RAM Slots: 4").
> Binding to **Asset** creates a **per-device detail** (e.g. "Installed RAM
> modules: SK-Hynix 16GB"). Choose the binding that matches what you are
> describing.

You can bind a single custom field to multiple object types. For example, a
"Cost Centre" field could apply to both Assets and Subscriptions.

---

## Required vs Optional

The **Required** checkbox on each custom field controls server-side validation:

| Setting | Behaviour |
|---------|-----------|
| **Required** (checked) | The field must be populated before the object can be saved. The form shows an asterisk and validates on submit. |
| **Optional** (unchecked) | The field can be left blank. |

> [!WARNING]
> Required custom fields are enforced at the **model validation level**.
> This means objects can only be created or modified through the UI or REST
> API if the required custom fields are satisfied. Bulk imports and API
> clients must supply values for all required fields.

---

## Custom Fieldsets

Custom fieldsets let you group related custom fields into logical sections on
object forms. Without fieldsets, all custom fields render together in a single
panel — fine for a handful of fields, but unwieldy when you have dozens.

### Creating a Fieldset

Navigate to **Extras → Custom Fieldsets** and click **Add**:

| Attribute | Description |
|-----------|-------------|
| **Fieldset Name** | Section heading displayed in the form (e.g. "Network Configuration", "Financial Details") |
| **Custom Fields** | The fields to include in this group |

### Assigning Fieldsets to Model Types

Custom fieldsets are automatically rendered on forms based on their member
fields' object type bindings. When a fieldset contains fields bound to
`assets.asset`, the fieldset appears on the Asset edit form. Fields bound to
multiple object types appear in the corresponding fieldsets on each form.

Fields that are **not** assigned to any fieldset still render — they appear
in a default "Custom Fields" panel at the bottom of the form.

### Example

```
Fieldset: "Network Details"
  ├─ IPv4 Address (Text, bound to Asset)
  ├─ VLAN ID (Number, bound to Asset)
  └─ PoE Enabled (Boolean, bound to Asset Type)
```

On an Asset form, the first two fields render in a "Network Details" panel.
On an Asset Type form, only "PoE Enabled" appears in that panel.

---

## Custom Fields in the UI

### Detail Pages

On object detail pages (e.g. an Asset detail view), custom field values are
displayed in a **Custom Fields** card alongside the object's standard
attributes. Fields are grouped by their assigned fieldset; ungrouped fields
appear under a default heading.

### List Views

Custom fields that are bound to the listed model are available as **optional
columns** in the list view. Use the column selector to toggle them on/off
per list.

Custom fields also appear as **filter fields** in the list view filter panel.
Text fields support substring search; number fields support range operators;
select fields render as multi-select dropdowns; boolean fields as checkboxes;
date fields support date-range pickers.

### Exports

Custom field columns are available in the **export column selector** alongside
standard model fields. When you create an export (CSV, JSON, or via an
Export Template), custom field values are included.

### REST API

Custom field values are accessible via the REST API under the `custom_fields`
attribute on each object. Use the **Field Name** (slug) as the key:

```json
{
  "id": 42,
  "asset_tag": "IT-00042",
  "custom_fields": {
    "sim_card_number": "8944100030001234567",
    "support_tier": "Gold"
  }
}
```

---

## Troubleshooting

**Custom field is not appearing on the edit form**
: The field's `object_types` binding does not match the model you are
  viewing. Check the field's configuration — it may be bound to `Asset Type`
  when you expected it on `Asset` (or vice versa).

**Required field prevents saving but is hidden**
: A custom field bound to the model and marked Required is not rendered
  because it was removed from the form template. Check whether any template
  overrides are hiding the custom fields panel. The field must be either
  populated or set to Optional.

**Dropdown shows no choices**
: The field type is Select / Dropdown but the **Choices** field is empty.
  Add one choice per line in the field's Choices textarea.

**Custom field value is missing from exports**
: Ensure the custom field column is toggled on in the export column selector.
  Also verify the field is bound to the model type being exported.

**Fieldset not appearing on forms**
: Fieldsets render only when at least one of their member fields is bound to
  the current object's model type. If none of the fields in the fieldset match
  the form's ContentType, the fieldset is skipped.
