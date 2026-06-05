import django_filters
from django import forms
from django.db.models import Q
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth import get_user_model

from core.models import ObjectChange
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
                            asset__tenant=current_tenant
                        ).distinct()
                    else:
                        continue
                    
                    filter_obj.queryset = filtered_qs
                    filter_obj.extra['queryset'] = filtered_qs


class ObjectChangeFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
        widget=forms.TextInput(attrs={'placeholder': 'Search Username, Object, type...'})
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
