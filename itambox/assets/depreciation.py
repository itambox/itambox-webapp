"""
Pure depreciation math — no ORM, no side effects.

compute_book_value(asset, on_date=None) -> Decimal | None
resolve_policy(asset)                  -> (Depreciation | None, rung_label | None)
"""
import datetime
from decimal import Decimal, ROUND_HALF_UP


def _to_decimal(value):
    """Coerce a money-ish value (float / int / str / Decimal / None) to Decimal.

    Real forms hand us Decimal already; this is defensive hardening for callers
    (e.g. the seed) that assign plain floats. Going via ``str`` avoids binary
    float artefacts (``Decimal(0.1)`` vs ``Decimal('0.1')``).
    """
    if value is None or isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def resolve_policy(asset):
    """
    Resolution chain: asset override → tenant default → asset-type schedule → None.
    Returns (policy, rung) where rung is 'override'|'tenant'|'type'|None.
    """
    override = getattr(asset, 'depreciation_override', None)
    if override is not None:
        return override, 'override'
    tenant = getattr(asset, 'tenant', None)
    if tenant is not None:
        tenant_default = getattr(tenant, 'default_depreciation', None)
        if tenant_default is not None:
            return tenant_default, 'tenant'
    asset_type = getattr(asset, 'asset_type', None)
    if asset_type is not None:
        type_policy = getattr(asset_type, 'depreciation', None)
        if type_policy is not None:
            return type_policy, 'type'
    return None, None


def compute_book_value(asset, on_date=None):
    """
    Returns the straight-line depreciated book value of *asset* on *on_date*.

    Returns None when purchase_cost is absent.
    Returns disposal_value verbatim when asset.disposed_at is set (frozen sign-off).
    Quantises to 2 dp (ROUND_HALF_UP) so the nightly materialisation task only
    writes assets whose value actually changed.
    """
    # Frozen sign-off value takes priority.
    if getattr(asset, 'disposed_at', None) is not None:
        return _to_decimal(asset.disposal_value)

    if not asset.purchase_cost:
        return None

    # Coerce money inputs to Decimal up front. The math below mixes these with
    # Decimal literals and policy DecimalFields; a stray float would raise on
    # ``float / Decimal`` or ``float.quantize``.
    purchase_cost = _to_decimal(asset.purchase_cost)
    salvage = _to_decimal(asset.salvage_value) or Decimal('0.00')

    policy, _ = resolve_policy(asset)

    # No policy or method=none → value equals purchase cost.
    if policy is None:
        return purchase_cost.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    # Import here to avoid circular-import at module load; choices are strings.
    method = getattr(policy, 'method', 'straight_line')
    if method == 'none':
        return purchase_cost.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    if not getattr(policy, 'months', 0) or policy.months <= 0:
        return purchase_cost.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    # Determine depreciation clock start.
    clock_start = getattr(asset, 'in_service_date', None) or asset.purchase_date
    if not clock_start:
        return purchase_cost.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    if on_date is None:
        on_date = datetime.date.today()

    month_diff = (on_date.year - clock_start.year) * 12 + on_date.month - clock_start.month

    convention = getattr(policy, 'convention', 'exclude_purchase_month')
    if convention == 'include_purchase_month':
        months_held = max(month_diff + 1, 0)
    else:
        months_held = max(month_diff, 0)

    # GWG / immediate-expense threshold.
    threshold = getattr(policy, 'immediate_expense_threshold', None)
    if threshold is not None and purchase_cost <= _to_decimal(threshold):
        if months_held >= 1:
            return salvage.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return purchase_cost.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    if months_held == 0:
        return purchase_cost.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    if months_held >= policy.months:
        return salvage.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    depreciable_base = purchase_cost - salvage
    monthly_depreciation = depreciable_base / Decimal(str(policy.months))
    current = purchase_cost - (monthly_depreciation * Decimal(str(months_held)))
    return max(current, salvage).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
