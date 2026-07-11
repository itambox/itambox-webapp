"""WS4 regression suite — explicit managed-scope (assigned_tenants) is audited.

``ChangeLoggingMixin.save()`` cannot capture a many-to-many edit: Django fires
``m2m_changed`` AFTER ``save()`` returns, so the old ``assigned_tenants.set()``
left the audit trail showing an empty explicit scope on creation and NO row at
all for later edits. ``RoleAssignment.set_assigned_tenants()`` (via
``ChangeLoggingMixin.log_m2m_change``) is now the single supported writer and
records each real change in ``ObjectChange`` — attributed to the membership's
tenant, the current request, and the acting user — while still clearing effective
permission caches. See ``RBAC_STAGE3_POST_REVIEW_FIX_PLAN.md`` §4.
"""
import uuid

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from core.managers import set_current_tenant, set_current_membership
from core.models import ObjectChange
from core.tests.mixins import grant
from itambox.middleware import _current_user, _request_id
from organization.models import Membership, Role, RoleAssignment, Tenant

User = get_user_model()


class ManagedScopeAuditTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.provider = Tenant.objects.create(name='Audit P', slug='audit-p', is_provider=True)
        self.cust_a = Tenant.objects.create(name='Audit A', slug='audit-a', managed_by=self.provider)
        self.cust_b = Tenant.objects.create(name='Audit B', slug='audit-b', managed_by=self.provider)

        self.actor = User.objects.create_user(username='audit_actor', password='pw')
        self.actor2 = User.objects.create_user(username='audit_actor2', password='pw')
        self.member = User.objects.create_user(username='audit_member', password='pw')
        self.role = Role.objects.create(tenant=self.provider, name='Audit Role', permissions=[])

        # A managed-reach assignment (explicit scope, initially empty).
        self.membership = Membership.objects.create(user=self.member, tenant=self.provider)
        self.assignment = RoleAssignment.objects.create(
            membership=self.membership, role=self.role,
            reach=RoleAssignment.REACH_MANAGED, managed_scope=RoleAssignment.SCOPE_EXPLICIT,
        )

        # Establish a request context so the changelog actually records rows.
        self.request_id = uuid.uuid4()
        _current_user.set(self.actor)
        _request_id.set(self.request_id)

    def tearDown(self):
        _current_user.set(None)
        _request_id.set(None)
        set_current_tenant(None)
        set_current_membership(None)

    def _ct(self):
        return ContentType.objects.get_for_model(RoleAssignment)

    def _updates(self):
        return ObjectChange._base_manager.filter(
            changed_object_type=self._ct(), changed_object_id=self.assignment.pk,
            action='update',
        ).order_by('time')

    def test_new_explicit_scope_is_recorded_with_the_tenant_ids(self):
        changed = self.assignment.set_assigned_tenants([self.cust_a], actor=self.actor)
        self.assertTrue(changed)
        oc = self._updates().last()
        self.assertIn(self.cust_a.pk, oc.postchange_data.get('assigned_tenants'))
        # Attributed to the membership's tenant (via changelog_tenant_lookup) + actor.
        self.assertEqual(oc.tenant_id, self.provider.pk)
        self.assertEqual(oc.user_id, self.actor.pk)
        self.assertEqual(oc.request_id, self.request_id)

    def test_replacing_a_with_b_records_pre_a_and_post_b(self):
        self.assignment.set_assigned_tenants([self.cust_a], actor=self.actor)
        self.assignment.set_assigned_tenants([self.cust_b], actor=self.actor2)
        oc = self._updates().last()
        self.assertEqual(oc.prechange_data.get('assigned_tenants'), [self.cust_a.pk])
        self.assertEqual(oc.postchange_data.get('assigned_tenants'), [self.cust_b.pk])
        self.assertEqual(oc.user_id, self.actor2.pk)
        self.assertEqual(oc.tenant_id, self.provider.pk)

    def test_reapplying_the_same_set_writes_no_row(self):
        self.assignment.set_assigned_tenants([self.cust_a], actor=self.actor)
        before = self._updates().count()
        result = self.assignment.set_assigned_tenants([self.cust_a], actor=self.actor)
        self.assertFalse(result)
        self.assertEqual(self._updates().count(), before)

    def test_clearing_explicit_tenants_is_logged(self):
        self.assignment.set_assigned_tenants([self.cust_a], actor=self.actor)
        result = self.assignment.set_assigned_tenants([], actor=self.actor)
        self.assertTrue(result)
        oc = self._updates().last()
        self.assertEqual(oc.prechange_data.get('assigned_tenants'), [self.cust_a.pk])
        self.assertEqual(oc.postchange_data.get('assigned_tenants'), [])

    def test_permission_caches_are_cleared_on_every_real_change(self):
        # Memoize a fake per-tenant perm cache on the member, then mutate scope.
        self.member._perms_tenant_999 = frozenset()
        self.member._tenant_membership_999 = object()
        self.assignment.membership.user = self.member  # ensure the signal sees this instance
        self.assignment.set_assigned_tenants([self.cust_a], actor=self.actor)
        self.assertFalse(hasattr(self.member, '_perms_tenant_999'))
        self.assertFalse(hasattr(self.member, '_tenant_membership_999'))

    def test_form_created_explicit_assignment_audit_contains_ids(self):
        # End-to-end through the form's save path: a brand-new explicit managed grant
        # must leave the selected tenant ids reconstructable from ObjectChange.
        from organization.forms.membership_form import MembershipForm
        from ._membership_form_helpers import membership_post_data

        superuser = User.objects.create_superuser(
            username='audit_su', email='audit_su@x.com', password='pw',
        )
        _current_user.set(superuser)
        newcomer = User.objects.create_user(username='audit_newcomer', password='pw')
        form = MembershipForm(
            data=membership_post_data(
                user=newcomer.pk, tenant=self.provider.pk,
                managed=[{
                    'role': self.role.pk, 'managed_scope': RoleAssignment.SCOPE_EXPLICIT,
                    'assigned_tenants': [self.cust_a.pk, self.cust_b.pk],
                }],
            ),
            tenant=self.provider, user=superuser,
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        membership = form.save()
        new_assignment = membership.assignments.get(
            role=self.role, reach=RoleAssignment.REACH_MANAGED,
        )
        updates = ObjectChange._base_manager.filter(
            changed_object_type=self._ct(), changed_object_id=new_assignment.pk,
            action='update',
        )
        recorded = set()
        for oc in updates:
            recorded.update(oc.postchange_data.get('assigned_tenants') or [])
        self.assertEqual(recorded, {self.cust_a.pk, self.cust_b.pk})
