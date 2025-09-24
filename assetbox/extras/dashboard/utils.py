from extras.dashboard.widgets import get_registered_widgets
from extras.models import Dashboard


def get_default_dashboard():
    """Return the default layout config for a fresh dashboard."""
    return [
        # Row 1: High-Level Operational Health (Tactical Traffic-Light Status Overview)
        {'widget': 'financial-overview', 'title': 'Financial Overview', 'visible': True, 'w': 4, 'h': 3, 'style': 'success', 'config': {}},
        {'widget': 'low-stock', 'title': 'Low Stock Alerts', 'visible': True, 'w': 4, 'h': 3, 'style': 'danger', 'config': {}},
        {'widget': 'upcoming-renewals', 'title': 'Upcoming Renewals', 'visible': True, 'w': 4, 'h': 3, 'style': 'warning', 'config': {}},
        
        # Row 2: Interactive Visual Analytics (Gorgeous ApexCharts centerpieces)
        {'widget': 'status-labels', 'title': 'Asset Status Labels', 'visible': True, 'w': 4, 'h': 4, 'style': 'info', 'config': {'chart_type': 'doughnut'}},
        {'widget': 'asset-age', 'title': 'Asset Age Distribution', 'visible': True, 'w': 4, 'h': 4, 'style': 'info', 'config': {'chart_format': 'bar'}},
        {'widget': 'tenant-spend', 'title': 'Tenant Spend', 'visible': True, 'w': 4, 'h': 4, 'style': 'info', 'config': {'limit': 6}},
        
        # Row 3: Scoped Inventory Statistics & Lifecycle Actions
        {'widget': 'object-counts', 'title': 'Object Counts', 'visible': True, 'w': 4, 'h': 3, 'style': 'info', 'config': {
            'models': [
                'assets.asset',
                'organization.site',
                'organization.tenant',
                'licenses.license',
                'components.component',
                'inventory.accessory',
                'inventory.consumable',
                'software.software'
            ],
            'display_style': 'list'
        }},
        {'widget': 'eol-alerts', 'title': 'EOL Planning Alerts', 'visible': True, 'w': 4, 'h': 3, 'style': 'warning', 'config': {}},
        {'widget': 'active-maintenances', 'title': 'Active Repairs & Maintenances', 'visible': True, 'w': 4, 'h': 3, 'style': 'warning', 'config': {}},
        
        # Row 4: Software Entitlements & Onboarding Notes
        {'widget': 'license-utilization', 'title': 'Software License Seats', 'visible': True, 'w': 8, 'h': 3, 'style': 'info', 'config': {}},
        {'widget': 'note', 'title': 'Quick Notes', 'visible': True, 'w': 4, 'h': 3, 'style': 'default', 'config': {
            'content': '# Welcome to AssetBox!\n\nThis is your premium ITAM dashboard. Click the gear icon on any widget to adjust parameters, or click **Unlock** at the top right to drag, resize, or reorder elements.'
        }},
        
        # Row 5: Audit Log Trails (Full-Width changelog with absolute fidelity)
        {'widget': 'recent-activity', 'title': 'Change Log', 'visible': True, 'w': 12, 'h': 4, 'style': 'default', 'config': {'limit': 8}},
    ]


def get_dashboard(user, for_update=False):
    """Get or create a dashboard for the given user.

    Args:
        user: The user whose dashboard to fetch.
        for_update: If True, locks the row with SELECT ... FOR UPDATE
                    to prevent concurrent mutation race conditions.
    """
    qs = Dashboard.objects.all()
    if for_update:
        qs = qs.select_for_update()
    dashboard, created = qs.get_or_create(user=user)
    if created or not dashboard.layout:
        dashboard.layout = get_default_dashboard()
        dashboard.save(update_fields=['layout'])
    return dashboard
