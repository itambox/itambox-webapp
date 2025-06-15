import django_filters
from django import forms
from django.db.models import Q
from .models import Tag, ConfigTemplate

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
            django_filters.Q(name__icontains=value) | 
            django_filters.Q(slug__icontains=value) |
            django_filters.Q(description__icontains=value)
        )

class ConfigTemplateFilter(django_filters.FilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
    )
    
    class Meta:
        model = ConfigTemplate
        fields = ['name']
    
    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            django_filters.Q(name__icontains=value) | 
            django_filters.Q(description__icontains=value) |
            django_filters.Q(template_content__icontains=value)
        ) 