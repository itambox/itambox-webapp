from datetime import date, timedelta
import json
from django import forms
from django.db.models import Sum, Count, Q, Avg, F, Case, When, Value, IntegerField
from django.db.models.functions import Extract, Coalesce
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.template.loader import render_to_string
from django.core.exceptions import PermissionDenied

from assets.models import Asset, ActivityLog, StatusLabel
from compliance.models import AssetMaintenance
from inventory.models import Accessory, Consumable
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
# Secure Scoping Scoping & Multitenancy Helper
# -----------------------------------------------------------------------------

def get_scoped_queryset(model_class, request, config=None):
    """
    Safely resolves and returns a model queryset constrained by active tenant boundaries
    and optional regional/site preferences defined in the widget config.
    """
    qs = model_class.objects.all()
    config = config or {}
    user = request.user

    if not user or not user.is_authenticated:
        return qs.none()

    # 1. Resolve Active Tenant boundary
    is_global_admin = user.is_superuser or (hasattr(user, 'is_staff') and user.is_staff)
    active_tenant = None

    if not is_global_admin:
        # Standard tenant-bound users are strictly sandboxed to their tenant
        if hasattr(user, 'asset_holder_profile') and user.asset_holder_profile is not None:
            active_tenant = user.asset_holder_profile.tenant
    else:
        # Global Admin or Superuser: Can view system-wide or select a target tenant in config
        tenant_id = config.get('tenant_id')
        if tenant_id:
            from organization.models import Tenant
            active_tenant = Tenant.objects.filter(id=tenant_id).first()

    if active_tenant:
        if hasattr(model_class, 'tenant'):
            qs = qs.filter(tenant=active_tenant)
        elif model_class.__name__ == 'AssetMaintenance':
            qs = qs.filter(asset__tenant=active_tenant)
        elif model_class.__name__ == 'SubscriptionAssignment':
            qs = qs.filter(subscription__tenant=active_tenant)
        elif model_class.__name__ == 'LicenseSeatAssignment':
            qs = qs.filter(license__tenant=active_tenant)
        elif model_class.__name__ == 'ObjectChange':
            # Partition audit logs to changes made by members of this tenant
            qs = qs.filter(user__asset_holder_profile__tenant=active_tenant)
    elif not is_global_admin:
        # Standard users without an active tenant profile are restricted by default
        return qs.none()

    return qs


# --- Proxy Wrappers for Inventory ---

class ScopedAccessoryWrapper:
    """Wraps an Accessory model instance, exposing the scoped available quantity."""
    def __init__(self, accessory, available):
        self.accessory = accessory
        self._available = available

    def __getattr__(self, name):
        return getattr(self.accessory, name)

    @property
    def available(self):
        return self._available


class ScopedConsumableWrapper:
    """Wraps a Consumable model instance, exposing the scoped available quantity."""
    def __init__(self, consumable, available):
        self.consumable = consumable
        self._available = available

    def __getattr__(self, name):
        return getattr(self.consumable, name)

    @property
    def available(self):
        return self._available


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
    admin_only = False          # Restricted to global administrators

    def __init__(self, config=None):
        self.config = config or {}

    class ConfigForm(WidgetConfigForm):
        pass

    def get_config_value(self, key, default=None):
        cfg = self.config.get("config", {}) if isinstance(self.config, dict) else {}
        return cfg.get(key, default)

    def get_config_form(self, data=None, request=None):
        cls = type(self).ConfigForm
        initial = self.config.get("config", {}) if isinstance(self.config, dict) else {}
        form = cls(data=data, initial=initial or {})
        
        # If request is provided and user is staff/superuser, dynamically inject a 'tenant_id' field
        if request and (request.user.is_superuser or (hasattr(request.user, 'is_staff') and request.user.is_staff)):
            if self.widget_id != 'tenant-spend': # exclude tenant-spend widget which is global
                from organization.models import Tenant
                tenants = Tenant.objects.all().order_by('name')
                choices = [('', 'All Tenants')] + [(str(t.id), t.name) for t in tenants]
                form.fields['tenant_id'] = forms.ChoiceField(
                    label='Target Tenant Context',
                    choices=choices,
                    required=False,
                    initial=initial.get('tenant_id', ''),
                    widget=forms.Select(attrs={'class': 'form-select'}),
                    help_text='Scope this widget to a specific tenant.'
                )
        return form

    def has_permission(self, request):
        if self.admin_only:
            user = request.user
            return user.is_superuser or (hasattr(user, 'is_staff') and user.is_staff)
        return True

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
        if not self.has_permission(request):
            return mark_safe(f'<div class="text-danger text-center py-4">Restricted to Global Administrators.</div>')
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
        style = forms.ChoiceField(
            label='Style',
            choices=[
                ('default', 'Default'),
                ('info', 'Info (Blue)'),
                ('warning', 'Warning (Yellow)'),
                ('success', 'Success (Green)'),
                ('danger', 'Danger (Red)'),
            ],
            initial='default',
            widget=forms.Select(attrs={'class': 'form-select'}),
            required=False
        )

    def get_context(self, request):
        return {
            'content': self.get_config_value('content', ''),
            'style': self.get_config_value('style', 'default'),
        }


OBJECT_COUNT_MODEL_CHOICES = [
    ('assets.asset', 'Assets'),
    ('assets.assettype', 'Asset Types'),
    ('assets.manufacturer', 'Manufacturers'),
    ('assets.statuslabel', 'Status Labels'),
    ('components.component', 'Components'),
    ('inventory.accessory', 'Accessories'),
    ('inventory.consumable', 'Consumables'),
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
        display_style = forms.ChoiceField(
            label='Display Style',
            choices=[
                ('grid', 'Badge Grid'),
                ('list', 'List View'),
            ],
            initial='grid',
            widget=forms.Select(attrs={'class': 'form-select'}),
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
        from components.models import Component
        from assets.models import AssetType, Manufacturer
        model_map = {
            'assets.asset': (Asset, 'assets:asset_list'),
            'assets.assettype': (AssetType, 'assets:assettype_list'),
            'assets.manufacturer': (Manufacturer, 'assets:manufacturer_list'),
            'assets.statuslabel': (StatusLabel, 'assets:statuslabel_list'),
            'components.component': (Component, 'assets:component_list'),
            'inventory.accessory': (Accessory, 'inventory:accessory_list'),
            'inventory.consumable': (Consumable, 'inventory:consumable_list'),
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
                scoped_qs = get_scoped_queryset(model_cls, request, config=self.config.get("config", {}))
                count = scoped_qs.count()
                label = self._get_model_label(key)
                url = reverse(url_name)
                counts.append({'label': label, 'count': count, 'url': url})
            except Exception:
                counts.append({'label': self._get_model_label(key), 'count': '?', 'url': None})
        return {
            'counts': counts,
            'has_data': True,
            'display_style': self.get_config_value('display_style', 'grid'),
        }


@register_widget
class FinancialWidget(DashboardWidget):
    widget_id = 'financial-overview'
    title = 'Financial Overview'
    description = 'Total cost of ownership, purchase costs, maintenance, and salvage values'
    template_name = 'extras/dashboard/widgets/financial.html'

    class ConfigForm(WidgetConfigForm):
        currency = forms.CharField(
            label='Currency Symbol',
            max_length=5,
            initial='$',
            widget=forms.TextInput(attrs={'class': 'form-control'}),
            required=False
        )
        budget_limit = forms.DecimalField(
            label='Budget Limit',
            max_digits=12,
            decimal_places=2,
            widget=forms.NumberInput(attrs={'class': 'form-control'}),
            required=False,
            help_text='If set, progress bars will reflect percentages against this budget.'
        )

    def get_context(self, request):
        assets = get_scoped_queryset(Asset, request, config=self.config.get("config", {}))
        maintenances = get_scoped_queryset(AssetMaintenance, request, config=self.config.get("config", {}))

        total_purchase = assets.aggregate(total=Sum('purchase_cost'))['total'] or 0.0
        total_salvage = assets.aggregate(total=Sum('salvage_value'))['total'] or 0.0
        total_maintenance = maintenances.aggregate(total=Sum('cost'))['total'] or 0.0
        total_tco = total_purchase + total_maintenance
        
        currency = self.get_config_value('currency', '$')
        budget_limit = self.get_config_value('budget_limit')
        if budget_limit:
            budget_limit = float(budget_limit)

        return {
            'total_tco': total_tco,
            'total_purchase_cost': total_purchase,
            'total_maintenance_cost': total_maintenance,
            'total_salvage_value': total_salvage,
            'currency': currency,
            'budget_limit': budget_limit,
        }


@register_widget
class StatusLabelsWidget(DashboardWidget):
    widget_id = 'status-labels'
    title = 'Asset Status Labels'
    description = 'Donut chart showing asset distribution by status label'
    template_name = 'extras/dashboard/widgets/status_labels.html'

    class ConfigForm(WidgetConfigForm):
        chart_type = forms.ChoiceField(
            label='Chart Type',
            choices=[
                ('doughnut', 'Doughnut'),
                ('pie', 'Pie'),
                ('bar', 'Bar'),
                ('list', 'Simple List'),
            ],
            initial='doughnut',
            widget=forms.Select(attrs={'class': 'form-select'}),
            required=False
        )

    def get_context(self, request):
        user = request.user
        is_global_admin = user.is_superuser or (hasattr(user, 'is_staff') and user.is_staff)
        active_tenant = None

        if not is_global_admin:
            if hasattr(user, 'asset_holder_profile') and user.asset_holder_profile is not None:
                active_tenant = user.asset_holder_profile.tenant
        else:
            tenant_id = self.get_config_value('tenant_id')
            if tenant_id:
                from organization.models import Tenant
                active_tenant = Tenant.objects.filter(id=tenant_id).first()

        if active_tenant:
            statuses = StatusLabel.objects.annotate(
                asset_count=Count('assets', filter=Q(assets__tenant=active_tenant))
            ).order_by('-asset_count')
            total_assets = Asset.objects.filter(tenant=active_tenant).count()
        else:
            statuses = StatusLabel.objects.annotate(
                asset_count=Count('assets')
            ).order_by('-asset_count')
            total_assets = Asset.objects.count()

        return {
            'total_assets': total_assets,
            'status_stats': statuses,
            'chart_type': self.get_config_value('chart_type', 'doughnut'),
        }


@register_widget
class LicenseWidget(DashboardWidget):
    widget_id = 'license-utilization'
    title = 'Software License Seats'
    description = 'Top 5 licenses by seat utilization percentage'
    template_name = 'extras/dashboard/widgets/licenses.html'

    class ConfigForm(WidgetConfigForm):
        limit = forms.IntegerField(
            label='Limit to Top N',
            initial=5,
            widget=forms.NumberInput(attrs={'class': 'form-control'}),
            required=False
        )
        warning_threshold = forms.DecimalField(
            label='Warning Threshold (%)',
            initial=85.0,
            widget=forms.NumberInput(attrs={'class': 'form-control'}),
            required=False,
            help_text='Threshold percentage to flag high utilization.'
        )

    def get_context(self, request):
        stats = []
        licenses = get_scoped_queryset(License, request, config=self.config.get("config", {}))
        for lic in licenses.with_counts():
            total = lic.seats
            allocated = lic.assigned_count
            pct = round((allocated / total) * 100) if total > 0 else 0
            stats.append({'license': lic, 'total': total, 'allocated': allocated, 'util_pct': pct})
        stats.sort(key=lambda x: x['util_pct'], reverse=True)

        limit = self.get_config_value('limit', 5)
        warning_threshold = float(self.get_config_value('warning_threshold', 85.0))

        return {
            'license_stats': stats[:limit],
            'warning_threshold': warning_threshold,
        }


@register_widget
class MaintenanceWidget(DashboardWidget):
    widget_id = 'active-maintenances'
    title = 'Active Repairs & Maintenances'
    description = 'Ongoing repairs and maintenance tasks with associated costs'
    template_name = 'extras/dashboard/widgets/maintenances.html'

    class ConfigForm(WidgetConfigForm):
        limit = forms.IntegerField(
            label='Limit to Top N',
            initial=10,
            widget=forms.NumberInput(attrs={'class': 'form-control'}),
            required=False
        )
        highlight_overdue = forms.BooleanField(
            label='Highlight Overdue',
            initial=True,
            widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            required=False,
            help_text='Flag repairs that have exceeded expected timelines.'
        )

    def get_context(self, request):
        limit = self.get_config_value('limit', 10)
        maintenances = get_scoped_queryset(AssetMaintenance, request, config=self.config.get("config", {})).filter(
            completion_date__isnull=True
        ).select_related('asset').order_by('-start_date')
        return {
            'active_maintenances': maintenances[:limit],
            'active_maintenance_count': maintenances.count(),
            'highlight_overdue': self.get_config_value('highlight_overdue', True),
        }


@register_widget
class EOLAlertsWidget(DashboardWidget):
    widget_id = 'eol-alerts'
    title = 'EOL Planning Alerts'
    description = 'Hardware expiring within 90 days or already past EOL'
    template_name = 'extras/dashboard/widgets/eol_alerts.html'

    class ConfigForm(WidgetConfigForm):
        days_horizon = forms.ChoiceField(
            label='Planning Horizon',
            choices=[
                ('30', '30 Days'),
                ('90', '90 Days (Default)'),
                ('180', '180 Days'),
                ('365', '365 Days'),
            ],
            initial='90',
            widget=forms.Select(attrs={'class': 'form-select'}),
            required=False
        )

    def get_context(self, request):
        today = date.today()
        days_horizon = int(self.get_config_value('days_horizon', 90))
        alerts = []
        queryset = get_scoped_queryset(Asset, request, config=self.config.get("config", {})).filter(
            purchase_date__isnull=False,
            asset_type__eol_months__isnull=False
        ).select_related('asset_type')

        for asset in queryset.iterator(chunk_size=500):
            eol = asset.eol_date
            if eol is None:
                continue
            days_left = (eol - today).days
            if days_left > days_horizon:
                continue
            alerts.append({'asset': asset, 'days_left': days_left, 'eol_date': eol})

        return {
            'eol_alerts': sorted(alerts, key=lambda a: a['days_left']),
            'days_horizon': days_horizon,
        }


@register_widget
class ChangelogWidget(DashboardWidget):
    widget_id = 'recent-activity'
    title = 'Change Log'
    description = 'Recent object changes across the system (create, update, delete)'
    template_name = 'extras/dashboard/widgets/activity.html'

    class ConfigForm(WidgetConfigForm):
        limit = forms.IntegerField(
            label='Max Items to Display',
            initial=10,
            widget=forms.NumberInput(attrs={'class': 'form-control'}),
            required=False
        )

    def get_context(self, request):
        from core.models import ObjectChange
        limit = self.get_config_value('limit', 10)
        qs = get_scoped_queryset(ObjectChange, request, config=self.config.get("config", {}))
        changes = qs.select_related(
            'user', 'changed_object_type'
        ).prefetch_related('changed_object')[:limit]
        return {'recent_changes': changes}


@register_widget
class RenewalsWidget(DashboardWidget):
    widget_id = 'upcoming-renewals'
    title = 'Upcoming Renewals'
    description = 'Active subscriptions renewing within 90 days'
    template_name = 'extras/dashboard/widgets/renewals.html'

    class ConfigForm(WidgetConfigForm):
        days_horizon = forms.ChoiceField(
            label='Planning Horizon',
            choices=[
                ('30', '30 Days'),
                ('60', '60 Days'),
                ('90', '90 Days (Default)'),
                ('180', '180 Days'),
            ],
            initial='90',
            widget=forms.Select(attrs={'class': 'form-select'}),
            required=False
        )
        limit = forms.IntegerField(
            label='Max Items to Display',
            initial=10,
            widget=forms.NumberInput(attrs={'class': 'form-control'}),
            required=False
        )

    def get_context(self, request):
        today = date.today()
        days_horizon = int(self.get_config_value('days_horizon', 90))
        limit = self.get_config_value('limit', 10)
        cutoff = today + timedelta(days=days_horizon)
        
        scoped_subs = get_scoped_queryset(Subscription, request, config=self.config.get("config", {}))
        subs = scoped_subs.filter(
            status='active',
            renewal_date__isnull=False,
            renewal_date__lte=cutoff,
            renewal_date__gte=today,
        ).select_related('provider').order_by('renewal_date')[:limit]

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

        total_spend = scoped_subs.filter(status='active').aggregate(
            total=Sum('renewal_cost')
        )['total'] or 0

        return {
            'upcoming_renewals': result,
            'total_subscription_spend': total_spend,
            'days_horizon': days_horizon,
        }


@register_widget
class LowStockWidget(DashboardWidget):
    widget_id = 'low-stock'
    title = 'Low Stock Alerts'
    description = 'Accessories, consumables, and components below minimum quantity'
    template_name = 'extras/dashboard/widgets/low_stock.html'

    class ConfigForm(WidgetConfigForm):
        include_accessories = forms.BooleanField(
            label='Include Accessories',
            initial=True,
            widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            required=False
        )
        include_consumables = forms.BooleanField(
            label='Include Consumables',
            initial=True,
            widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            required=False
        )
        include_components = forms.BooleanField(
            label='Include Components',
            initial=True,
            widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            required=False
        )

    def get_context(self, request):
        user = request.user
        is_global_admin = user.is_superuser or (hasattr(user, 'is_staff') and user.is_staff)
        active_tenant = None

        if not is_global_admin:
            if hasattr(user, 'asset_holder_profile') and user.asset_holder_profile is not None:
                active_tenant = user.asset_holder_profile.tenant
        else:
            tenant_id = self.get_config_value('tenant_id')
            if tenant_id:
                from organization.models import Tenant
                active_tenant = Tenant.objects.filter(id=tenant_id).first()

        include_acc = self.get_config_value('include_accessories', True)
        include_con = self.get_config_value('include_consumables', True)
        include_comp = self.get_config_value('include_components', True)

        # Scoped Accessory calculation
        low_accessories = []
        if include_acc:
            acc_qs = get_scoped_queryset(Accessory, request, config=self.config.get("config", {}))
            for acc in acc_qs.filter(min_qty__gt=0):
                # Scoped total stock in locations belonging to this tenant
                stock_qs = acc.stocks.all()
                if active_tenant:
                    stock_qs = stock_qs.filter(location__tenant=active_tenant)
                total_stock = stock_qs.aggregate(total=Sum('qty'))['total'] or 0

                # Scoped checked out quantity
                assignment_qs = acc.assignments.all()
                if active_tenant:
                    assignment_qs = assignment_qs.filter(
                        Q(assigned_location__tenant=active_tenant) | Q(assigned_holder__tenant=active_tenant)
                    )
                checked_out = assignment_qs.aggregate(total=Sum('qty'))['total'] or 0

                available = max(0, total_stock - checked_out)
                if available < acc.min_qty:
                    low_accessories.append(ScopedAccessoryWrapper(acc, available))

        # Scoped Consumable calculation
        low_consumables = []
        if include_con:
            con_qs = get_scoped_queryset(Consumable, request, config=self.config.get("config", {}))
            for con in con_qs.filter(min_qty__gt=0):
                # Scoped total stock in locations belonging to this tenant
                stock_qs = con.stocks.all()
                if active_tenant:
                    stock_qs = stock_qs.filter(location__tenant=active_tenant)
                total_stock = stock_qs.aggregate(total=Sum('qty'))['total'] or 0

                # Scoped consumed quantity
                consumption_qs = con.consumptions.all()
                if active_tenant:
                    consumption_qs = consumption_qs.filter(
                        Q(assigned_location__tenant=active_tenant) | Q(assigned_holder__tenant=active_tenant)
                    )
                consumed = consumption_qs.aggregate(total=Sum('qty'))['total'] or 0

                available = max(0, total_stock - consumed)
                if available < con.min_qty:
                    low_consumables.append(ScopedConsumableWrapper(con, available))

        # Scoped Component calculation
        low_components = []
        if include_comp:
            from components.models import Component, ComponentStock, ComponentAllocation
            comp_qs = Component.objects.filter(min_stock_level__gt=0).order_by('name')
            for comp in comp_qs:
                stock_filter = Q(component=comp)
                allocation_filter = Q(component=comp, deleted_at__isnull=True)
                if active_tenant:
                    stock_filter &= Q(location__tenant=active_tenant)
                    allocation_filter &= Q(asset__tenant=active_tenant)

                total_stock = ComponentStock.objects.filter(stock_filter).aggregate(total=Sum('qty'))['total'] or 0
                total_allocated = ComponentAllocation.objects.filter(allocation_filter).aggregate(total=Sum('qty_allocated'))['total'] or 0
                
                available = total_stock - total_allocated
                if available < comp.min_stock_level:
                    low_components.append({
                        'component': comp,
                        'available_stock': available,
                        'total_stock': total_stock,
                        'total_allocated': total_allocated,
                        'min_stock_level': comp.min_stock_level,
                    })

        return {
            'low_stock_accessories': low_accessories,
            'low_stock_consumables': low_consumables,
            'low_stock_components': low_components,
            'low_stock_accessory_count': len(low_accessories),
            'low_stock_consumable_count': len(low_consumables),
            'low_stock_component_count': len(low_components),
        }


@register_widget
class AssetAgeWidget(DashboardWidget):
    widget_id = 'asset-age'
    title = 'Asset Age Distribution'
    description = 'Breakdown of assets by age bucket and average age'
    template_name = 'extras/dashboard/widgets/asset_age.html'

    class ConfigForm(WidgetConfigForm):
        chart_format = forms.ChoiceField(
            label='Chart Format',
            choices=[
                ('bar', 'Bar Chart'),
                ('pie', 'Pie Chart'),
                ('list', 'List Format'),
            ],
            initial='bar',
            widget=forms.Select(attrs={'class': 'form-select'}),
            required=False
        )

    def get_context(self, request):
        today = date.today()
        assets = get_scoped_queryset(Asset, request, config=self.config.get("config", {})).filter(purchase_date__isnull=False)
        total_age = 0
        count = 0
        buckets = {'lt1y': 0, '1_3y': 0, '3_5y': 0, '5_7y': 0, 'gt7y': 0}

        for asset in assets.only('purchase_date').iterator(chunk_size=1000):
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
            'chart_format': self.get_config_value('chart_format', 'bar'),
        }


@register_widget
class TenantSpendWidget(DashboardWidget):
    widget_id = 'tenant-spend'
    title = 'Tenant Spend'
    description = 'Purchase cost grouped by tenant (top 8)'
    template_name = 'extras/dashboard/widgets/tenant_spend.html'
    admin_only = True          # Restrict in UI list

    class ConfigForm(WidgetConfigForm):
        limit = forms.IntegerField(
            label='Max Tenants to Show',
            initial=8,
            widget=forms.NumberInput(attrs={'class': 'form-control'}),
            required=False
        )

    def get_context(self, request):
        limit = self.get_config_value('limit', 8)
        spend = Asset.objects.values('tenant__name').annotate(
            total=Sum('purchase_cost')
        ).order_by('-total')[:limit]
        return {'tenant_spend': list(spend)}
