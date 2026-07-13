"""Regression coverage for canonical RoleGrant integrity reporting."""

import json
import tempfile
from io import StringIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core.integrity import (
    CLASS_INVALID,
    CLASS_PROVIDER_MANAGED,
    check_rbac_grants,
)
from core.managers import set_current_membership, set_current_tenant, set_current_tenant_group
from core.tests.mixins import grant
from organization.models import (
    Membership,
    Role,
    RoleGrant,
    RoleGrantScope,
    Tenant,
)
from users.models import GroupMembership, UserGroup

User = get_user_model()


def _findings_for(findings, model_label, pk):
    return [finding for finding in findings if finding.model == model_label and finding.pk == pk]


class _ContextResetMixin:
    def setUp(self):
        super().setUp()
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)

    def tearDown(self):
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)
        super().tearDown()


class RoleGrantRBACCheckTests(_ContextResetMixin, TestCase):
    def _matches(self, findings, role_grant):
        return _findings_for(findings, 'organization.RoleGrant', role_grant.pk)

    def test_own_scope_role_owned_by_membership_tenant_is_clean(self):
        tenant = Tenant.objects.create(name='RG Own', slug='rg-own')
        role = Role.objects.create(tenant=tenant, name='Viewer', permissions=[])
        user = User.objects.create_user(username='rg_own', password='pw')
        role_grant = grant(user, tenant, role)

        self.assertEqual(self._matches(check_rbac_grants(), role_grant), [])

    def test_shared_provider_role_on_managed_membership_is_clean(self):
        provider = Tenant.objects.create(name='RG Provider', slug='rg-provider', is_provider=True)
        managed = Tenant.objects.create(
            name='RG Managed', slug='rg-managed', managed_by=provider,
        )
        role = Role.objects.create(
            tenant=provider,
            name='Shared Viewer',
            shared_with_managed=True,
            permissions=[],
        )
        user = User.objects.create_user(username='rg_shared', password='pw')
        role_grant = grant(user, managed, role)

        self.assertEqual(self._matches(check_rbac_grants(), role_grant), [])

    def test_unshared_provider_role_on_managed_membership_is_flagged(self):
        provider = Tenant.objects.create(name='RG Provider B', slug='rg-provider-b', is_provider=True)
        managed = Tenant.objects.create(
            name='RG Managed B', slug='rg-managed-b', managed_by=provider,
        )
        local_role = Role.objects.create(tenant=managed, name='Local Viewer', permissions=[])
        unshared_role = Role.objects.create(
            tenant=provider,
            name='Unshared Viewer',
            shared_with_managed=False,
            permissions=[],
        )
        user = User.objects.create_user(username='rg_unshared', password='pw')
        role_grant = grant(user, managed, local_role)
        RoleGrant.objects.filter(pk=role_grant.pk).update(role=unshared_role)

        matches = self._matches(check_rbac_grants(), role_grant)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].classification, CLASS_PROVIDER_MANAGED)
        self.assertFalse(matches[0].details['shared_with_managed'])

    def test_unrelated_role_owner_is_invalid(self):
        home = Tenant.objects.create(name='RG Home', slug='rg-home')
        other = Tenant.objects.create(name='RG Other', slug='rg-other')
        home_role = Role.objects.create(tenant=home, name='Home Viewer', permissions=[])
        foreign_role = Role.objects.create(tenant=other, name='Foreign Viewer', permissions=[])
        user = User.objects.create_user(username='rg_unrelated', password='pw')
        role_grant = grant(user, home, home_role)
        RoleGrant.objects.filter(pk=role_grant.pk).update(role=foreign_role)

        matches = self._matches(check_rbac_grants(), role_grant)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].classification, CLASS_INVALID)

    def test_managed_scope_on_non_provider_principal_is_flagged(self):
        tenant = Tenant.objects.create(name='RG Non-provider', slug='rg-non-provider')
        role = Role.objects.create(tenant=tenant, name='Viewer', permissions=[])
        user = User.objects.create_user(username='rg_non_provider', password='pw')
        role_grant = grant(user, tenant, role)
        role_grant.scopes.update(scope_type=RoleGrantScope.SCOPE_ALL_MANAGED)

        matches = self._matches(check_rbac_grants(), role_grant)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].classification, CLASS_INVALID)
        self.assertIn('principal tenant is not a provider', matches[0].summary)

    def test_managed_scope_role_must_be_provider_owned(self):
        provider = Tenant.objects.create(name='RG Provider C', slug='rg-provider-c', is_provider=True)
        managed = Tenant.objects.create(
            name='RG Managed C', slug='rg-managed-c', managed_by=provider,
        )
        provider_role = Role.objects.create(tenant=provider, name='Provider Viewer', permissions=[])
        customer_role = Role.objects.create(tenant=managed, name='Customer Viewer', permissions=[])
        user = User.objects.create_user(username='rg_wrong_role', password='pw')
        role_grant = grant(
            user,
            provider,
            provider_role,
            reach=RoleGrant.REACH_MANAGED,
            managed_scope=RoleGrantScope.SCOPE_ALL_MANAGED,
        )
        RoleGrant.objects.filter(pk=role_grant.pk).update(role=customer_role)

        matches = self._matches(check_rbac_grants(), role_grant)

        self.assertEqual(len(matches), 1)
        self.assertIn('managed-scope role must be owned by the granting provider', matches[0].summary)

    def test_explicit_target_no_longer_managed_is_flagged(self):
        provider = Tenant.objects.create(name='RG Provider D', slug='rg-provider-d', is_provider=True)
        managed = Tenant.objects.create(
            name='RG Managed D', slug='rg-managed-d', managed_by=provider,
        )
        role = Role.objects.create(tenant=provider, name='Scoped Viewer', permissions=[])
        user = User.objects.create_user(username='rg_stale', password='pw')
        role_grant = grant(
            user,
            provider,
            role,
            reach=RoleGrant.REACH_MANAGED,
            managed_scope=RoleGrantScope.SCOPE_TENANT,
            assigned_tenants=[managed],
        )
        Tenant._base_manager.filter(pk=managed.pk).update(managed_by=None)

        matches = self._matches(check_rbac_grants(), role_grant)

        self.assertEqual(len(matches), 1)
        self.assertIn('no longer managed', matches[0].summary)
        self.assertEqual(matches[0].details['stale_tenant_ids'], [managed.pk])

    def test_scope_less_grant_is_flagged(self):
        tenant = Tenant.objects.create(name='RG Scope-less', slug='rg-scope-less')
        role = Role.objects.create(tenant=tenant, name='Viewer', permissions=[])
        user = User.objects.create_user(username='rg_scope_less', password='pw')
        membership = Membership.objects.create(user=user, tenant=tenant)
        role_grant = RoleGrant.objects.create(membership=membership, role=role)

        matches = self._matches(check_rbac_grants(), role_grant)

        self.assertEqual(len(matches), 1)
        self.assertIn('has no scope', matches[0].summary)

    def test_elevated_direct_grant_without_metadata_is_flagged(self):
        tenant = Tenant.objects.create(name='RG Elevated', slug='rg-elevated')
        role = Role.objects.create(tenant=tenant, name='Viewer', permissions=[])
        user = User.objects.create_user(username='rg_elevated', password='pw')
        role_grant = grant(user, tenant, role)
        Role._base_manager.filter(pk=role.pk).update(
            permissions=['organization.change_tenant'],
        )

        matches = self._matches(check_rbac_grants(), role_grant)

        self.assertEqual(len(matches), 1)
        self.assertIn('lacks a reason or expiration', matches[0].summary)

    def test_soft_deleted_role_suppresses_finding(self):
        home = Tenant.objects.create(name='RG Home E', slug='rg-home-e')
        other = Tenant.objects.create(name='RG Other E', slug='rg-other-e')
        home_role = Role.objects.create(tenant=home, name='Home Viewer', permissions=[])
        foreign_role = Role.objects.create(tenant=other, name='Foreign Viewer', permissions=[])
        user = User.objects.create_user(username='rg_soft_deleted', password='pw')
        role_grant = grant(user, home, home_role)
        RoleGrant.objects.filter(pk=role_grant.pk).update(role=foreign_role)
        self.assertEqual(len(self._matches(check_rbac_grants(), role_grant)), 1)

        Role._base_manager.filter(pk=foreign_role.pk).update(deleted_at=timezone.now())

        self.assertEqual(self._matches(check_rbac_grants(), role_grant), [])


class UserGroupRBACCheckTests(_ContextResetMixin, TestCase):
    def test_group_role_owned_by_another_tenant_is_flagged(self):
        provider = Tenant.objects.create(name='UG Provider', slug='ug-provider', is_provider=True)
        managed = Tenant.objects.create(
            name='UG Managed', slug='ug-managed', managed_by=provider,
        )
        provider_role = Role.objects.create(tenant=provider, name='Provider Viewer', permissions=[])
        customer_role = Role.objects.create(tenant=managed, name='Customer Viewer', permissions=[])
        group = UserGroup.objects.create(name='Provider Team', tenant=provider)
        role_grant = RoleGrant.objects.create(user_group=group, role=provider_role)
        RoleGrantScope.objects.create(
            role_grant=role_grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )
        RoleGrant.objects.filter(pk=role_grant.pk).update(role=customer_role)

        matches = _findings_for(
            check_rbac_grants(), 'organization.RoleGrant', role_grant.pk,
        )

        self.assertTrue(matches)
        self.assertTrue(any(match.classification == CLASS_PROVIDER_MANAGED for match in matches))

    def test_group_membership_from_another_tenant_is_flagged(self):
        owner = Tenant.objects.create(name='UG Owner', slug='ug-owner')
        other = Tenant.objects.create(name='UG Other', slug='ug-other')
        group = UserGroup.objects.create(name='Owner Team', tenant=owner)
        user = User.objects.create_user(username='ug_wrong_membership', password='pw')
        owner_membership = Membership.objects.create(user=user, tenant=owner)
        other_membership = Membership.objects.create(user=user, tenant=other)
        link = GroupMembership.objects.create(
            user_group=group,
            membership=owner_membership,
        )
        GroupMembership.objects.filter(pk=link.pk).update(membership=other_membership)

        matches = _findings_for(check_rbac_grants(), 'users.GroupMembership', link.pk)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].classification, CLASS_INVALID)
        self.assertIn('differs from group owner', matches[0].summary)

    def test_consistent_group_principal_has_no_findings(self):
        tenant = Tenant.objects.create(name='UG Tenant', slug='ug-tenant')
        role = Role.objects.create(tenant=tenant, name='Viewer', permissions=[])
        group = UserGroup.objects.create(name='Tenant Team', tenant=tenant)
        user = User.objects.create_user(username='ug_member', password='pw')
        membership = Membership.objects.create(user=user, tenant=tenant)
        GroupMembership.objects.create(user_group=group, membership=membership)
        role_grant = RoleGrant.objects.create(user_group=group, role=role)
        RoleGrantScope.objects.create(
            role_grant=role_grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )

        findings = check_rbac_grants()

        self.assertEqual(
            _findings_for(findings, 'organization.RoleGrant', role_grant.pk),
            [],
        )
        self.assertEqual(
            _findings_for(
                findings,
                'users.GroupMembership',
                GroupMembership.objects.get().pk,
            ),
            [],
        )


class IntegrityReportCommandTests(_ContextResetMixin, TestCase):
    def _seed_rbac_anomaly(self):
        home = Tenant.objects.create(name='CMD Home', slug='cmd-home')
        other = Tenant.objects.create(name='CMD Other', slug='cmd-other')
        home_role = Role.objects.create(tenant=home, name='Home Viewer', permissions=[])
        foreign_role = Role.objects.create(tenant=other, name='Foreign Viewer', permissions=[])
        user = User.objects.create_user(username='cmd_anomaly', password='pw')
        role_grant = grant(user, home, home_role)
        RoleGrant.objects.filter(pk=role_grant.pk).update(role=foreign_role)
        return role_grant

    def test_clean_db_reports_no_findings(self):
        out = StringIO()
        call_command('integrity_report', stdout=out)

        self.assertIn('No integrity findings', out.getvalue())

    def test_seeded_anomaly_reports_check_title_and_classification(self):
        self._seed_rbac_anomaly()
        out = StringIO()
        call_command('integrity_report', stdout=out)

        output = out.getvalue()
        self.assertIn('RBAC grants: role owner vs principal tenant', output)
        self.assertIn('[unrelated-invalid]', output)

    def test_json_output_parses_with_expected_keys(self):
        self._seed_rbac_anomaly()
        out = StringIO()
        call_command('integrity_report', as_json=True, stdout=out)

        payload = json.loads(out.getvalue())
        self.assertIn('findings', payload)
        self.assertIn('proposals', payload)
        self.assertIn('stats', payload)
        self.assertTrue(any(
            finding['check'] == 'rbac_grant_inconsistent'
            and finding['classification'] == 'unrelated-invalid'
            for finding in payload['findings']
        ))

    def test_proposals_flag_writes_json_list(self):
        self._seed_rbac_anomaly()
        with tempfile.TemporaryDirectory() as tmp_dir:
            proposals_path = Path(tmp_dir) / 'proposals.json'
            out = StringIO()
            call_command('integrity_report', proposals=str(proposals_path), stdout=out)

            self.assertTrue(proposals_path.exists())
            self.assertIsInstance(
                json.loads(proposals_path.read_text(encoding='utf-8')),
                list,
            )

    def test_fail_on_findings_raises_systemexit(self):
        self._seed_rbac_anomaly()
        with self.assertRaises(SystemExit):
            call_command('integrity_report', fail_on_findings=True, stdout=StringIO())

    def test_fail_on_findings_does_not_raise_when_clean(self):
        out = StringIO()
        call_command('integrity_report', fail_on_findings=True, stdout=out)

        self.assertIn('No integrity findings', out.getvalue())
