# Depreciation Calculations

ITAMbox supports straight-line depreciation and a no-depreciation policy in the current release.

## Policy Precedence

The effective policy is selected in this order:

1. Asset override;
2. tenant default;
3. Asset Type policy.

## Straight-Line Concept

Straight-line depreciation spreads the depreciable amount across the configured useful life. The depreciable amount is purchase cost minus salvage value. The result is bounded so it does not continue below the configured salvage value.

The in-service date is used when present; purchase date is the fallback. Missing policy/date/cost information can mean no computed value is available.

ITAMbox records/display values in the configured currency context but does not perform exchange-rate conversion.

For the user workflow, see [Depreciation](../usage/depreciation.md).
