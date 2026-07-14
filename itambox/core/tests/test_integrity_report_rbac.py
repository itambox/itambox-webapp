"""Regression tests for ``core.integrity.check_rbac_grants`` and the
``integrity_report`` management command (data-model remediation plan, phase 1).

These tests operate purely at the model layer (no HTTP requests, no active
tenant/user context) so the tenant-scoping managers on Role/Tenant/TenantGroup
resolve unscoped (see core/managers.py::TenantScopingQuerySet.filter_by_tenant:
with no current user bound, the "fail closed" branch is skipped and the full
queryset is returned) — exactly like ``check_rbac_grants`` itself, which reads
everything through ``_base_manager``/unscoped managers by design.

Anomalous rows that the model layer's own ``clean()`` would reject on create
are seeded by creating a VALID row first and mutating it into the anomalous
shape via ``QuerySet.update()``, which bypasses ``save()``/the pre_save
validator (see ``core/tests/mixins.py`` conventions and neighbouring RBAC
tests such as ``organization/tests/test_managed_only_group_access.py``).
"""
import json
import tempfile
from io import StringIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core.integrity import (
    CLASS_AMBIGUOUS,
    CLASS_INVALID,
    CLASS_PROVIDER_MANAGED,
    check_rbac_grants,
)
from core.managers import set_current_membership, set_current_tenant, set_current_tenant_group
from core.tests.mixins import grant
from organization.models import Membership, Role, RoleAssignment, Tenant, TenantGroup
from users.models import UserGroup

User = get_user_model()


def _findings_for(findings, model_label, pk):
    return [f for f in findings if f.model == model_label and f.pk == pk]


class _ContextResetMixin:
    """Belt-and-suspenders context reset (conftest already does this after
    every test; resetting on entry too keeps these tests order-independent)."""

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


# --------------------------------------------------------------------------- RoleAssignment
class RoleAssignmentRBACCheckTests(_ContextResetMixin, TestCase):
    """check_rbac_grants(): every RoleAssignment shape from the ADR-0001 target
    semantics, plus the anomalies that fall outside it."""

    def _matches(self, findings, assignment):
        return _findings_for(findings, 'organization.RoleAssignment', assignment.pk)

    # (a) own-reach grant of a role owned by the membership tenant -> NO finding.
    def test_own_reach_role_owned_by_membership_tenant_is_clean(self):
        tenant = Tenant.objects.create(name='RA Own A', slug='ra-own-a')
        role = Role.objects.create(tenant=tenant, name='Own Role A', permissions=[])
        user = User.objects.create_user(username='ra_own_a', password='pw')
        assignment = grant(user, tenant, role)

        findings = check_rbac_grants()

        self.assertEqual(self._matches(findings, assignment), [])

    # (b) own-reach grant of a provider-owned shared role into a managed tenant -> NO finding.
    def test_own_reach_shared_role_into_managed_tenant_is_clean(self):
        provider = Tenant.objects.create(name='RA Prov B', slug='ra-prov-b', is_provider=True)
        managed = Tenant.objects.create(
            name='RA Managed B', slug='ra-managed-b', managed_by=provider,
        )
        role = Role.objects.create(
            tenant=provider, name='Shared Role B', shared_with_managed=True, permissions=[],
        )
        user = User.objects.create_user(username='ra_shared_b', password='pw')
        # Own-reach grant on a membership anchored in the MANAGED tenant.
        assignment = grant(user, managed, role)

        findings = check_rbac_grants()

        self.assertEqual(self._matches(findings, assignment), [])

    # (c) same as (b) but shared_with_managed=False -> finding.
    def test_own_reach_non_shared_role_into_managed_tenant_is_flagged(self):
        provider = Tenant.objects.create(name='RA Prov C', slug='ra-prov-c', is_provider=True)
        managed = Tenant.objects.create(
            name='RA Managed C', slug='ra-managed-c', managed_by=provider,
        )
        role = Role.objects.create(
            tenant=provider, name='Unshared Role C', shared_with_managed=False, permissions=[],
        )
        user = User.objects.create_user(username='ra_unshared_c', password='pw')
        assignment = grant(user, managed, role)

        findings = check_rbac_grants()

        matches = self._matches(findings, assignment)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].check, 'rbac_grant_inconsistent')
        self.assertEqual(matches[0].classification, CLASS_PROVIDER_MANAGED)
        self.assertFalse(matches[0].details['shared_with_managed'])

    # (d) own-reach grant of a role owned by a completely unrelated tenant -> unrelated-invalid.
    def test_own_reach_role_from_unrelated_tenant_is_invalid(self):
        home = Tenant.objects.create(name='RA Home D', slug='ra-home-d')
        other = Tenant.objects.create(name='RA Unrelated D', slug='ra-unrelated-d')
        role = Role.objects.create(tenant=other, name='Foreign Role D', permissions=[])
        user = User.objects.create_user(username='ra_unrelated_d', password='pw')
        assignment = grant(user, home, role)

        findings = check_rbac_grants()

        matches = self._matches(findings, assignment)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].check, 'rbac_grant_inconsistent')
        self.assertEqual(matches[0].classification, CLASS_INVALID)
        self.assertEqual(CLASS_INVALID, 'unrelated-invalid')

    # (e) managed-reach assignment whose membership tenant is NOT a provider -> finding.
    def test_managed_reach_with_non_provider_membership_tenant_is_flagged(self):
        tenant = Tenant.objects.create(name='RA NonProv E', slug='ra-nonprov-e')
        role = Role.objects.create(tenant=tenant, name='Role E', permissions=[])
        user = User.objects.create_user(username='ra_nonprov_e', password='pw')
        # Create as a VALID own-reach grant first (clean() forbids reach=managed
        # on a non-provider membership tenant at create time), then mutate.
        assignment = grant(user, tenant, role)
        RoleAssignment.objects.filter(pk=assignment.pk).update(
            reach=RoleAssignment.REACH_MANAGED,
        )

        findings = check_rbac_grants()

        matches = self._matches(findings, assignment)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].classification, CLASS_INVALID)
        self.assertIn('membership tenant is not a provider', matches[0].summary)

    # (f) managed-reach assignment whose role belongs to a managed tenant, not the provider.
    def test_managed_reach_role_owned_by_managed_tenant_is_flagged(self):
        provider = Tenant.objects.create(name='RA Prov F', slug='ra-prov-f', is_provider=True)
        managed = Tenant.objects.create(
            name='RA Managed F', slug='ra-managed-f', managed_by=provider,
        )
        role = Role.objects.create(tenant=managed, name='Role F', permissions=[])
        user = User.objects.create_user(username='ra_provrole_f', password='pw')
        assignment = grant(user, provider, role, reach=RoleAssignment.REACH_MANAGED)

        findings = check_rbac_grants()

        matches = self._matches(findings, assignment)
        self.assertEqual(len(matches), 1)
        self.assertIn(
            'managed-reach role must be owned by the granting provider', matches[0].summary,
        )

    # (g) managed-reach explicit scope whose assigned_tenants includes a tenant no
    # longer managed by the provider -> finding mentioning stale coverage.
    def test_managed_reach_explicit_scope_with_stale_tenant_is_flagged(self):
        provider = Tenant.objects.create(name='RA Prov G', slug='ra-prov-g', is_provider=True)
        managed = Tenant.objects.create(
            name='RA Managed G', slug='ra-managed-g', managed_by=provider,
        )
        role = Role.objects.create(
            tenant=provider, name='Role G', shared_with_managed=True, permissions=[],
        )
        user = User.objects.create_user(username='ra_stale_g', password='pw')
        assignment = grant(
            user, provider, role,
            reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_EXPLICIT,
            assigned_tenants=[managed],
        )
        # The provider no longer manages this tenant, but the grant's explicit
        # coverage still references it.
        Tenant._base_manager.filter(pk=managed.pk).update(managed_by=None)

        findings = check_rbac_grants()

        matches = self._matches(findings, assignment)
        self.assertEqual(len(matches), 1)
        self.assertIn('no longer managed', matches[0].summary)
        self.assertEqual(matches[0].details['stale_tenant_ids'], [managed.pk])

    # (h) tenant_group scope with scope_group=NULL -> finding.
    def test_managed_reach_tenant_group_scope_without_group_is_flagged(self):
        provider = Tenant.objects.create(name='RA Prov H', slug='ra-prov-h', is_provider=True)
        group = TenantGroup.objects.create(name='RA Group H', slug='ra-group-h')
        role = Role.objects.create(tenant=provider, name='Role H', permissions=[])
        user = User.objects.create_user(username='ra_group_h', password='pw')
        assignment = grant(
            user, provider, role,
            reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_TENANT_GROUP,
            scope_group=group,
        )
        RoleAssignment.objects.filter(pk=assignment.pk).update(scope_group=None)

        findings = check_rbac_grants()

        matches = self._matches(findings, assignment)
        self.assertEqual(len(matches), 1)
        self.assertIn('tenant_group scope without a scope group', matches[0].summary)

    # Soft-deleted roles must not produce findings even when the underlying
    # grant shape is otherwise anomalous.
    def test_soft_deleted_role_suppresses_the_finding(self):
        home = Tenant.objects.create(name='RA Home I', slug='ra-home-i')
        other = Tenant.objects.create(name='RA Unrelated I', slug='ra-unrelated-i')
        role = Role.objects.create(tenant=other, name='Foreign Role I', permissions=[])
        user = User.objects.create_user(username='ra_softdel_i', password='pw')
        assignment = grant(user, home, role)

        findings_before = check_rbac_grants()
        self.assertEqual(len(self._matches(findings_before, assignment)), 1)

        Role._base_manager.filter(pk=role.pk).update(deleted_at=timezone.now())

        findings_after = check_rbac_grants()
        self.assertEqual(self._matches(findings_after, assignment), [])


# --------------------------------------------------------------------------- UserGroup
class UserGroupRBACCheckTests(_ContextResetMixin, TestCase):
    """check_rbac_grants(): UserGroup ownership/membership consistency."""

    def _matches(self, findings, group):
        return _findings_for(findings, 'users.UserGroup', group.pk)

    # group with tenant=NULL -> finding.
    def test_group_without_owning_tenant_is_flagged(self):
        group = UserGroup.objects.create(name='UG NoTenant A', tenant=None)

        findings = check_rbac_grants()

        matches = self._matches(findings, group)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].check, 'rbac_group_inconsistent')
        self.assertEqual(matches[0].classification, CLASS_AMBIGUOUS)
        self.assertIn('has no owning tenant', matches[0].summary)

    # group carrying a role owned by another tenant -> finding, classified
    # provider-to-managed when the role owner is managed by the group's tenant.
    def test_group_with_role_from_managed_tenant_is_flagged(self):
        provider = Tenant.objects.create(name='UG Prov B', slug='ug-prov-b', is_provider=True)
        managed = Tenant.objects.create(
            name='UG Managed B', slug='ug-managed-b', managed_by=provider,
        )
        role = Role.objects.create(tenant=managed, name='UG Role B', permissions=[])
        group = UserGroup.objects.create(name='UG Group B', tenant=provider)
        group.roles.add(role)

        findings = check_rbac_grants()

        matches = self._matches(findings, group)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].check, 'rbac_group_inconsistent')
        self.assertEqual(matches[0].classification, CLASS_PROVIDER_MANAGED)
        self.assertEqual(matches[0].details['role_id'], role.pk)

    # group member without an active membership in the owning tenant -> finding.
    def test_group_member_without_active_membership_is_flagged(self):
        tenant = Tenant.objects.create(name='UG Tenant C', slug='ug-tenant-c')
        group = UserGroup.objects.create(name='UG Group C', tenant=tenant)
        user = User.objects.create_user(username='ug_orphan_c', password='pw')
        group.members.add(user)

        findings = check_rbac_grants()

        matches = self._matches(findings, group)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].check, 'rbac_group_inconsistent')
        self.assertEqual(matches[0].classification, CLASS_AMBIGUOUS)
        self.assertIn('no active membership', matches[0].summary)
        self.assertEqual(matches[0].details['user_id'], user.pk)

    # fully consistent group -> NO findings.
    def test_fully_consistent_group_has_no_findings(self):
        tenant = Tenant.objects.create(name='UG Tenant D', slug='ug-tenant-d')
        role = Role.objects.create(tenant=tenant, name='UG Role D', permissions=[])
        group = UserGroup.objects.create(name='UG Group D', tenant=tenant)
        group.roles.add(role)
        user = User.objects.create_user(username='ug_member_d', password='pw')
        Membership.objects.create(user=user, tenant=tenant)
        group.members.add(user)

        findings = check_rbac_grants()

        self.assertEqual(self._matches(findings, group), [])


# --------------------------------------------------------------------------- management command
class IntegrityReportCommandTests(_ContextResetMixin, TestCase):
    """`manage.py integrity_report` — CLI surface over core.integrity.run_all_checks."""

    def _seed_rbac_anomaly(self):
        """An own-reach grant of a role from a completely unrelated tenant —
        deterministically produces exactly one 'unrelated-invalid' RBAC finding."""
        home = Tenant.objects.create(name='CMD Home', slug='cmd-home')
        other = Tenant.objects.create(name='CMD Unrelated', slug='cmd-unrelated')
        role = Role.objects.create(tenant=other, name='CMD Foreign Role', permissions=[])
        user = User.objects.create_user(username='cmd_anomaly_user', password='pw')
        return grant(user, home, role)

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
            f['check'] == 'rbac_grant_inconsistent' and f['classification'] == 'unrelated-invalid'
            for f in payload['findings']
        ))

    def test_proposals_flag_writes_a_json_list_file(self):
        self._seed_rbac_anomaly()
        with tempfile.TemporaryDirectory() as tmp_dir:
            proposals_path = Path(tmp_dir) / 'p.json'
            out = StringIO()
            call_command('integrity_report', proposals=str(proposals_path), stdout=out)

            self.assertTrue(proposals_path.exists())
            payload = json.loads(proposals_path.read_text(encoding='utf-8'))
            self.assertIsInstance(payload, list)

    def test_fail_on_findings_raises_systemexit_when_findings_exist(self):
        self._seed_rbac_anomaly()
        out = StringIO()

        with self.assertRaises(SystemExit):
            call_command('integrity_report', fail_on_findings=True, stdout=out)

    def test_fail_on_findings_does_not_raise_when_clean(self):
        out = StringIO()

        # Must not raise.
        call_command('integrity_report', fail_on_findings=True, stdout=out)

        self.assertIn('No integrity findings', out.getvalue())
