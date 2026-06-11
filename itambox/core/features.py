BETA = "beta"
STABLE = "stable"

# App-level maturity grades. Sub-module beta areas (reports, webhooks/event rules,
# SCIM) are handled by view-level overrides since they share an app with stable code.
MODULE_MATURITY = {
    "subscriptions": BETA,
    "procurement": BETA,
}


def module_maturity(app_label: str) -> str:
    """Return 'beta' or 'stable' for the given Django app label."""
    return MODULE_MATURITY.get(app_label, STABLE)
