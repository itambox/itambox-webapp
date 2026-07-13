"""Share a stock pool with another tenant (TenantResourceGrant, ADR-0001 4b).

The pool (and therefore the owning tenant) is bound by the view from the URL
— the form only chooses WHO receives access, at what level, and why.
"""
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout
from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from organization.access import (
    accessible_tenant_ids, get_ancestor_tenant_group_ids,
)
from organization.models import Tenant, TenantGroup, TenantResourceGrant


class TenantResourceGrantForm(forms.ModelForm):
    class Meta:
        model = TenantResourceGrant
        fields = ['grantee_tenant', 'grantee_tenant_group', 'access_level', 'reason']
        widgets = {
            'reason': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, owner_tenant=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['grantee_tenant'].required = False
        self.fields['grantee_tenant_group'].required = False

        if owner_tenant is not None:
            # Candidate grantees: tenants the owner manages, tenants sharing
            # the owner's group tree, and tenants the acting user can access —
            # never the owner itself. _base_manager: the candidates are by
            # definition OTHER tenants, which the scoped manager (and the
            # ModelChoiceField monkey-patch) would hide.
            candidate_ids = set(
                Tenant._base_manager.filter(
                    managed_by=owner_tenant, deleted_at__isnull=True,
                ).values_list('pk', flat=True)
            )
            if user is not None:
                candidate_ids |= accessible_tenant_ids(user)
            if owner_tenant.group_id:
                root_ids = get_ancestor_tenant_group_ids(owner_tenant.group_id)
                candidate_ids |= set(
                    Tenant._base_manager.filter(
                        group_id__in=root_ids, deleted_at__isnull=True,
                    ).values_list('pk', flat=True)
                )
            candidate_ids.discard(owner_tenant.pk)
            self.fields['grantee_tenant'].queryset = Tenant._base_manager.filter(
                pk__in=candidate_ids, deleted_at__isnull=True,
            ).order_by('name')
        # Tenant groups stay on the scoped default manager: the monkey-patch
        # narrows the choices to groups the acting user can see.
        self.fields['grantee_tenant_group'].queryset = TenantGroup.objects.all().order_by('name')

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            'grantee_tenant',
            'grantee_tenant_group',
            'access_level',
            'reason',
        )

    def clean(self):
        cleaned = super().clean()
        tenant = cleaned.get('grantee_tenant')
        group = cleaned.get('grantee_tenant_group')
        if bool(tenant) == bool(group):
            raise ValidationError(_(
                "Select exactly one grantee: a tenant OR a tenant group."
            ))
        return cleaned
