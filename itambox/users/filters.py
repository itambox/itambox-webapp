import django_filters
from django import forms
from django.db.models import Q
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML

from organization.access import get_ancestor_tenant_group_ids
from organization.models import Role, RoleGrantScope, Tenant
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
    can_login = django_filters.BooleanFilter(
        widget=forms.Select(choices=(
            ('', _('All')),
            ('true', _('Can log in')),
            ('false', _('Cannot log in')),
        ), attrs={'class': 'form-select'})
    )

    class Meta:
        model = User
        fields = ['username', 'email', 'is_active', 'is_staff', 'can_login']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.form.helper = FormHelper()
        self.form.helper.form_method = 'get'
        self.form.helper.form_tag = False
        self.form.helper.layout = Layout(
            'q', 'is_active', 'can_login', 'is_staff',
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
    # Canonical joins flow through RoleGrant/RoleGrantScope and GroupMembership.
    q = django_filters.CharFilter(
        method='search',
        label=_('Search'),
        widget=forms.TextInput(attrs={'placeholder': _('Search...')})
    )
    roles = django_filters.ModelMultipleChoiceFilter(
        field_name='role_grants__role',
        queryset=Role._base_manager.all(),
        label=_("Roles"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'})
    )
    grants_tenant = django_filters.ModelChoiceFilter(
        method='filter_grants_tenant',
        queryset=Tenant._base_manager.filter(deleted_at__isnull=True),
        label=_("Grants access to tenant"),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    members = django_filters.ModelChoiceFilter(
        field_name='group_memberships__membership__user',
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

    def filter_grants_tenant(self, queryset, name, tenant):
        coverage = Q(
            tenant=tenant,
            role_grants__scopes__scope_type=RoleGrantScope.SCOPE_OWN,
        ) | Q(
            role_grants__scopes__scope_type=RoleGrantScope.SCOPE_TENANT,
            role_grants__scopes__tenant=tenant,
        )
        if tenant.managed_by_id:
            coverage |= Q(
                tenant_id=tenant.managed_by_id,
                role_grants__scopes__scope_type=RoleGrantScope.SCOPE_ALL_MANAGED,
            )
            ancestor_ids = get_ancestor_tenant_group_ids(
                tenant.group_id,
                live_only=True,
            )
            if ancestor_ids:
                coverage |= Q(
                    tenant_id=tenant.managed_by_id,
                    role_grants__scopes__scope_type=RoleGrantScope.SCOPE_TENANT_GROUP,
                    role_grants__scopes__tenant_group_id__in=ancestor_ids,
                )
        return queryset.filter(coverage).distinct()
