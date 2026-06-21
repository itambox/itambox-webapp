# Depreciation Profiles

A **Depreciation Profile** maps straight-line monthly financial depreciation curves to assets, allowing you to calculate live bookkeeping values for assets across their active lifecycle.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Convention** | Determines whether the acquisition month counts as a full depreciation month. | Choice | Yes |
| **Description** | The description of the depreciation. | Text | No |
| **Immediate Expense Threshold** | Assets with purchase cost at or below this amount are fully expensed in the month of acquisition (e.g. 800 for German GWG). | Decimal | No |
| **Method** | The method of the depreciation. | Choice | Yes |
| **Lifespan (Months)** | Total active timeframe in months over which the asset's value drops to its salvage threshold. | Integer | Yes |
| **Name** | Unique name of the profile (e.g. `Laptops - 3 Year Straight Line`). | String | Yes |

## Calculation Logic
ITAMbox uses standard straight-line monthly calculations:
1. **Depreciable Base** = `Purchase Cost` - `Salvage Value`
2. **Monthly Depreciation** = `Depreciable Base` / `Lifespan (Months)`
3. **Current Value** = `Purchase Cost` - (`Monthly Depreciation` * `Months Held`)

* If months held exceeds the lifespan, the asset value stabilizes permanently at `Salvage Value`.
