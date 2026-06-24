import django_filters
from django import forms
from django.db.models import Q
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML

from organization.models import Tenant, TenantRole
from .models import UserGroup

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


class UserGroupFilterSet(django_filters.FilterSet):
    # Groups are global; filter by the roles they grant (which may span tenants) and
    # by member. `grants_tenant` (roles__tenant) finds groups granting access to a tenant.
    q = django_filters.CharFilter(
        method='search',
        label=_('Search'),
        widget=forms.TextInput(attrs={'placeholder': _('Search...')})
    )
    roles = django_filters.ModelMultipleChoiceFilter(
        queryset=TenantRole._base_manager.all(),
        label=_("Roles"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'})
    )
    grants_tenant = django_filters.ModelChoiceFilter(
        field_name='roles__tenant',
        queryset=Tenant.objects.all(),
        label=_("Grants access to tenant"),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    members = django_filters.ModelChoiceFilter(
        queryset=User.objects.all().order_by('username'),
        label=_("Member"),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    is_active = django_filters.BooleanFilter(
        widget=forms.Select(
            choices=[('', _('Any')), ('true', _('Yes')), ('false', _('No'))],
            attrs={'class': 'form-select'},
        ),
    )

    class Meta:
        model = UserGroup
        fields = ['roles', 'grants_tenant', 'members', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.form.helper = FormHelper()
        self.form.helper.form_method = 'get'
        self.form.helper.form_tag = False
        self.form.helper.layout = Layout(
            'q', 'roles', 'grants_tenant', 'members', 'is_active',
            HTML('<div class="mt-3">'),
            Submit('submit', _('Apply Filter'), css_class='btn btn-primary'),
            HTML('<a href="{{ request.path }}" class="btn btn-secondary ms-2">%s</a>' % _('Clear Filters')),
            HTML('</div>')
        )

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(description__icontains=value)
        ).distinct()
