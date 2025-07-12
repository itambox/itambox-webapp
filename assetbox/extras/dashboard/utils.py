from extras.dashboard.widgets import get_registered_widgets
from extras.models import Dashboard


def get_default_dashboard():
    """Return the default layout config for a fresh dashboard."""
    return [
        # Row 1: Key overview widgets
        {'widget': 'status-labels', 'title': 'Asset Status Labels', 'visible': True, 'w': 4, 'h': 3, 'config': {}},
        {'widget': 'financial-overview', 'title': 'Financial Overview', 'visible': True, 'w': 4, 'h': 3, 'config': {}},
        {'widget': 'object-counts', 'title': 'Object Counts', 'visible': True, 'w': 4, 'h': 3, 'config': {'models': ['assets.asset', 'organization.site', 'organization.tenant', 'licenses.license']}},
        
        # Row 2: Alerts and monitoring
        {'widget': 'eol-alerts', 'title': 'EOL Planning Alerts', 'visible': True, 'w': 4, 'h': 2, 'config': {}},
        {'widget': 'active-maintenances', 'title': 'Active Repairs & Maintenances', 'visible': True, 'w': 4, 'h': 2, 'config': {}},
        {'widget': 'low-stock', 'title': 'Low Stock Alerts', 'visible': True, 'w': 4, 'h': 2, 'config': {}},
        
        # Row 3: Data and trends
        {'widget': 'license-utilization', 'title': 'Software License Seats', 'visible': True, 'w': 4, 'h': 2, 'config': {}},
        {'widget': 'upcoming-renewals', 'title': 'Upcoming Renewals', 'visible': True, 'w': 4, 'h': 2, 'config': {}},
        {'widget': 'asset-age', 'title': 'Asset Age Distribution', 'visible': True, 'w': 4, 'h': 2, 'config': {}},
        
        # Row 4: Quick reference
        {'widget': 'note', 'title': 'Quick Notes', 'visible': True, 'w': 6, 'h': 3, 'config': {'content': ''}},
        {'widget': 'tenant-spend', 'title': 'Tenant Spend', 'visible': True, 'w': 6, 'h': 3, 'config': {}},
        {'widget': 'recent-activity', 'title': 'Change Log', 'visible': True, 'w': 12, 'h': 3, 'config': {}},
    ]

def get_dashboard(user):
    """Get or create a dashboard for the given user."""
    dashboard, created = Dashboard.objects.get_or_create(user=user)
    if created or not dashboard.layout:
        dashboard.layout = get_default_dashboard()
        dashboard.save(update_fields=['layout'])
    return dashboard
