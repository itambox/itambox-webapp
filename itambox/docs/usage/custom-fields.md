# Custom Fields

Custom Fields add organization-specific data to supported ITAMbox objects without changing the core schema. Use them for information that is genuinely local to your operating model, such as an ERP asset class, internal service tier, or customer-specific support code.

## Create A Custom Field

Define the field name, label, data type, and the object types where it should appear. Configure validation and required/default behavior only when the business rule is stable enough to enforce everywhere the field is edited.

Keep labels understandable to operators. A field called **ERP Asset Class** is easier to maintain than an internal project abbreviation no one recognizes six months later.

## Fieldsets

Custom Fieldsets group related fields for presentation. They help keep forms readable when a tenant or workflow needs several related custom values.

## Validation

Choose the narrowest type that accurately represents the value. Use choices when the allowed vocabulary is controlled. Avoid making a field required until existing records and import/integration paths can reliably supply it.

## APIs And Imports

Custom-field values can be exposed through supported APIs and import/export paths for object types that support them. Integrations should use the field's stable programmatic name rather than screen position.

See [REST and GraphQL](../integration/developer_guide.md) for API behavior and [Bulk Import](../integration/bulk_import_guide.md) for import workflows.

## Governance

Custom Fields are easy to add and costly to remove once reports, imports, or integrations depend on them. Review new fields for ownership, allowed values, and whether the information already has a first-class ITAMbox object.

See [Custom Field Governance](../best-practices/lifecycle-data-quality.md) and the [Custom Field Data Model](../models/extras/customfield.md).
