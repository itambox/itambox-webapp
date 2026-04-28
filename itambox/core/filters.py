import django_filters
from django import forms
from django.db.models import Q
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth import get_user_model

from core.models import ObjectChange
from core.choices import ObjectChangeActionChoices

User = get_user_model()

class ObjectChangeFilterSet(django_filters.FilterSet):
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
