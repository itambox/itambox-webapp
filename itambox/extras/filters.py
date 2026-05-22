import django_filters
from django import forms
from django.db.models import Q
from .models import Tag, CustomField, CustomFieldset

class TagFilter(django_filters.FilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
    )
    
    class Meta:
        model = Tag
        fields = ['name']
    
    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) | 
            Q(slug__icontains=value) |
            Q(description__icontains=value)
        )


class CustomFieldFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(method='search', label='Search')

    class Meta:
        model = CustomField
        fields = ['name', 'label', 'field_type', 'required']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(label__icontains=value)
        ).distinct()


class CustomFieldsetFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(method='search', label='Search')

    class Meta:
        model = CustomFieldset
        fields = ['name']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value)
        ).distinct()

 