from django.test import TestCase
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from organization.models import Tenant, TenantMembership, TenantRole
from organization.forms import TenantRoleForm
from core.managers import set_current_tenant, set_current_membership

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
        # Create role in Tenant A
        role_a = TenantRole.objects.create(
            tenant=self.tenant_a,
            name="Alpha Admin",
            permissions=["assets.view_asset"]
        )
        
        # Scoped to Tenant A
        set_current_tenant(self.tenant_a)
        self.assertIn(role_a, TenantRole.objects.all())
        
        # Scoped to Tenant B (should be invisible)
        set_current_tenant(self.tenant_b)
        self.assertNotIn(role_a, TenantRole.objects.all())
        
        # Reset context
        set_current_tenant(None)

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
        role = TenantRole.objects.create(
            tenant=self.tenant_a,
            name="ReadOnly Member",
            permissions=["assets.view_asset", "extras.view_dashboard"]
        )
        membership = TenantMembership.objects.create(
            user=self.user_a,
            tenant=self.tenant_a,
        )
        membership.roles.add(role)

        set_current_membership(membership)
        set_current_tenant(self.tenant_a)
        
        self.assertTrue(self.user_a.has_perm('assets.view_asset'))
        self.assertFalse(self.user_a.has_perm('assets.add_asset'))
        self.assertFalse(self.user_a.has_perm('assets.delete_asset'))
        
        set_current_membership(None)
        set_current_tenant(None)

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
        membership = TenantMembership.objects.create(
            user=self.user_a,
            tenant=self.tenant_a,
        )
        membership.roles.add(role)
        set_current_membership(membership)
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
        reader_role = TenantRole.objects.create(
            tenant=self.tenant_a,
            name="Reader",
            permissions=["assets.view_asset", "extras.view_dashboard"]
        )
        membership_a = TenantMembership.objects.create(
            user=self.user_a,
            tenant=self.tenant_a,
        )
        membership_a.roles.add(reader_role)

        set_current_membership(membership_a)
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
        from organization.forms.tenantrole_form import MATRIX_MODELS

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

    def test_role_deletion_clears_membership_m2m(self):
        """Deleting a TenantRole removes it from the M2M join table.

        With roles as a ManyToManyField on TenantMembership, there is no FK PROTECT
        constraint — deleting (or soft-deleting) a role simply removes the M2M rows so
        the membership continues to exist but no longer carries that role's permissions.
        """
        role = TenantRole.objects.create(
            tenant=self.tenant_a,
            name="Deletable?",
            permissions=["assets.view_asset"]
        )
        membership = TenantMembership.objects.create(
            user=self.user_a,
            tenant=self.tenant_a,
        )
        membership.roles.add(role)

        self.assertIn(role, membership.roles.all())

        # Deleting the role must not raise — M2M rows are cascaded by the DB.
        role.delete()

        membership.refresh_from_db()
        # Role is gone from the membership's effective role set.
        self.assertNotIn(role, membership.roles.all())
        # The membership itself still exists.
        self.assertTrue(TenantMembership.objects.filter(pk=membership.pk).exists())

    def test_global_mode_tenant_selection(self):
        # In global mode (no tenant in kwargs), tenant is selected in form fields
        form_data = {
            'name': 'Global Role',
            'tenant': self.tenant_b.pk,
            'description': 'Created in global mode',
            'perm_asset_read': True,
        }
        form = TenantRoleForm(data=form_data, user=self.super_user)
        self.assertTrue(form.is_valid(), form.errors)
        role = form.save()
        self.assertEqual(role.tenant, self.tenant_b)

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
        reader_role = TenantRole.objects.create(
            tenant=self.tenant_a,
            name="Reader",
            permissions=["assets.view_asset"]
        )
        membership_a = TenantMembership.objects.create(
            user=self.user_a,
            tenant=self.tenant_a,
        )
        membership_a.roles.add(reader_role)

        # User B is a Software Manager (only has view_software, no view_asset)
        sw_role = TenantRole.objects.create(
            tenant=self.tenant_a,
            name="SW Manager",
            permissions=["software.view_software"]
        )
        membership_b = TenantMembership.objects.create(
            user=self.user_b,
            tenant=self.tenant_a,
        )
        membership_b.roles.add(sw_role)
        
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
        alert_admin_role = TenantRole.objects.create(
            tenant=self.tenant_a,
            name="Alert Admin",
            permissions=["extras.change_alertlog"]
        )
        membership_a = TenantMembership.objects.create(
            user=self.user_a,
            tenant=self.tenant_a,
        )
        membership_a.roles.add(alert_admin_role)

        # User B does not have change_alertlog
        reader_role = TenantRole.objects.create(
            tenant=self.tenant_a,
            name="Reader",
            permissions=["assets.view_asset"]
        )
        membership_b = TenantMembership.objects.create(
            user=self.user_b,
            tenant=self.tenant_a,
        )
        membership_b.roles.add(reader_role)
        
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
        report_admin_role = TenantRole.objects.create(
            tenant=self.tenant_a,
            name="Report Admin",
            permissions=["extras.view_reporttemplate", "extras.view_scheduledreport"]
        )
        membership_a = TenantMembership.objects.create(
            user=self.user_a,
            tenant=self.tenant_a,
        )
        membership_a.roles.add(report_admin_role)

        # User B does not have report permissions
        reader_role = TenantRole.objects.create(
            tenant=self.tenant_a,
            name="Reader",
            permissions=["assets.view_asset"]
        )
        membership_b = TenantMembership.objects.create(
            user=self.user_b,
            tenant=self.tenant_a,
        )
        membership_b.roles.add(reader_role)
        
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
        role = TenantRole.objects.create(
            tenant=self.tenant_a,
            name="Extras Regression Role",
            permissions=[change_alertlog_str, view_reporttemplate_str],
        )
        membership = TenantMembership.objects.create(
            user=self.user_a,
            tenant=self.tenant_a,
        )
        membership.roles.add(role)
        set_current_tenant(self.tenant_a)
        set_current_membership(membership)
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
