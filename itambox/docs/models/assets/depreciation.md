# Depreciation Profiles

A **Depreciation Profile** maps straight-line monthly financial depreciation curves to assets, allowing you to calculate live bookkeeping values for assets across their active lifecycle.

## Fields

### Convention

Determines whether the acquisition month counts as a full depreciation month.

**Required:** Yes.

### Description

Human-readable description of this depreciation profile.

### Immediate Expense Threshold

Assets with purchase cost at or below this amount are fully expensed in the month of acquisition (e.g. 800 for German GWG).

### Method

Configured method for this record.

**Required:** Yes.

### Lifespan (Months)

Total active timeframe in months over which the asset's value drops to its salvage threshold.

**Required:** Yes.

### Name

Unique name of the profile (e.g. `Laptops - 3 Year Straight Line`).

**Required:** Yes.


## Calculation Logic
ITAMbox uses standard straight-line monthly calculations:
1. **Depreciable Base** = `Purchase Cost` - `Salvage Value`
2. **Monthly Depreciation** = `Depreciable Base` / `Lifespan (Months)`
3. **Current Value** = `Purchase Cost` - (`Monthly Depreciation` * `Months Held`)

* If months held exceeds the lifespan, the asset value stabilizes permanently at `Salvage Value`.
