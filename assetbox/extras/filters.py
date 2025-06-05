import django_filters
from django import forms
from django.db.models import Q
from .models import Tag

class TagFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
        widget=forms.TextInput(attrs={'placeholder': 'Name, Slug, Description...'})
    )
    # Add color filter later if desired

    class Meta:
        model = Tag
        fields = ['name', 'slug']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(slug__icontains=value) |
            Q(description__icontains=value)
        ).distinct() 