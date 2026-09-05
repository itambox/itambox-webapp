# Depreciation

Depreciation lets ITAMbox estimate the book value of an asset over time. It is financial tracking inside the asset record, not an accounting posting or currency-conversion system.

## Which Policy Applies

When ITAMbox needs a depreciation policy, the most specific configured policy wins:

1. the Asset's depreciation override;
2. the tenant default depreciation policy;
3. the Asset Type depreciation policy.

If no applicable policy exists, ITAMbox does not invent one.

## Dates And Values

The in-service date is the preferred start date. When it is not set, purchase date can be used as the fallback. Purchase cost and salvage value determine the amount available to depreciate.

A missing or zero purchase cost cannot produce a meaningful monetary book value. Currency fields are displayed as recorded; ITAMbox does not perform foreign-exchange conversion.

Supported policies include straight-line depreciation and no depreciation. See [Depreciation Calculations](../reference/depreciation-calculations.md) for the exact calculation concepts.

## Disposal

When an asset is disposed, the disposal workflow records the end-of-life event and relevant value. That historical point should be treated separately from an asset that is still actively depreciating.

## Operational Use

Use depreciation for questions such as:

- which devices are nearing the end of their planned useful life;
- what estimated book value remains in a tenant's hardware inventory;
- whether replacement planning aligns with financial lifecycle assumptions.

Do not treat the computed value as a substitute for the organization's accounting ledger or tax advice.

See the [Depreciation Profile](../models/assets/depreciation.md) and [Asset](../models/assets/asset.md) Data Model pages for stored fields.
