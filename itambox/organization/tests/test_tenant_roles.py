"""Role/backend security coverage for the unified RBAC model (RBAC_STAGE2_SPEC.md).

Post-collapse there is exactly one container (Tenant), one permission vocabulary
('app.codename'), and per-grant RoleAssignment rows. A managing (MSP) tenant is
just ``Tenant(is_provider=True)``; a customer tenant points at it via
``managed_by``; reach into managed tenants is a property of the individual
RoleAssignment (``reach='managed'`` + a scope refinement), not of a separate
Provider container. Grants are authored with the ``core.tests.mixins.grant``
helper, which creates the Membership anchor + one RoleAssignment row.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from organization.models import Tenant, Membership, Role, RoleAssignment
from organization.forms import RoleForm as TenantRoleForm, MembershipForm
from core.auth import MembershipBackend
from core.managers import set_current_tenant, set_current_membership
from core.tests.mixins import grant

User = get_user_model()


class TenantRoleSecurityTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)

        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")

        self.super_user = User.objects.create_superuser(
            username='superuser', email='super@example.com', password='password123'
        )
        self.user_a = User.objects.create_user(
            username='usera', email='usera@example.com', password='password123'
        )
        self.user_b = User.objects.create_user(
            username='userb', email='userb@example.com', password='password123'
        )

    def test_role_scoping_to_tenant(self):
        # Role.tenant pins a role to exactly one container. It's no longer hidden
        # by a tenant-scoping manager on the read side — instead the auth backend
        # gates effective perms per tenant via the RoleAssignment join: a role
        # attached through an own-reach grant in tenant_a never projects into
        # tenant_b.
        role_a = Role.objects.create(
            tenant=self.tenant_a,
            name="Alpha Admin",
            permissions=["assets.view_asset"],
        )
        self.assertEqual(role_a.tenant, self.tenant_a)
        self.assertIn(role_a, Role.objects.filter(tenant=self.tenant_a))
        self.assertNotIn(role_a, Role.objects.filter(tenant=self.tenant_b))
        grant(self.user_a, self.tenant_a, role_a)

        backend = MembershipBackend()
        self.assertIn('assets.view_asset', backend._effective_perms_for_tenant(self.user_a, self.tenant_a))
        self.assertNotIn('assets.view_asset', backend._effective_perms_for_tenant(self.user_a, self.tenant_b))

    def test_form_serialization_and_deserialization(self):
        # Create role using TenantRoleForm
        form_data = {
            'name': 'Custom Asset Specialist',
            'description': 'Can view and change assets',
            'perm_asset_read': True,
            'perm_asset_create': True,
            'perm_asset_edit': True,
            'perm_asset_delete': False,
            'perm_add_delegated_assetrequest': True,
        }

        form = TenantRoleForm(data=form_data, tenant=self.tenant_a, user=self.super_user)
        self.assertTrue(form.is_valid(), form.errors)
        role = form.save()

        # Verify packed permissions list
        self.assertIn('assets.view_asset', role.permissions)
        self.assertIn('assets.add_asset', role.permissions)
        self.assertIn('assets.change_asset', role.permissions)
        self.assertNotIn('assets.delete_asset', role.permissions)
        self.assertIn('assets.add_delegated_assetrequest', role.permissions)
        # Dashboard permissions are automatically added
        self.assertIn('extras.view_dashboard', role.permissions)

        # Verify deserialization into form initial values
        edit_form = TenantRoleForm(instance=role, tenant=self.tenant_a, user=self.super_user)
        self.assertTrue(edit_form.fields['perm_asset_read'].initial)
        self.assertTrue(edit_form.fields['perm_asset_create'].initial)
        self.assertTrue(edit_form.fields['perm_asset_edit'].initial)
        self.assertFalse(edit_form.fields['perm_asset_delete'].initial)
        self.assertTrue(edit_form.fields['perm_add_delegated_assetrequest'].initial)

    def test_permission_backend_resolution(self):
        role = Role.objects.create(
            tenant=self.tenant_a,
            name="ReadOnly Member",
            permissions=["assets.view_asset", "extras.view_dashboard"]
        )
        assignment = grant(self.user_a, self.tenant_a, role)

        set_current_membership(assignment.membership)
        set_current_tenant(self.tenant_a)

        self.assertTrue(self.user_a.has_perm('assets.view_asset'))
        self.assertFalse(self.user_a.has_perm('assets.add_asset'))
        self.assertFalse(self.user_a.has_perm('assets.delete_asset'))
        set_current_membership(None)
        set_current_tenant(None)

    def test_managed_reach_projects_tenant_permissions_without_stripping(self):
        # H4 (High), ported to the per-grant model: an MSP-staff member's
        # managed-reach RoleAssignment on their membership at the managing
        # (is_provider) tenant projects the role's permissions into a managed
        # tenant the grant's refinement covers. There is no capability
        # vocabulary left to strip (that's DELETED, see RBAC_STAGE2_SPEC.md §6) —
        # whatever the role carries projects as-is; the escalation guard (not
        # stripping) is what keeps grants honest.
        msp = Tenant.objects.create(name="MSP Provider", slug="msp-provider-h4", is_provider=True)
        self.tenant_a.managed_by = msp
        self.tenant_a.save()

        staff_user = User.objects.create_user(
            username='staffuser', email='staff@example.com', password='password123'
        )
        staff_role = Role.objects.create(
            tenant=msp,
            name="MSP Tech",
            permissions=["assets.view_asset", "assets.change_asset"],
        )
        staff_membership = Membership.objects.create(user=staff_user, tenant=msp, is_active=True)
        assignment = RoleAssignment.objects.create(
            membership=staff_membership, role=staff_role,
            reach=RoleAssignment.REACH_MANAGED, managed_scope=RoleAssignment.SCOPE_ALL,
        )

        backend = MembershipBackend()
        # 1. SCOPE_ALL -> covered in tenant_a (managed by msp) but NOT tenant_b
        # (unmanaged — a different/no provider).
        self.assertTrue(backend.has_perm(staff_user, 'assets.view_asset', self.tenant_a))
        self.assertFalse(backend.has_perm(staff_user, 'assets.view_asset', self.tenant_b))

        # 2. SCOPE_EXPLICIT without tenant_a assigned -> no coverage. Saving the
        # assignment busts the user's perm cache via the RoleAssignment post_save
        # signal (organization/signals.py) — no manual cache-clearing needed.
        assignment.managed_scope = RoleAssignment.SCOPE_EXPLICIT
        assignment.save()
        self.assertFalse(backend.has_perm(staff_user, 'assets.view_asset', self.tenant_a))

        # 3. SCOPE_EXPLICIT with tenant_a assigned -> covered again. The m2m_changed
        # signal on assigned_tenants busts the cache too.
        assignment.assigned_tenants.add(self.tenant_a)
        self.assertTrue(backend.has_perm(staff_user, 'assets.view_asset', self.tenant_a))

        set_current_membership(None)
        set_current_tenant(None)

    def test_role_permissions_tolerated_at_model_layer(self):
        # M6 (design): Role.permissions is deliberately NOT hard-validated at the model layer.
        # A global pre_save signal (core/signals.py) runs clean() on every save, so a hard
        # codename check would break the seed (whose tenant Administrator role is granted the
        # full permission set) and the validate_role_permissions audit command (which must be
        # able to persist a stale codename in order to detect it).
        # Codename hygiene is enforced by the form (drops unknown codenames) and audited
        # post-hoc by validate_role_permissions.
        role = Role.objects.create(
            tenant=self.tenant_a,
            name="Tolerant Role",
            permissions=["assets.view_asset", "nonexistent.permission_codename"],
        )
        role.refresh_from_db()
        self.assertIn("nonexistent.permission_codename", role.permissions)

    def test_purchase_order_permissions_form_and_backend(self):
        # Create a role with PO permissions using TenantRoleForm
        form_data = {
            'name': 'Procurement Officer',
            'description': 'Can manage, approve and receive purchase orders',
            'perm_purchaseorder_read': True,
            'perm_purchaseorder_create': True,
            'perm_purchaseorder_edit': True,
            'perm_purchaseorder_delete': True,
            'perm_approve_purchaseorder': True,
            'perm_receive_purchaseorder': True,
        }

        form = TenantRoleForm(data=form_data, tenant=self.tenant_a, user=self.super_user)
        self.assertTrue(form.is_valid(), form.errors)
        role = form.save()

        # Verify packed permissions list
        self.assertIn('procurement.view_purchaseorder', role.permissions)
        self.assertIn('procurement.add_purchaseorder', role.permissions)
        self.assertIn('procurement.change_purchaseorder', role.permissions)
        self.assertIn('procurement.delete_purchaseorder', role.permissions)
        self.assertIn('procurement.approve_purchaseorder', role.permissions)
        self.assertIn('procurement.receive_purchaseorder', role.permissions)

        # Verify deserialization
        edit_form = TenantRoleForm(instance=role, tenant=self.tenant_a, user=self.super_user)
        self.assertTrue(edit_form.fields['perm_purchaseorder_read'].initial)
        self.assertTrue(edit_form.fields['perm_purchaseorder_create'].initial)
        self.assertTrue(edit_form.fields['perm_purchaseorder_edit'].initial)
        self.assertTrue(edit_form.fields['perm_purchaseorder_delete'].initial)
        self.assertTrue(edit_form.fields['perm_approve_purchaseorder'].initial)
        self.assertTrue(edit_form.fields['perm_receive_purchaseorder'].initial)

        # Verify backend resolution for user
        assignment = grant(self.user_a, self.tenant_a, role)
        set_current_membership(assignment.membership)
        set_current_tenant(self.tenant_a)

        self.assertTrue(self.user_a.has_perm('procurement.view_purchaseorder'))
        self.assertTrue(self.user_a.has_perm('procurement.add_purchaseorder'))
        self.assertTrue(self.user_a.has_perm('procurement.change_purchaseorder'))
        self.assertTrue(self.user_a.has_perm('procurement.delete_purchaseorder'))
        self.assertTrue(self.user_a.has_perm('procurement.approve_purchaseorder'))
        self.assertTrue(self.user_a.has_perm('procurement.receive_purchaseorder'))

        set_current_membership(None)
        set_current_tenant(None)

    def test_privilege_escalation_validation(self):
        # User A is a ReadOnly member
        reader_role = Role.objects.create(
            tenant=self.tenant_a,
            name="Reader",
            permissions=["assets.view_asset", "extras.view_dashboard"]
        )
        assignment_a = grant(self.user_a, self.tenant_a, reader_role)

        set_current_membership(assignment_a.membership)
        set_current_tenant(self.tenant_a)

        # User A tries to create a role with Delete Asset permission (Privilege Escalation!)
        form_data = {
            'name': 'Rogue Admin',
            'description': 'Elevated permissions',
            'perm_asset_read': True,
            'perm_asset_delete': True, # User A does not have delete_asset!
        }

        form = TenantRoleForm(data=form_data, tenant=self.tenant_a, user=self.user_a)
        self.assertFalse(form.is_valid())
        self.assertIn('__all__', form.errors)
        self.assertTrue(any("Privilege escalation detected" in e for e in form.errors['__all__']))

        set_current_membership(None)
        set_current_tenant(None)

    def test_invalid_codenames_are_dropped_not_blocking(self):
        """A matrix row that maps to a permission which doesn't exist (a model without
        that action, an uninstalled plugin, or an app-label typo) must NOT block the
        save. The bogus codename is dropped; valid permissions still save. (Previously
        the whole save was rejected, which blocked legitimate roles on any matrix bug.)"""
        from organization.forms.role_form import MATRIX_MODELS

        MATRIX_MODELS['mock_invalid'] = {
            'label': 'Invalid Model',
            'app': 'nonexistentapp',
            'model_name': 'do_magic',
        }
        try:
            form_data = {
                'name': 'Magician',
                'description': 'Uses a nonexistent permission alongside a real one',
                'perm_mock_invalid_read': True,   # nonexistentapp.view_do_magic -> dropped
                'perm_asset_read': True,          # assets.view_asset -> kept
            }
            form = TenantRoleForm(data=form_data, tenant=self.tenant_a, user=self.super_user)
            self.assertTrue(form.is_valid(), form.errors)
            self.assertNotIn('nonexistentapp.view_do_magic', form.instance.permissions)
            self.assertIn('assets.view_asset', form.instance.permissions)
        finally:
            del MATRIX_MODELS['mock_invalid']

    def test_role_soft_delete_excludes_from_effective_perms_but_keeps_assignment(self):
        """Role uses SoftDeleteMixin, so role.delete() sets ``deleted_at`` rather than
        removing the row — unlike the old roles-M2M world, there is no FK PROTECT (or
        CASCADE) to observe on delete. The RoleAssignment pointing at a soft-deleted
        role survives as the audit trail, but the auth backend (and the Role
        post_save cache-busting signal) treat a soft-deleted role as inert: its
        permissions stop projecting immediately."""
        role = Role.objects.create(
            tenant=self.tenant_a, name="Deletable?", permissions=["assets.view_asset"],
        )
        assignment = grant(self.user_a, self.tenant_a, role)

        backend = MembershipBackend()
        self.assertIn('assets.view_asset', backend._effective_perms_for_tenant(self.user_a, self.tenant_a))

        role.delete()  # soft delete (default) — sets deleted_at, still a save()
        self.assertIsNotNone(role.deleted_at)

        # The grant row itself survives — it's the audit trail, not a live-permission
        # source — and so does the membership.
        self.assertTrue(RoleAssignment.objects.filter(pk=assignment.pk).exists())
        self.assertTrue(Membership.objects.filter(pk=assignment.membership_id).exists())

        # But the soft-deleted role's permissions no longer project. The Role
        # post_save cache-busting signal clears the cache on a freshly DB-fetched
        # user instance (it walks `role.assignments`), not on this test's
        # long-lived `self.user_a` handle — re-fetch to observe the post-delete
        # state cleanly (a real request always starts from a fresh user instance
        # too, so this isn't a product bug, just a test-object-identity artifact).
        fresh_user_a = User.objects.get(pk=self.user_a.pk)
        self.assertNotIn(
            'assets.view_asset',
            backend._effective_perms_for_tenant(fresh_user_a, self.tenant_a),
        )

    def test_context_tenant_deep_link_wins_over_active_tenant(self):
        """RoleForm owner resolution on create: the ``?tenant=`` deep-link context
        (passed as the ``tenant`` kwarg) wins over the ambient active tenant —
        opening the "add role" page from tenant B's detail view must never
        silently file the new role under whatever tenant happens to be active in
        the session."""
        set_current_tenant(self.tenant_a)
        try:
            form_data = {
                'name': 'Deep-Linked Role',
                'description': 'Created via ?tenant= deep link while Tenant A is active',
                'perm_asset_read': True,
            }
            form = TenantRoleForm(data=form_data, tenant=self.tenant_b, user=self.super_user)
            self.assertTrue(form.is_valid(), form.errors)
            role = form.save()
            self.assertEqual(role.tenant, self.tenant_b)
        finally:
            set_current_tenant(None)

    def test_no_tenant_context_falls_back_to_active_tenant(self):
        """With no ``?tenant=`` deep-link, the role is filed under the ambient
        active tenant."""
        set_current_tenant(self.tenant_a)
        try:
            form = TenantRoleForm(
                data={'name': 'Ambient Role', 'description': '', 'perm_asset_read': True},
                user=self.super_user,
            )
            self.assertTrue(form.is_valid(), form.errors)
            role = form.save()
            self.assertEqual(role.tenant, self.tenant_a)
        finally:
            set_current_tenant(None)

    def test_no_tenant_context_at_all_is_rejected(self):
        """No deep-link and no active tenant: the form refuses to guess and
        raises instead of filing the role somewhere arbitrary."""
        form = TenantRoleForm(
            data={'name': 'Homeless Role', 'description': '', 'perm_asset_read': True},
            user=self.super_user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('No tenant context', ' '.join(form.non_field_errors()))

    def test_generic_detail_view_permissions_enforced(self):
        from assets.models import Asset, StatusLabel
        from model_bakery import baker
        from django.urls import reverse

        # Create an asset in Tenant A
        status = baker.make(StatusLabel, type='deployable', name="Deployable")
        asset = Asset.objects.create(
            tenant=self.tenant_a,
            name="Confidential Asset",
            asset_tag="AST-999",
            status=status
        )

        # User A is a Reader (only has view_asset)
        reader_role = Role.objects.create(
            tenant=self.tenant_a,
            name="Reader",
            permissions=["assets.view_asset"]
        )
        grant(self.user_a, self.tenant_a, reader_role)

        # User B is a Software Manager (only has view_software, no view_asset)
        sw_role = Role.objects.create(
            tenant=self.tenant_a,
            name="SW Manager",
            permissions=["software.view_software"]
        )
        grant(self.user_b, self.tenant_a, sw_role)

        # Login User A (with view_asset permission)
        self.client.force_login(self.user_a)
        session = self.client.session
        session['active_tenant_id'] = self.tenant_a.pk
        session.save()

        # Access asset detail view (should succeed - status code 200)
        url = reverse('assets:asset_detail', kwargs={'pk': asset.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Login User B (without view_asset permission)
        self.client.force_login(self.user_b)
        session = self.client.session
        session['active_tenant_id'] = self.tenant_a.pk
        session.save()

        # Access asset detail view (should fail - status code 403 Forbidden)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_alert_action_permissions_enforced(self):
        from extras.models import AlertRule, AlertLog
        from model_bakery import baker
        from django.contrib.contenttypes.models import ContentType
        from django.urls import reverse

        rule = baker.make(AlertRule, tenant=self.tenant_a)
        ct = ContentType.objects.get_for_model(self.user_a)
        alert = AlertLog.objects.create(
            rule=rule,
            subject="Test Alert",
            message="Alert message",
            content_type=ct,
            object_id=self.user_a.pk,
            tenant=self.tenant_a,
            status=AlertLog.STATUS_ACTIVE
        )

        # User A has change_alertlog
        alert_admin_role = Role.objects.create(
            tenant=self.tenant_a,
            name="Alert Admin",
            permissions=["extras.change_alertlog"]
        )
        grant(self.user_a, self.tenant_a, alert_admin_role)

        # User B does not have change_alertlog
        reader_role = Role.objects.create(
            tenant=self.tenant_a,
            name="Reader",
            permissions=["assets.view_asset"]
        )
        grant(self.user_b, self.tenant_a, reader_role)

        # Login User A
        self.client.force_login(self.user_a)
        session = self.client.session
        session['active_tenant_id'] = self.tenant_a.pk
        session.save()

        url = reverse('extras:alertlog_acknowledge', kwargs={'pk': alert.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302) # Redirects on success
        alert.refresh_from_db()
        self.assertEqual(alert.status, AlertLog.STATUS_ACKNOWLEDGED)

        # Reset alert status
        alert.status = AlertLog.STATUS_ACTIVE
        alert.save()

        # Login User B (no permission)
        self.client.force_login(self.user_b)
        session = self.client.session
        session['active_tenant_id'] = self.tenant_a.pk
        session.save()

        response = self.client.post(url)
        self.assertEqual(response.status_code, 403) # Forbidden
        alert.refresh_from_db()
        self.assertEqual(alert.status, AlertLog.STATUS_ACTIVE) # Unchanged

    def test_report_views_permissions_enforced(self):
        from extras.models import ReportTemplate, ScheduledReport
        from model_bakery import baker
        from django.urls import reverse

        template = baker.make(ReportTemplate, tenant=self.tenant_a, name="Test Report Template")
        sched = ScheduledReport.objects.create(
            name="Monthly Report",
            tenant=self.tenant_a,
            report=template,
            frequency='monthly'
        )

        # User A has view_reporttemplate and view_scheduledreport
        report_admin_role = Role.objects.create(
            tenant=self.tenant_a,
            name="Report Admin",
            permissions=["extras.view_reporttemplate", "extras.view_scheduledreport"]
        )
        grant(self.user_a, self.tenant_a, report_admin_role)

        # User B does not have report permissions
        reader_role = Role.objects.create(
            tenant=self.tenant_a,
            name="Reader",
            permissions=["assets.view_asset"]
        )
        grant(self.user_b, self.tenant_a, reader_role)

        # Login User A
        self.client.force_login(self.user_a)
        session = self.client.session
        session['active_tenant_id'] = self.tenant_a.pk
        session.save()

        download_url = reverse('extras:reporttemplate_download', kwargs={'pk': template.pk})
        trigger_url = reverse('extras:scheduledreport_trigger', kwargs={'pk': sched.pk})

        response = self.client.get(download_url)
        self.assertEqual(response.status_code, 200) # Succeeds

        response = self.client.post(trigger_url)
        self.assertEqual(response.status_code, 302) # Redirect on success

        # Login User B (no permission)
        self.client.force_login(self.user_b)
        session = self.client.session
        session['active_tenant_id'] = self.tenant_a.pk
        session.save()

        response = self.client.get(download_url)
        self.assertEqual(response.status_code, 403) # Forbidden

        response = self.client.post(trigger_url)
        self.assertEqual(response.status_code, 403) # Forbidden

    def test_extras_perms_not_injected_as_strings(self):
        """Regression: permission strings must match real auth.Permission app_label.

        Builds roles from real auth.Permission rows (app_label='extras') rather than
        injecting literal strings — catches any future drift between model location and
        perm-string constants in views / MATRIX_MODELS.
        """
        from django.contrib.auth.models import Permission
        from django.urls import reverse

        MOVED_MODELS = [
            'alertrule', 'alertlog', 'notificationchannel',
            'reporttemplate', 'scheduledreport', 'exporttemplate',
            'webhookendpoint', 'eventrule', 'labeltemplate',
        ]

        # Assert every perm for these models lives under app_label='extras', not 'core'.
        for model_name in MOVED_MODELS:
            core_perms = Permission.objects.filter(
                content_type__app_label='core',
                content_type__model=model_name,
            )
            self.assertFalse(
                core_perms.exists(),
                f"Permission for {model_name} still has app_label='core' — model not fully moved to extras"
            )
            extras_perms = Permission.objects.filter(
                content_type__app_label='extras',
                content_type__model=model_name,
            )
            self.assertTrue(
                extras_perms.exists(),
                f"No extras.* permission found for {model_name}"
            )

        # Build perm strings from real Permission rows — no literal injection.
        change_alertlog_perm = Permission.objects.get(
            content_type__app_label='extras',
            content_type__model='alertlog',
            codename='change_alertlog',
        )
        change_alertlog_str = f"{change_alertlog_perm.content_type.app_label}.{change_alertlog_perm.codename}"

        view_reporttemplate_perm = Permission.objects.get(
            content_type__app_label='extras',
            content_type__model='reporttemplate',
            codename='view_reporttemplate',
        )
        view_reporttemplate_str = f"{view_reporttemplate_perm.content_type.app_label}.{view_reporttemplate_perm.codename}"

        # Create role with real-sourced perm strings and verify auth backend accepts them.
        role = Role.objects.create(
            tenant=self.tenant_a,
            name="Extras Regression Role",
            permissions=[change_alertlog_str, view_reporttemplate_str],
        )
        assignment = grant(self.user_a, self.tenant_a, role)
        set_current_tenant(self.tenant_a)
        set_current_membership(assignment.membership)
        self.assertTrue(
            self.user_a.has_perm(change_alertlog_str),
            f"user_a should have {change_alertlog_str} via TenantMembershipBackend",
        )
        self.assertTrue(
            self.user_a.has_perm(view_reporttemplate_str),
            f"user_a should have {view_reporttemplate_str} via TenantMembershipBackend",
        )

        # Verify role form initial values pre-check the right boxes for an edited role.
        form = TenantRoleForm(instance=role, tenant=self.tenant_a)
        self.assertTrue(
            form.fields['perm_alertlog_edit'].initial,
            "Role form should pre-check alertlog edit for a role carrying extras.change_alertlog",
        )
        self.assertTrue(
            form.fields['perm_reporttemplate_read'].initial,
            "Role form should pre-check reporttemplate read for a role carrying extras.view_reporttemplate",
        )

        # Verify form save round-trip: checking the matrix boxes generates extras.* strings.
        form_data = {
            'name': 'Extras Regression Role',
            'perm_alertlog_edit': True,
            'perm_reporttemplate_read': True,
        }
        form2 = TenantRoleForm(
            data=form_data,
            instance=role,
            tenant=self.tenant_a,
            user=self.super_user,
        )
        self.assertTrue(form2.is_valid(), f"Form errors: {form2.errors}")
        saved = form2.save()
        self.assertIn(change_alertlog_str, saved.permissions)
        self.assertIn(view_reporttemplate_str, saved.permissions)

        set_current_membership(None)
        set_current_tenant(None)


class SharedRoleTests(TestCase):
    """``shared_with_managed`` is the successor to the deleted ``is_default``
    role-cloning: a role owned by a managing tenant projects LIVE into its
    managed tenants instead of being copied on tenant creation. There is one
    definition, not N clones to drift out of sync.

    Covers the three properties that matter: the shared definition is
    assignable in a managed tenant (via an own-reach grant there), an edit at
    the owner propagates immediately (no clone to re-sync), and the definition
    itself is never editable from the managed tenant (editing is gated on
    holding permissions in the OWNING tenant, which a managed-tenant admin
    never has)."""

    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)

        self.super_user = User.objects.create_superuser(
            username='su-shared', email='su-shared@example.com', password='password123',
        )
        self.msp_tenant = Tenant.objects.create(
            name="Shared MSP", slug="shared-msp", is_provider=True,
        )
        self.customer_tenant = Tenant.objects.create(
            name="Shared Customer", slug="shared-customer", managed_by=self.msp_tenant,
        )
        self.shared_role = Role.objects.create(
            tenant=self.msp_tenant, name="MSP Technician",
            permissions=["assets.view_asset"], shared_with_managed=True,
        )
        self.private_role = Role.objects.create(
            tenant=self.msp_tenant, name="MSP Internal",
            permissions=["organization.delete_tenant"], shared_with_managed=False,
        )

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def test_role_picker_offers_shared_but_not_private_roles(self):
        # The role-picker query documented in RBAC_STAGE2_SPEC.md §7 (implemented
        # by MembershipForm): Q(tenant=T) | Q(tenant=T.managed_by, shared_with_managed=True).
        form = MembershipForm(user=self.super_user, tenant=self.customer_tenant)
        offered = set(form.fields['roles'].queryset)
        self.assertIn(self.shared_role, offered)
        self.assertNotIn(self.private_role, offered)

    def test_shared_role_is_assignable_in_managed_tenant(self):
        member = User.objects.create_user(username='cust-member', email='cust-member@example.com')
        form = MembershipForm(
            data={
                'user': member.pk, 'tenant': self.customer_tenant.pk,
                'roles': [self.shared_role.pk], 'reach': RoleAssignment.REACH_OWN,
                'is_active': True,
            },
            user=self.super_user, tenant=self.customer_tenant,
        )
        self.assertTrue(form.is_valid(), form.errors)
        membership = form.save()
        assignment = RoleAssignment.objects.get(
            membership=membership, role=self.shared_role, reach=RoleAssignment.REACH_OWN,
        )
        # It's the SAME role row, not a clone — still owned by the MSP tenant.
        self.assertEqual(assignment.role.tenant, self.msp_tenant)

        backend = MembershipBackend()
        self.assertIn(
            'assets.view_asset',
            backend._effective_perms_for_tenant(member, self.customer_tenant),
        )

    def test_private_role_is_not_assignable_in_managed_tenant(self):
        member = User.objects.create_user(username='cust-member2', email='cust-member2@example.com')
        # The private role isn't even offered by the picker (see
        # test_role_picker_offers_shared_but_not_private_roles above): its queryset
        # is `Q(tenant=T) | Q(tenant=T.managed_by, shared_with_managed=True)`, which
        # excludes an unshared MSP-owned role. Submitting its pk anyway is rejected
        # as an invalid choice on the `roles` field itself — the clean()-level
        # "not available in the selected tenant" guard is unreachable here since
        # the field-level rejection empties `cleaned_data['roles']` first.
        form = MembershipForm(
            data={
                'user': member.pk, 'tenant': self.customer_tenant.pk,
                'roles': [self.private_role.pk], 'reach': RoleAssignment.REACH_OWN,
                'is_active': True,
            },
            user=self.super_user, tenant=self.customer_tenant,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('roles', form.errors)
        self.assertFalse(
            RoleAssignment.objects.filter(role=self.private_role, membership__user=member).exists()
        )

    def test_shared_role_edit_propagates_live_not_a_clone(self):
        member = User.objects.create_user(username='cust-member3', email='cust-member3@example.com')
        grant(member, self.customer_tenant, self.shared_role)  # own-reach at the managed tenant

        backend = MembershipBackend()
        self.assertNotIn(
            'assets.change_asset',
            backend._effective_perms_for_tenant(member, self.customer_tenant),
        )

        # Edit the ONE shared definition at its owning (MSP) tenant...
        self.shared_role.permissions = list(self.shared_role.permissions) + ['assets.change_asset']
        self.shared_role.save()

        # ...and it shows up immediately for the customer-tenant member — no clone
        # to re-sync, because there never was one. The Role post_save cache-busting
        # signal clears the cache on a freshly DB-fetched user instance (it walks
        # `role.assignments`), not on this test's long-lived `member` handle, so
        # re-fetch to observe it cleanly (a real request always starts from a fresh
        # user instance too).
        fresh_member = User.objects.get(pk=member.pk)
        self.assertIn(
            'assets.change_asset',
            backend._effective_perms_for_tenant(fresh_member, self.customer_tenant),
        )

    def test_shared_role_not_editable_from_managed_tenant(self):
        # A customer-tenant admin who can change roles WITHIN their own tenant has
        # no membership/assignment whatsoever at the shared role's owning (MSP)
        # tenant, so has_perm(obj=shared_role) resolves the MSP tenant as context
        # and finds nothing there.
        admin_role = Role.objects.create(
            tenant=self.customer_tenant, name="Customer Admin",
            permissions=['organization.change_role', 'organization.view_role'],
        )
        customer_admin = User.objects.create_user(
            username='cust-admin', email='cust-admin@example.com',
        )
        grant(customer_admin, self.customer_tenant, admin_role)

        self.assertFalse(customer_admin.has_perm('organization.change_role', obj=self.shared_role))
        # The same permission DOES work on a role the admin actually owns.
        own_role = Role.objects.create(tenant=self.customer_tenant, name="Own Role", permissions=[])
        self.assertTrue(customer_admin.has_perm('organization.change_role', obj=own_role))


class RoleDeleteConfirmSharedWarningTests(TestCase):
    """Delete-confirm wording for a shared role with live assignments in a managed
    tenant (RBAC_STAGE3_SPEC.md §4): "grants survive as audit but stop resolving" —
    wording only, since Role.delete() (soft delete) + the auth backend's
    role.deleted_at check already implement that semantics as of stage 2. The
    warning fires only when BOTH hold: the role is shared_with_managed AND at
    least one RoleAssignment actually lives in one of its managed tenants."""

    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)

        self.superuser = User.objects.create_superuser(
            username='su-delete-warn', email='su-delete-warn@example.com', password='pw',
        )
        self.msp_tenant = Tenant.objects.create(
            name="Delete-Warn MSP", slug="delete-warn-msp", is_provider=True,
        )
        self.customer_tenant = Tenant.objects.create(
            name="Delete-Warn Customer", slug="delete-warn-customer", managed_by=self.msp_tenant,
        )
        self.client.force_login(self.superuser)

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def _delete_url(self, pk, tenant):
        from django.urls import reverse
        return f"{reverse('organization:role_delete', kwargs={'pk': pk})}?switch_tenant={tenant.pk}"

    def test_delete_confirm_warns_when_a_shared_role_has_a_managed_tenant_assignment(self):
        role = Role.objects.create(
            tenant=self.msp_tenant, name="Shared With Grants", permissions=[],
            shared_with_managed=True,
        )
        member = User.objects.create_user(username='cust-del-warn', email='cust-del-warn@example.com')
        grant(member, self.customer_tenant, role)  # own-reach grant AT the managed tenant

        resp = self.client.get(self._delete_url(role.pk, self.msp_tenant))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "survive as an audit trail")

    def test_delete_confirm_silent_when_a_shared_role_has_no_managed_tenant_assignments(self):
        role = Role.objects.create(
            tenant=self.msp_tenant, name="Shared No Grants", permissions=[],
            shared_with_managed=True,
        )
        resp = self.client.get(self._delete_url(role.pk, self.msp_tenant))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "survive as an audit trail")

    def test_delete_confirm_silent_for_a_non_shared_role_even_with_assignments(self):
        # Assignments at the OWNING tenant itself (not a managed one) never
        # trigger the warning, and neither does shared_with_managed=False.
        role = Role.objects.create(
            tenant=self.msp_tenant, name="Private With Grants", permissions=[],
            shared_with_managed=False,
        )
        staff = User.objects.create_user(username='staff-del-warn', email='staff-del-warn@example.com')
        grant(staff, self.msp_tenant, role)

        resp = self.client.get(self._delete_url(role.pk, self.msp_tenant))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "survive as an audit trail")


class RBACCoverageTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)

        self.super_user = User.objects.create_superuser(
            username='superuser', email='super@example.com', password='password123'
        )
        self.msp_tenant, _ = Tenant.objects.get_or_create(
            slug="msp-provider-coverage",
            defaults={"name": "MSP Provider Coverage", "is_provider": True},
        )
        self.tenant_a, _ = Tenant.objects.get_or_create(
            slug="tenant-a-coverage",
            defaults={"name": "Tenant A Coverage", "managed_by": self.msp_tenant},
        )

    def test_tenant_group_hierarchy_and_cycle(self):
        # M7: Test get_descendant_tenant_group_ids with hierarchy and cycle
        from organization.models import TenantGroup
        from organization.access import get_descendant_tenant_group_ids

        g1 = TenantGroup.objects.create(name="Group 1", slug="g1")
        g2 = TenantGroup.objects.create(name="Group 2", slug="g2", parent=g1)
        g3 = TenantGroup.objects.create(name="Group 3", slug="g3", parent=g2)

        # Verify hierarchy walk
        descendants = get_descendant_tenant_group_ids(g1.pk)
        self.assertEqual(descendants, {g1.pk, g2.pk, g3.pk})

        # Introduce a cycle manually (parent=g3 for g1)
        TenantGroup.objects.filter(pk=g1.pk).update(parent=g3)

        # Verify it doesn't infinite loop and returns the cycle set
        descendants_cycle = get_descendant_tenant_group_ids(g1.pk)
        self.assertEqual(descendants_cycle, {g1.pk, g2.pk, g3.pk})

    def test_managed_scope_tenant_group_walks_descendants(self):
        # M7 successor: RoleAssignment.covers_tenant is now the single source of
        # truth for managed-scope resolution (it replaces the deleted
        # MembershipBackend._tenant_in_scope).
        from organization.models import TenantGroup

        g1 = TenantGroup.objects.create(name="Group 1", slug="g1-cov")
        g2 = TenantGroup.objects.create(name="Group 2", slug="g2-cov", parent=g1)

        tenant_in_g2 = Tenant.objects.create(
            name="Tenant in G2", slug="t-g2-cov", group=g2, managed_by=self.msp_tenant,
        )
        tenant_sibling = Tenant.objects.create(
            name="Tenant Sibling", slug="t-sib-cov", managed_by=self.msp_tenant,
        )

        user = User.objects.create_user(username='tech', email='tech@example.com')
        role = Role.objects.create(tenant=self.msp_tenant, name="Group-Scoped Tech", permissions=[])
        membership = Membership.objects.create(user=user, tenant=self.msp_tenant, is_active=True)
        assignment = RoleAssignment.objects.create(
            membership=membership, role=role,
            reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_TENANT_GROUP, scope_group=g1,
        )

        self.assertTrue(assignment.covers_tenant(tenant_in_g2))
        self.assertFalse(assignment.covers_tenant(tenant_sibling))

    def test_cache_invalidation_signals_on_assignment_create_delete(self):
        # M9 successor: cache-invalidation is now keyed off RoleAssignment
        # post_save/post_delete (organization/signals.py), not the deleted
        # Membership.roles M2M.
        backend = MembershipBackend()

        user = User.objects.create_user(username='memberuser', email='member@example.com')
        role = Role.objects.create(
            tenant=self.tenant_a, name="Viewer", permissions=["assets.view_asset"],
        )
        membership = Membership.objects.create(user=user, tenant=self.tenant_a, is_active=True)

        self.assertNotIn("assets.view_asset", backend._effective_perms_for_tenant(user, self.tenant_a))

        # Create an assignment (post_save signal busts the cache).
        assignment = RoleAssignment.objects.create(
            membership=membership, role=role, reach=RoleAssignment.REACH_OWN,
        )
        self.assertIn("assets.view_asset", backend._effective_perms_for_tenant(user, self.tenant_a))

        # Delete it (post_delete signal busts the cache).
        assignment.delete()
        self.assertNotIn("assets.view_asset", backend._effective_perms_for_tenant(user, self.tenant_a))

    def test_technician_quick_route_redirects_to_unified_preset(self):
        # Stage 3 deletes TechnicianQuickForm. The compatibility route is now a
        # GET-only redirect into the unified, guarded membership form.
        self.client.force_login(self.super_user)

        response = self.client.get(reverse('organization:technician_quick_add'))

        self.assertRedirects(
            response,
            reverse('organization:membership_create')
            + f'?preset=technician&tenant={self.msp_tenant.pk}',
            fetch_redirect_response=False,
        )
