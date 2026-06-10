import django_filters
from django import forms
from django.db.models import Q
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth import get_user_model

from core.models import AlertLog, AlertRule, ObjectChange
from core.choices import ObjectChangeActionChoices

User = get_user_model()


class BaseFilterSet(django_filters.FilterSet):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.managers import get_current_tenant
        current_tenant = get_current_tenant()
        if not current_tenant:
            return

        for field_name, filter_obj in self.filters.items():
            if isinstance(filter_obj, (django_filters.ModelChoiceFilter, django_filters.ModelMultipleChoiceFilter)):
                queryset = filter_obj.extra.get('queryset')
                if queryset is not None:
                    model = queryset.model
                    
                    # 1. If model is Tenant, limit choices to the active tenant
                    if model.__name__ == 'Tenant':
                        filtered_qs = queryset.filter(pk=current_tenant.pk)
                    
                    # 2. If model has tenant field, filter by active tenant
                    elif hasattr(model, 'tenant') or any(f.name == 'tenant' for f in model._meta.fields):
                        filtered_qs = queryset.filter(tenant=current_tenant)
                    
                    # 3. If global models, filter by relation to tenant-owned objects
                    elif model.__name__ == 'Manufacturer':
                        filtered_qs = queryset.filter(
                            Q(asset_types__assets__tenant=current_tenant) |
                            Q(accessories__tenant=current_tenant) |
                            Q(consumables__tenant=current_tenant)
                        ).distinct()
                    elif model.__name__ == 'AssetType':
                        filtered_qs = queryset.filter(
                            assets__tenant=current_tenant
                        ).distinct()
                    elif model.__name__ == 'Supplier':
                        filtered_qs = queryset.filter(
                            Q(assets__tenant=current_tenant) |
                            Q(supplier_accessories__tenant=current_tenant)
                        ).distinct()
                    elif model.__name__ == 'Category':
                        filtered_qs = queryset.filter(
                            Q(asset_types__assets__tenant=current_tenant) |
                            Q(accessories__tenant=current_tenant) |
                            Q(consumables__tenant=current_tenant)
                        ).distinct()
                    elif model.__name__ == 'StatusLabel':
                        filtered_qs = queryset.filter(
                            assets__tenant=current_tenant
                        ).distinct()
                    elif model.__name__ == 'AssetRole':
                        filtered_qs = queryset.filter(
                            assets__tenant=current_tenant
                        ).distinct()
                    else:
                        continue
                    
                    filter_obj.queryset = filtered_qs
                    filter_obj.extra['queryset'] = filtered_qs


class ObjectChangeFilterSet(BaseFilterSet):
    # Auto-generated, high-volume core bookkeeping models whose change records are
    # noise in the audit trail. Hidden by default; surfaced via "show_system_events".
    NOISE_CONTENT_TYPES = (
        ('core', 'job'),
        ('core', 'bookmark'),
        ('core', 'event'),
        ('core', 'reportgenerationarchive'),
    )

    q = django_filters.CharFilter(
        method='search',
        label='Search',
        widget=forms.TextInput(attrs={'placeholder': 'Search Username, Object, type...'})
    )

    show_system_events = django_filters.BooleanFilter(
        method='_noop',
        label='Show system events',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )

    action = django_filters.MultipleChoiceFilter(
        choices=[(c[0], c[1]) for c in ObjectChangeActionChoices().CHOICES],
        label='Action',
        widget=forms.SelectMultiple(attrs={'class': 'form-select'})
    )
    
    changed_object_type = django_filters.ModelMultipleChoiceFilter(
        queryset=ContentType.objects.order_by('app_label', 'model'),
        label='Object Type',
        widget=forms.SelectMultiple(attrs={'class': 'form-select'})
    )
    
    user = django_filters.ModelChoiceFilter(
        queryset=User.objects.order_by('username'),
        label='User',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    
    time_after = django_filters.DateTimeFilter(
        field_name='time',
        lookup_expr='gte',
        label='After',
        widget=forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'})
    )
    
    time_before = django_filters.DateTimeFilter(
        field_name='time',
        lookup_expr='lte',
        label='Before',
        widget=forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'})
    )

    class Meta:
        model = ObjectChange
        fields = []

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(user_name__icontains=value) |
            Q(object_repr__icontains=value) |
            Q(object_type_repr__icontains=value)
        ).distinct()

    def _noop(self, queryset, name, value):
        # The actual show/hide logic lives in filter_queryset so it can run when
        # the (unchecked) checkbox submits no value. This keeps the field for the form.
        return queryset

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        raw = (self.data or {}).get('show_system_events')
        show_system = str(raw).lower() in ('1', 'true', 'on', 'yes')
        if not show_system:
            noise_q = None
            for app_label, model in self.NOISE_CONTENT_TYPES:
                clause = Q(
                    changed_object_type__app_label=app_label,
                    changed_object_type__model=model,
                )
                noise_q = clause if noise_q is None else (noise_q | clause)
            if noise_q is not None:
                queryset = queryset.exclude(noise_q)
        return queryset


class AlertLogFilterSet(BaseFilterSet):
    status = django_filters.MultipleChoiceFilter(
        choices=AlertLog.STATUS_CHOICES,
        label='Status',
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )
    severity = django_filters.MultipleChoiceFilter(
        choices=AlertRule.SEVERITY_CHOICES,
        label='Severity',
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )
    rule = django_filters.ModelChoiceFilter(
        queryset=AlertRule.objects.all(),
        label='Rule',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    created_after = django_filters.DateFilter(
        field_name='created_at',
        lookup_expr='gte',
        label='Created after',
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
    )
    created_before = django_filters.DateFilter(
        field_name='created_at',
        lookup_expr='lte',
        label='Created before',
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
    )

    class Meta:
        model = AlertLog
        fields = ['status', 'severity', 'rule', 'created_after', 'created_before']
