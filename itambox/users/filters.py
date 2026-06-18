import django_filters
from django import forms
from django.db.models import Q
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML

User = get_user_model()

class UserFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(
        method='search',
        label=_('Search'),
        widget=forms.TextInput(attrs={'placeholder': _('Search...')})
    )
    is_active = django_filters.BooleanFilter(
        widget=forms.Select(choices=(
            ('', _('All')),
            ('true', _('Active')),
            ('false', _('Inactive')),
        ), attrs={'class': 'form-select'})
    )
    is_staff = django_filters.BooleanFilter(
        widget=forms.Select(choices=(
            ('', _('All')),
            ('true', _('Staff')),
            ('false', _('Non-Staff')),
        ), attrs={'class': 'form-select'})
    )

    class Meta:
        model = User
        fields = ['username', 'email', 'is_active', 'is_staff']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.form.helper = FormHelper()
        self.form.helper.form_method = 'get'
        self.form.helper.form_tag = False
        self.form.helper.layout = Layout(
            'q', 'is_active', 'is_staff',
            HTML('<div class="mt-3">'),
            Submit('submit', _('Apply Filter'), css_class='btn btn-primary'),
            HTML('<a href="{{ request.path }}" class="btn btn-secondary ms-2">%s</a>' % _('Clear Filters')),
            HTML('</div>')
        )

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(username__icontains=value) |
            Q(first_name__icontains=value) |
            Q(last_name__icontains=value) |
            Q(email__icontains=value)
        ).distinct()
