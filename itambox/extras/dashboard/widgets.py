from datetime import date, timedelta
import json
from django import forms
from django.db.models import Sum, Count, Q, Avg, F, Case, When, Value, IntegerField, Subquery, OuterRef
from django.db.models.functions import Extract, Coalesce

from django.urls import reverse
from django.utils.safestring import mark_safe
from django.template.loader import render_to_string
from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext as _

from assets.models import Asset, StatusLabel
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

    def get_footer_links(self, request):
        """Return a list of dictionaries with 'url' and 'label' for card footer buttons."""
        return []


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
        return {
            'content': self.get_config_value('content', ''),
        }


OBJECT_COUNT_MODEL_CHOICES = [
    ('assets.asset', 'Assets'),
    ('assets.assettype', 'Asset Types'),
    ('assets.manufacturer', 'Manufacturers'),
    ('assets.statuslabel', 'Status Labels'),
    ('inventory.component', 'Components'),
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
        from inventory.models import Component
        from assets.models import AssetType, Manufacturer
        model_map = {
            'assets.asset': (Asset, 'assets:asset_list'),
            'assets.assettype': (AssetType, 'assets:assettype_list'),
            'assets.manufacturer': (Manufacturer, 'assets:manufacturer_list'),
            'assets.statuslabel': (StatusLabel, 'assets:statuslabel_list'),
            'inventory.component': (Component, 'inventory:component_list'),
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

        asset_sums = assets.aggregate(
            purchase_total=Sum('purchase_cost'),
            salvage_total=Sum('salvage_value')
        )
        total_purchase = float(asset_sums['purchase_total'] or 0.0)
        total_salvage = float(asset_sums['salvage_total'] or 0.0)
        total_maintenance = float(maintenances.aggregate(total=Sum('cost'))['total'] or 0.0)
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

    def get_footer_links(self, request):
        return [{'url': reverse('assets:asset_list'), 'label': _('View Cost Details')}]


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

        chart_data = []
        for status in statuses:
            if status.asset_count > 0:
                chart_data.append({
                    'name': status.name,
                    'count': status.asset_count,
                    'color': f"#{status.color}" if status.color else "#626976"
                })

        return {
            'total_assets': total_assets,
            'status_stats': statuses,
            'chart_type': self.get_config_value('chart_type', 'doughnut'),
            'chart_data_json': json.dumps(chart_data),
        }

    def get_footer_links(self, request):
        return [{'url': reverse('assets:asset_list'), 'label': _('View All Assets')}]


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

    def get_footer_links(self, request):
        return [{'url': reverse('licenses:license_list'), 'label': _('View All Licenses')}]


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

    def get_footer_links(self, request):
        return [{'url': reverse('compliance:assetmaintenance_list'), 'label': _('View All Repairs')}]


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
        
        # 1. Resolve distinct EOL months in the catalog
        from assets.models import AssetType
        eol_month_values = list(AssetType.objects.filter(eol_months__isnull=False).values_list('eol_months', flat=True).distinct())
        
        if not eol_month_values:
            return {'eol_alerts': [], 'days_horizon': days_horizon}
            
        # 2. Build pre-filtering query to filter only assets approaching EOL in the database
        q_filter = Q()
        for M in eol_month_values:
            # purchase_date + M months <= today + days_horizon
            cutoff_date = today + timedelta(days=days_horizon) - timedelta(days=int(M * 30.44))
            q_filter |= Q(asset_type__eol_months=M, purchase_date__lte=cutoff_date)
            
        queryset = get_scoped_queryset(Asset, request, config=self.config.get("config", {})).filter(
            purchase_date__isnull=False,
            asset_type__eol_months__isnull=False
        ).filter(q_filter).select_related('asset_type', 'asset_type__manufacturer')

        for asset in queryset:
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

    def get_footer_links(self, request):
        return [{'url': reverse('assets:asset_list'), 'label': _('View All Assets')}]


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
        action_filter = forms.ChoiceField(
            label='Filter by Action',
            choices=[
                ('', 'All Actions'),
                ('create', 'Creations Only'),
                ('update', 'Updates Only'),
                ('delete', 'Deletions Only'),
            ],
            initial='',
            widget=forms.Select(attrs={'class': 'form-select'}),
            required=False
        )
        models = forms.MultipleChoiceField(
            label='Filter by Object Types',
            choices=OBJECT_COUNT_MODEL_CHOICES,
            widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
            required=False,
            help_text='If none selected, changes for all object types will be shown.'
        )

    def get_context(self, request):
        from core.models import ObjectChange
        from django.contrib.contenttypes.models import ContentType
        
        limit = self.get_config_value('limit', 10)
        action = self.get_config_value('action_filter', '')
        selected_models = self.get_config_value('models', [])
        
        qs = get_scoped_queryset(ObjectChange, request, config=self.config.get("config", {}))
        
        if action:
            qs = qs.filter(action=action)
            
        if selected_models:
            ct_filters = Q()
            for m_label in selected_models:
                try:
                    app_label, model_name = m_label.split('.')
                    ct_filters |= Q(changed_object_type__app_label=app_label, changed_object_type__model=model_name)
                except ValueError:
                    continue
            qs = qs.filter(ct_filters)
            
        changes = qs.select_related(
            'user', 'changed_object_type'
        ).prefetch_related('changed_object')[:limit]
        
        return {
            'recent_changes': changes,
            'action_filter': action,
            'limit': limit,
        }

    def get_footer_links(self, request):
        return [{'url': reverse('objectchange_list'), 'label': _('View Full Change Log')}]



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

    def get_footer_links(self, request):
        return [{'url': reverse('subscriptions:subscription_list'), 'label': _('View All Subscriptions')}]


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

        # Scoped Accessory calculation (Annotated in single query)
        low_accessories = []
        if include_acc:
            from inventory.models import AccessoryStock, AccessoryAssignment
            acc_qs = get_scoped_queryset(Accessory, request, config=self.config.get("config", {})).filter(min_qty__gt=0)
            
            # Scoped total stock subquery
            stock_sub = AccessoryStock.objects.filter(accessory=OuterRef('pk'))
            if active_tenant:
                stock_sub = stock_sub.filter(location__tenant=active_tenant)
            stock_sub = stock_sub.values('accessory').annotate(sum_qty=Sum('qty')).values('sum_qty')
            
            # Scoped checked out subquery
            assignment_sub = AccessoryAssignment.objects.filter(accessory=OuterRef('pk'))
            if active_tenant:
                assignment_sub = assignment_sub.filter(
                    Q(assigned_location__tenant=active_tenant) | Q(assigned_holder__tenant=active_tenant)
                )
            assignment_sub = assignment_sub.values('accessory').annotate(sum_qty=Sum('qty')).values('sum_qty')
            
            acc_qs = acc_qs.annotate(
                _total_stock_annotated=Coalesce(Subquery(stock_sub), 0),
                _checked_out_annotated=Coalesce(Subquery(assignment_sub), 0)
            )
            
            for acc in acc_qs:
                available = max(0, acc._total_stock_annotated - acc._checked_out_annotated)
                if available < acc.min_qty:
                    low_accessories.append(ScopedAccessoryWrapper(acc, available))

        # Scoped Consumable calculation (Annotated in single query)
        low_consumables = []
        if include_con:
            from inventory.models import ConsumableStock, ConsumableAssignment
            con_qs = get_scoped_queryset(Consumable, request, config=self.config.get("config", {})).filter(min_qty__gt=0)
            
            # Scoped total stock subquery
            stock_sub = ConsumableStock.objects.filter(consumable=OuterRef('pk'))
            if active_tenant:
                stock_sub = stock_sub.filter(location__tenant=active_tenant)
            stock_sub = stock_sub.values('consumable').annotate(sum_qty=Sum('qty')).values('sum_qty')
            
            # Scoped consumed subquery
            consumption_sub = ConsumableAssignment.objects.filter(consumable=OuterRef('pk'))
            if active_tenant:
                consumption_sub = consumption_sub.filter(
                    Q(assigned_location__tenant=active_tenant) | Q(assigned_holder__tenant=active_tenant)
                )
            consumption_sub = consumption_sub.values('consumable').annotate(sum_qty=Sum('qty')).values('sum_qty')
            
            con_qs = con_qs.annotate(
                _total_stock_annotated=Coalesce(Subquery(stock_sub), 0),
                _consumed_annotated=Coalesce(Subquery(consumption_sub), 0)
            )
            
            for con in con_qs:
                available = max(0, con._total_stock_annotated - con._consumed_annotated)
                if available < con.min_qty:
                    low_consumables.append(ScopedConsumableWrapper(con, available))

        # Scoped Component calculation (Annotated in single query)
        low_components = []
        if include_comp:
            from inventory.models import Component, ComponentStock, ComponentAllocation
            comp_qs = Component.objects.filter(min_qty__gt=0).order_by('name')
            
            # Scoped total stock subquery
            stock_sub = ComponentStock.objects.filter(component=OuterRef('pk'))
            if active_tenant:
                stock_sub = stock_sub.filter(location__tenant=active_tenant)
            stock_sub = stock_sub.values('component').annotate(sum_qty=Sum('qty')).values('sum_qty')
            
            # Scoped allocated subquery
            allocation_sub = ComponentAllocation.objects.filter(component=OuterRef('pk'), deleted_at__isnull=True)
            if active_tenant:
                allocation_sub = allocation_sub.filter(assigned_asset__tenant=active_tenant)
            allocation_sub = allocation_sub.values('component').annotate(sum_qty=Sum('qty')).values('sum_qty')
            
            comp_qs = comp_qs.annotate(
                _total_stock_annotated=Coalesce(Subquery(stock_sub), 0),
                _total_allocated_annotated=Coalesce(Subquery(allocation_sub), 0)
            )
            
            for comp in comp_qs:
                available = comp._total_stock_annotated - comp._total_allocated_annotated
                if available < comp.min_qty:
                    low_components.append({
                        'component': comp,
                        'available_stock': available,
                        'total_stock': comp._total_stock_annotated,
                        'total_allocated': comp._total_allocated_annotated,
                        'min_qty': comp.min_qty,
                    })

        return {
            'low_stock_accessories': low_accessories,
            'low_stock_consumables': low_consumables,
            'low_stock_components': low_components,
            'low_stock_accessory_count': len(low_accessories),
            'low_stock_consumable_count': len(low_consumables),
            'low_stock_component_count': len(low_components),
        }

    def get_footer_links(self, request):
        return [
            {'url': reverse('inventory:component_list'), 'label': _('Components')},
            {'url': reverse('inventory:accessory_list'), 'label': _('Accessories')},
            {'url': reverse('inventory:consumable_list'), 'label': _('Consumables')},
        ]



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

        # Bypasses heavy Django model object creation completely
        purchase_dates = list(assets.values_list('purchase_date', flat=True))

        for p_date in purchase_dates:
            age_years = (today - p_date).days / 365.25
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
        chart_data = [
            { 'name': "< 1 Year", 'count': buckets['lt1y'], 'color': "#2fb344" },
            { 'name': "1 - 3 Years", 'count': buckets['1_3y'], 'color': "#206bc4" },
            { 'name': "3 - 5 Years", 'count': buckets['3_5y'], 'color': "#f59f00" },
            { 'name': "5 - 7 Years", 'count': buckets['5_7y'], 'color': "#fd7e14" },
            { 'name': "7+ Years", 'count': buckets['gt7y'], 'color': "#d63939" }
        ]
        return {
            'age_buckets': buckets,
            'avg_asset_age_years': avg_age,
            'chart_format': self.get_config_value('chart_format', 'bar'),
            'chart_data_json': json.dumps(chart_data),
        }

    def get_footer_links(self, request):
        return [{'url': reverse('assets:asset_list'), 'label': _('View All Assets')}]


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
        chart_type = forms.ChoiceField(
            label='Chart Type',
            choices=[
                ('bar', 'Horizontal Bar Chart'),
                ('doughnut', 'Doughnut'),
                ('pie', 'Pie'),
                ('list', 'Simple List'),
            ],
            initial='bar',
            widget=forms.Select(attrs={'class': 'form-select'}),
            required=False
        )
        currency = forms.CharField(
            label='Currency Symbol',
            max_length=5,
            initial='€',
            widget=forms.TextInput(attrs={'class': 'form-control'}),
            required=False
        )
        exclude_unassigned = forms.BooleanField(
            label='Exclude Unassigned Assets',
            initial=False,
            widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            required=False,
            help_text='Do not display spending for assets with no tenant.'
        )

    def get_context(self, request):
        limit = self.get_config_value('limit', 8)
        chart_type = self.get_config_value('chart_type', 'bar')
        currency = self.get_config_value('currency', '€')
        exclude_unassigned = self.get_config_value('exclude_unassigned', False)
        
        # Build query
        qs = Asset.objects.all()
        if exclude_unassigned:
            qs = qs.filter(tenant__isnull=False)
            
        spend = qs.values('tenant__name').annotate(
            total=Sum('purchase_cost')
        ).order_by('-total')[:limit]
        
        spend_list = list(spend)
        chart_data = []
        for t in spend_list:
            chart_data.append({
                'name': t['tenant__name'] or 'Unassigned',
                'total': float(t['total'] or 0.0)
            })

        return {
            'tenant_spend': spend_list,
            'chart_type': chart_type,
            'currency': currency,
            'chart_data_json': json.dumps(chart_data),
        }

    def get_footer_links(self, request):
        return [{'url': reverse('organization:tenant_list'), 'label': _('View All Tenants')}]
