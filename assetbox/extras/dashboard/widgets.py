from datetime import date, timedelta
from django import forms
from django.db.models import Sum, Count, Q, Avg, F
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.template.loader import render_to_string
from assets.models import (
    Asset, AssetMaintenance, Accessory, Consumable,
    ActivityLog, StatusLabel,
)
from licenses.models import License
from subscriptions.models import Subscription


# -----------------------------------------------------------------------------
# Widget Registry
# -----------------------------------------------------------------------------

_registry = {}


def register_widget(cls):
    """Register a DashboardWidget subclass."""
    _registry[cls.widget_id] = cls
    return cls


def get_widget(widget_id):
    """Get a registered widget class by its widget_id."""
    return _registry.get(widget_id)


def get_registered_widgets():
    """Return all registered widget classes."""
    return list(_registry.values())


# -----------------------------------------------------------------------------
# Base Widget
# -----------------------------------------------------------------------------

class WidgetConfigForm(forms.Form):
    pass


class DashboardWidget:
    widget_id = None            # Unique string ID (set in subclasses)
    title = ''                  # Default display title
    description = ''            # Short description shown in add-widget modal
    template_name = None        # Template for widget body content

    def __init__(self, config=None):
        self.config = config or {}

    class ConfigForm(WidgetConfigForm):
        pass

    def get_config_value(self, key, default=None):
        cfg = self.config.get("config", {}) if isinstance(self.config, dict) else {}
        return cfg.get(key, default)

    def get_config_form(self, data=None):
        cls = type(self).ConfigForm
        if not cls.declared_fields:
            return WidgetConfigForm(data=data)
        initial = self.config.get("config", {}) if isinstance(self.config, dict) else {}
        return cls(data=data, initial=initial or {})


    @property
    def display_title(self):
        return self.config.get('title') or self.title

    def get_template_name(self):
        return self.template_name

    def get_context(self, request):
        """Return a dict of context to render the widget template. Override in subclasses."""
        return {}

    def render(self, request):
        """Render the widget body HTML."""
        ctx = self.get_context(request)
        ctx['widget'] = self
        return render_to_string(self.get_template_name(), ctx, request=request)


# -----------------------------------------------------------------------------
# Widget Subclasses (one per dashboard card)
# -----------------------------------------------------------------------------

@register_widget
class NoteWidget(DashboardWidget):
    widget_id = 'note'
    title = 'Note'
    description = 'Display arbitrary custom content. Markdown is supported.'
    template_name = 'extras/dashboard/widgets/note.html'

    class ConfigForm(WidgetConfigForm):
        content = forms.CharField(
            label='Content',
            widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 6}),
            required=False
        )

    def get_context(self, request):
        return {'content': self.get_config_value('content', '')}


OBJECT_COUNT_MODEL_CHOICES = [
    ('assets.asset', 'Assets'),
    ('assets.assettype', 'Asset Types'),
    ('assets.manufacturer', 'Manufacturers'),
    ('assets.statuslabel', 'Status Labels'),
    ('organization.site', 'Sites'),
    ('organization.tenant', 'Tenants'),
    ('organization.location', 'Locations'),
    ('licenses.license', 'Licenses'),
    ('subscriptions.subscription', 'Subscriptions'),
    ('software.software', 'Software'),
]

@register_widget
class ObjectCountsWidget(DashboardWidget):
    widget_id = 'object-counts'
    title = 'Object Counts'
    description = 'Display counts of object types with links to their list views.'
    template_name = 'extras/dashboard/widgets/object_counts.html'

    

    class ConfigForm(WidgetConfigForm):
        models = forms.MultipleChoiceField(
            label='Models',
            choices=OBJECT_COUNT_MODEL_CHOICES,
            widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
            required=False
        )

    def _get_model_label(self, key):
        for k, label in OBJECT_COUNT_MODEL_CHOICES:
            if k == key:
                return label
        return key

    def get_context(self, request):
        selected = self.get_config_value('models', [])
        if not selected:
            return {'counts': [], 'has_data': False}
        from organization.models import Site, Tenant, Location
        from software.models import Software
        from assets.models import AssetType, Manufacturer
        model_map = {
            'assets.asset': (Asset, 'assets:asset_list'),
            'assets.assettype': (AssetType, 'assets:assettype_list'),
            'assets.manufacturer': (Manufacturer, 'assets:manufacturer_list'),
            'assets.statuslabel': (StatusLabel, 'assets:statuslabel_list'),
            'organization.site': (Site, 'organization:site_list'),
            'organization.tenant': (Tenant, 'organization:tenant_list'),
            'organization.location': (Location, 'organization:location_list'),
            'licenses.license': (License, 'licenses:license_list'),
            'subscriptions.subscription': (Subscription, 'subscriptions:subscription_list'),
            'software.software': (Software, 'software:software_list'),
        }
        counts = []
        for key in selected:
            info = model_map.get(key)
            if info is None:
                continue
            model_cls, url_name = info
            try:
                qs = model_cls.objects.all()
                count = qs.count()
                label = self._get_model_label(key)
                url = reverse(url_name)
                counts.append({'label': label, 'count': count, 'url': url})
            except Exception:
                counts.append({'label': self._get_model_label(key), 'count': '?', 'url': None})
        return {'counts': counts, 'has_data': True}





@register_widget
class FinancialWidget(DashboardWidget):
    widget_id = 'financial-overview'
    title = 'Financial Overview'
    description = 'Total cost of ownership, purchase costs, maintenance, and salvage values'
    template_name = 'extras/dashboard/widgets/financial.html'

    def get_context(self, request):
        assets = Asset.objects.all()
        total_purchase = assets.aggregate(total=Sum('purchase_cost'))['total'] or 0.0
        total_salvage = assets.aggregate(total=Sum('salvage_value'))['total'] or 0.0
        total_maintenance = AssetMaintenance.objects.aggregate(
            total=Sum('cost')
        )['total'] or 0.0
        total_tco = total_purchase + total_maintenance
        return {
            'total_tco': total_tco,
            'total_purchase_cost': total_purchase,
            'total_maintenance_cost': total_maintenance,
            'total_salvage_value': total_salvage,
        }


@register_widget
class StatusLabelsWidget(DashboardWidget):
    widget_id = 'status-labels'
    title = 'Asset Status Labels'
    description = 'Donut chart showing asset distribution by status label'
    template_name = 'extras/dashboard/widgets/status_labels.html'

    def get_context(self, request):
        from assets.models import StatusLabel
        statuses = StatusLabel.objects.annotate(
            asset_count=Count('assets')
        ).order_by('-asset_count')
        return {
            'total_assets': Asset.objects.count(),
            'status_stats': statuses,
        }


@register_widget
class LicenseWidget(DashboardWidget):
    widget_id = 'license-utilization'
    title = 'Software License Seats'
    description = 'Top 5 licenses by seat utilization percentage'
    template_name = 'extras/dashboard/widgets/licenses.html'

    def get_context(self, request):
        stats = []
        for lic in License.objects.with_counts():
            total = lic.seats
            allocated = lic.assigned_count
            pct = round((allocated / total) * 100) if total > 0 else 0
            stats.append({'license': lic, 'total': total, 'allocated': allocated, 'util_pct': pct})
        stats.sort(key=lambda x: x['util_pct'], reverse=True)
        return {'license_stats': stats[:5]}


@register_widget
class MaintenanceWidget(DashboardWidget):
    widget_id = 'active-maintenances'
    title = 'Active Repairs & Maintenances'
    description = 'Ongoing repairs and maintenance tasks with associated costs'
    template_name = 'extras/dashboard/widgets/maintenances.html'

    def get_context(self, request):
        maintenances = AssetMaintenance.objects.filter(
            completion_date__isnull=True
        ).select_related('asset').order_by('-start_date')
        return {
            'active_maintenances': maintenances,
            'active_maintenance_count': maintenances.count(),
        }


@register_widget
class EOLAlertsWidget(DashboardWidget):
    widget_id = 'eol-alerts'
    title = 'EOL Planning Alerts'
    description = 'Hardware expiring within 90 days or already past EOL'
    template_name = 'extras/dashboard/widgets/eol_alerts.html'

    def get_context(self, request):
        today = date.today()
        alerts = []
        assets = Asset.objects.filter(
            purchase_date__isnull=False,
            asset_type__eol_months__isnull=False
        ).select_related('asset_type')
        for asset in assets:
            eol = asset.eol_date
            if eol is None:
                continue
            days_left = (eol - today).days
            if days_left > 90:
                continue
            alerts.append({'asset': asset, 'days_left': days_left, 'eol_date': eol})
        return {'eol_alerts': sorted(alerts, key=lambda a: a['days_left'])}


@register_widget
class ChangelogWidget(DashboardWidget):
    widget_id = 'recent-activity'
    title = 'Change Log'
    description = 'Recent object changes across the system (create, update, delete)'
    template_name = 'extras/dashboard/widgets/activity.html'

    def get_context(self, request):
        from core.models import ObjectChange
        changes = ObjectChange.objects.select_related(
            'user', 'changed_object_type'
        ).prefetch_related('changed_object')[:10]
        return {'recent_changes': changes}


@register_widget
class RenewalsWidget(DashboardWidget):
    widget_id = 'upcoming-renewals'
    title = 'Upcoming Renewals'
    description = 'Active subscriptions renewing within 90 days'
    template_name = 'extras/dashboard/widgets/renewals.html'

    def get_context(self, request):
        today = date.today()
        cutoff = today + timedelta(days=90)
        subs = Subscription.objects.filter(
            status='active',
            renewal_date__isnull=False,
            renewal_date__lte=cutoff,
            renewal_date__gte=today,
        ).select_related('provider').order_by('renewal_date')

        result = []
        for sub in subs:
            result.append({
                'pk': sub.pk,
                'name': sub.name,
                'provider': sub.provider,
                'days_until_renewal': (sub.renewal_date - today).days,
                'renewal_cost': sub.renewal_cost,
                'currency': sub.currency,
            })

        total_spend = Subscription.objects.filter(status='active').aggregate(
            total=Sum('renewal_cost')
        )['total'] or 0

        return {
            'upcoming_renewals': result,
            'total_subscription_spend': total_spend,
        }


@register_widget
class LowStockWidget(DashboardWidget):
    widget_id = 'low-stock'
    title = 'Low Stock Alerts'
    description = 'Accessories and consumables below minimum quantity'
    template_name = 'extras/dashboard/widgets/low_stock.html'

    def get_context(self, request):
        accessories = Accessory.objects.filter(
            qty__lt=F('min_qty')
        ).filter(min_qty__gt=0).order_by('qty')

        consumables = Consumable.objects.filter(
            qty__lt=F('min_qty')
        ).filter(min_qty__gt=0).order_by('qty')

        return {
            'low_stock_accessories': accessories,
            'low_stock_consumables': consumables,
            'low_stock_accessory_count': accessories.count(),
            'low_stock_consumable_count': consumables.count(),
        }


@register_widget
class AssetAgeWidget(DashboardWidget):
    widget_id = 'asset-age'
    title = 'Asset Age Distribution'
    description = 'Breakdown of assets by age bucket and average age'
    template_name = 'extras/dashboard/widgets/asset_age.html'

    def get_context(self, request):
        today = date.today()
        assets = Asset.objects.filter(purchase_date__isnull=False)

        buckets = {'lt1y': 0, '1_3y': 0, '3_5y': 0, '5_7y': 0, 'gt7y': 0}
        total_age = 0
        count = 0

        for asset in assets:
            age_years = (today - asset.purchase_date).days / 365.25
            total_age += age_years
            count += 1
            if age_years < 1:
                buckets['lt1y'] += 1
            elif age_years < 3:
                buckets['1_3y'] += 1
            elif age_years < 5:
                buckets['3_5y'] += 1
            elif age_years < 7:
                buckets['5_7y'] += 1
            else:
                buckets['gt7y'] += 1

        avg_age = round(total_age / count, 1) if count > 0 else 0
        return {
            'age_buckets': buckets,
            'avg_asset_age_years': avg_age,
        }


@register_widget
class TenantSpendWidget(DashboardWidget):
    widget_id = 'tenant-spend'
    title = 'Tenant Spend'
    description = 'Purchase cost grouped by tenant (top 8)'
    template_name = 'extras/dashboard/widgets/tenant_spend.html'

    def get_context(self, request):
        spend = Asset.objects.values('tenant__name').annotate(
            total=Sum('purchase_cost')
        ).order_by('-total')[:8]
        return {'tenant_spend': list(spend)}
