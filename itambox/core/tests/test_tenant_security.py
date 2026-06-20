import json
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.urls import reverse
from organization.models import TenantGroup, Tenant, TenantMembership, TenantRole, Site, Location
from assets.models import StatusLabel, Asset, AssetRole, Manufacturer, AssetType
from core.managers import set_current_tenant, set_current_membership

User = get_user_model()

class CoreTenantSecurityTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username='testuser', password='password')
        self.superuser = User.objects.create_superuser(username='admin', password='password')
        self.tenant_group = TenantGroup.objects.create(name='Global Group', slug='global-group')
        self.tenant = Tenant.objects.create(name='Test Tenant', slug='test-tenant', group=self.tenant_group)
        self.site = Site.objects.create(name='Test Site', slug='test-site')
        self.location = Location.objects.create(name='Test Location', slug='test-location', tenant=self.tenant, site=self.site)
        self.role = TenantRole.objects.create(
            tenant=self.tenant,
            name='Admin',
            permissions=[
                'assets.view_asset', 'assets.add_asset', 'assets.change_asset', 'assets.delete_asset'
            ]
        )
        self.membership = TenantMembership.objects.create(user=self.user, tenant=self.tenant, role=self.role)

    def test_tenant_group_scoping(self):
        from core.managers import set_current_tenant_group
        from itambox.middleware import _current_user
        
        # Superuser scoping
        _current_user.set(self.superuser)
        set_current_tenant_group(self.tenant_group)
        self.assertEqual(Tenant.objects.count(), 1)
        self.assertEqual(Location.objects.count(), 1)

        # Standard user scoping
        _current_user.set(self.user)
        self.assertEqual(Tenant.objects.count(), 1)
        self.assertEqual(Location.objects.count(), 1)

        # Anonymous scoping
        _current_user.set(None)
        self.assertEqual(Tenant.objects.count(), 1)
        self.assertEqual(Location.objects.count(), 1)

        # Cleanup
        set_current_tenant_group(None)

    def test_tenant_group_membership_isolation(self):
        """Test that a user cannot edit an asset of a tenant where they are reader, even if switched to an admin tenant."""
        # 1. Create TenantGroup and two tenants in the same group
        group = TenantGroup.objects.create(name='Test Group 2', slug='test-group-2')
        tenant_admin = Tenant.objects.create(name='Admin Tenant', slug='admin-tenant', group=group)
        tenant_readonly = Tenant.objects.create(name='Readonly Tenant', slug='readonly-tenant', group=group)
        
        # 2. Create status & role
        status = StatusLabel.objects.create(name='Test Active', slug='test-active', type='deployable')
        role = AssetRole.objects.create(name='Test Role', slug='test-role')
        
        mfr = Manufacturer.objects.create(name='Dell', slug='dell')
        asset_type = AssetType.objects.create(manufacturer=mfr, model='Latitude 5550')
        
        # 3. Create asset belonging to the readonly tenant
        asset_readonly = Asset.objects.create(
            name='Protected Desktop',
            asset_tag='TAG-PROT',
            status=status,
            asset_role=role,
            tenant=tenant_readonly
        )
        
        # Create a non-superuser user
        test_user = User.objects.create_user(username='tenant_test_user', password='password123', is_superuser=False)
        
        # 4. Bind memberships
        admin_role = TenantRole.objects.create(
            tenant=tenant_admin,
            name='Admin',
            permissions=[
                'assets.view_asset', 'assets.add_asset', 'assets.change_asset', 'assets.delete_asset'
            ]
        )
        reader_role = TenantRole.objects.create(
            tenant=tenant_readonly,
            name='Reader',
            permissions=[
                'assets.view_asset'
            ]
        )
        TenantMembership.objects.create(user=test_user, tenant=tenant_admin, role=admin_role)
        TenantMembership.objects.create(user=test_user, tenant=tenant_readonly, role=reader_role)
        
        # Set active context in test client session
        self.client.force_login(test_user)
        session = self.client.session
        session['active_tenant_id'] = tenant_admin.pk
        session.save()
        
        # 5. Set active context to the ADMIN tenant
        from core.managers import set_current_tenant, set_current_membership
        membership_admin = TenantMembership.objects.get(user=test_user, tenant=tenant_admin)
        set_current_tenant(tenant_admin)
        set_current_membership(membership_admin)
        
        # 6. Verify that the user has general 'change_asset' permission (under active context)
        self.assertTrue(test_user.has_perm('assets.change_asset'))
        
        # 7. BUT verify that the user CANNOT edit the specific asset of the READONLY tenant!
        self.assertFalse(test_user.has_perm('assets.change_asset', obj=asset_readonly))
        
        # 8. Test that GET/POST requests are blocked (scoped out, resulting in 404 Not Found) for the readonly tenant asset
        
        # Update GET
        url_update = reverse('assets:asset_update', kwargs={'pk': asset_readonly.pk})
        response = self.client.get(url_update)
        self.assertEqual(response.status_code, 404)
        
        # Delete GET
        url_delete = reverse('assets:asset_delete', kwargs={'pk': asset_readonly.pk})
        response = self.client.get(url_delete)
        self.assertEqual(response.status_code, 404)
        
        # Clone GET
        url_clone = reverse('assets:asset_clone', kwargs={'pk': asset_readonly.pk})
        response = self.client.get(url_clone)
        self.assertEqual(response.status_code, 404)
        
        # Checkout GET (modal)
        url_checkout = reverse('assets:asset_checkout_modal', kwargs={'pk': asset_readonly.pk})
        response = self.client.get(url_checkout)
        self.assertEqual(response.status_code, 404)
        
        # Checkin POST
        url_checkin = reverse('assets:asset_checkin', kwargs={'pk': asset_readonly.pk})
        response = self.client.post(url_checkin)
        self.assertEqual(response.status_code, 404)
        
        # 9. Test that creating an asset and assigning it to the readonly tenant is blocked by form validation
        url_create = reverse('assets:asset_create')
        post_data = {
            'name': 'Illegally Assigned Laptop',
            'asset_tag': 'TAG-ILLEGAL',
            'status': status.pk,
            'asset_type': asset_type.pk,
            'asset_role': role.pk,
            'tenant': tenant_readonly.pk,
        }
        response = self.client.post(url_create, data=post_data)
        # The owning-tenant picker is scoped to the user's accessible tenants, so a
        # crafted tenant_readonly value is never accepted. For a member whose active
        # context resolves to a single tenant the field is hidden/auto-set (the POST
        # value is ignored and the asset lands in the active tenant); a member with
        # several accessible tenants instead gets a "not a valid choice" form error.
        # Either way the asset is NEVER created in the readonly tenant.
        self.assertIn(response.status_code, (200, 302))
        # _base_manager (unscoped): all_objects is itself tenant-scoped and would
        # hide a cross-tenant leak under the active context.
        self.assertFalse(
            Asset._base_manager.filter(asset_tag='TAG-ILLEGAL', tenant=tenant_readonly).exists()
        )
        if response.status_code == 302:
            created = Asset._base_manager.filter(asset_tag='TAG-ILLEGAL').first()
            self.assertIsNotNone(created)
            self.assertEqual(created.tenant_id, tenant_admin.pk)
        
        # Cleanup context
        set_current_tenant(None)
        set_current_membership(None)


class RecycleBinTenantScopingTestCase(TestCase):
    """Regression tests: ``all_objects`` on tenant-scoped models must itself be
    tenant-scoped, otherwise the Recycle Bin (which queries all_objects) leaks
    soft-deleted objects across tenants."""

    def setUp(self):
        self.tenant_a = Tenant.objects.create(name='Tenant A', slug='rb-tenant-a')
        self.tenant_b = Tenant.objects.create(name='Tenant B', slug='rb-tenant-b')
        self.status = StatusLabel.objects.create(name='RB Active', slug='rb-active', type='deployable')
        self.asset_a = Asset.objects.create(
            name='Asset A', asset_tag='RB-A-001', status=self.status, tenant=self.tenant_a
        )
        self.asset_b = Asset.objects.create(
            name='Asset B', asset_tag='RB-B-001', status=self.status, tenant=self.tenant_b
        )
        self.asset_a.delete()  # soft delete
        self.asset_b.delete()  # soft delete

    def test_all_objects_is_tenant_scoped(self):
        from core.managers import set_current_tenant
        set_current_tenant(self.tenant_a)
        try:
            visible = set(Asset.all_objects.values_list('pk', flat=True))
            self.assertIn(self.asset_a.pk, visible)
            self.assertNotIn(
                self.asset_b.pk, visible,
                "Asset.all_objects leaked another tenant's (soft-deleted) asset",
            )
        finally:
            set_current_tenant(None)

    def test_all_objects_includes_soft_deleted_rows(self):
        from core.managers import set_current_tenant
        set_current_tenant(self.tenant_a)
        try:
            self.assertFalse(Asset.objects.filter(pk=self.asset_a.pk).exists())
            self.assertTrue(Asset.all_objects.filter(pk=self.asset_a.pk).exists())
        finally:
            set_current_tenant(None)

    def test_tenant_scoped_models_have_tenant_scoped_all_objects(self):
        """Every model with a tenant FK and an all_objects manager must expose
        filter_by_tenant on its queryset (the contract the Recycle Bin relies on)."""
        from django.apps import apps
        offenders = []
        for model in apps.get_models():
            if not any(f.name == 'tenant' for f in model._meta.fields):
                continue
            manager = getattr(model, 'all_objects', None)
            if manager is None:
                continue
            if not hasattr(manager.get_queryset(), 'filter_by_tenant'):
                offenders.append(model._meta.label)
        self.assertEqual(
            offenders, [],
            f"Models with unscoped all_objects managers: {offenders}",
        )


class CrossTenantAttackTestCase(TestCase):
    """Authenticated user in Tenant B must not be able to read or mutate Tenant A objects
    via pk/id manipulation across all endpoint families (plan families 1-10)."""

    def setUp(self):
        self.tenant_a = Tenant.objects.create(name='CT-Tenant-A', slug='ct-tenant-a')
        self.tenant_b = Tenant.objects.create(name='CT-Tenant-B', slug='ct-tenant-b')

        self.status = StatusLabel.objects.create(name='CT-Active', slug='ct-active', type='deployable')
        self.mfr = Manufacturer.objects.create(name='CT-Mfr', slug='ct-mfr')
        self.asset_type = AssetType.objects.create(manufacturer=self.mfr, model='CT-Model')

        # Tenant A's asset — the object we try to attack from Tenant B
        self.asset_a = Asset.objects.create(
            name='CT-Asset-A', asset_tag='CT-A-001',
            status=self.status, asset_type=self.asset_type, tenant=self.tenant_a,
        )

        # User with membership ONLY in Tenant B
        self.user_b = User.objects.create_user(username='ct_user_b', password='pass123')
        self.role_b = TenantRole.objects.create(
            tenant=self.tenant_b,
            name='CT-Admin-B',
            permissions=[
                'assets.view_asset', 'assets.add_asset', 'assets.change_asset', 'assets.delete_asset',
                'core.change_recyclebin', 'core.delete_recyclebin',
            ]
        )
        self.membership_b = TenantMembership.objects.create(
            user=self.user_b, tenant=self.tenant_b, role=self.role_b
        )

        # Login as user_b with active tenant = Tenant B
        self.client.force_login(self.user_b)
        session = self.client.session
        session['active_tenant_id'] = self.tenant_b.pk
        session.save()

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    # -------------------------------------------------------------------------
    # Family 1: Generic UI views (detail / edit / delete / clone)
    # -------------------------------------------------------------------------

    def test_ui_detail_cross_tenant_404(self):
        url = reverse('assets:asset_detail', kwargs={'pk': self.asset_a.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_ui_edit_cross_tenant_404(self):
        url = reverse('assets:asset_update', kwargs={'pk': self.asset_a.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_ui_delete_cross_tenant_404(self):
        url = reverse('assets:asset_delete', kwargs={'pk': self.asset_a.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_ui_clone_cross_tenant_404(self):
        url = reverse('assets:asset_clone', kwargs={'pk': self.asset_a.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    # -------------------------------------------------------------------------
    # Family 2: Bulk edit / delete
    # -------------------------------------------------------------------------

    def test_bulk_delete_cross_tenant_pks_are_dropped(self):
        """POSTing Tenant A pks to the generic bulk-delete endpoint must not delete them."""
        url = reverse('bulk_delete')
        response = self.client.post(url, {
            'pk': [str(self.asset_a.pk)],
            'model_name': 'assets.asset',
            '_confirm': '1',
        })
        # Asset A must still exist (not deleted)
        self.assertTrue(Asset.all_objects.filter(pk=self.asset_a.pk).exists())

    def test_bulk_edit_cross_tenant_pks_are_not_modified(self):
        """POSTing Tenant A pks to bulk-edit must not change them."""
        original_name = self.asset_a.name
        url = reverse('bulk_edit')
        self.client.post(url, {
            'pk': [str(self.asset_a.pk)],
            'model_name': 'assets.asset',
            '_selected_fields': ['name'],
            'name': 'HACKED',
            '_apply': '1',
        })
        self.asset_a.refresh_from_db()
        self.assertEqual(self.asset_a.name, original_name)

    # -------------------------------------------------------------------------
    # Family 3: Export endpoints
    # -------------------------------------------------------------------------

    def test_export_cross_tenant_pks_returns_empty(self):
        url = reverse('object_export', kwargs={
            'app_label': 'assets', 'model_name': 'asset', 'template_id': 0
        })
        response = self.client.get(url + f'?pk={self.asset_a.pk}')
        self.assertEqual(response.status_code, 200)
        # The CSV must contain only the header row; the Tenant A asset must not appear
        content = response.content.decode()
        self.assertNotIn('CT-A-001', content)

    # -------------------------------------------------------------------------
    # Family 4: Attachments and Journal entries (GenericFK endpoints)
    # -------------------------------------------------------------------------

    def test_journal_entry_cross_tenant_object_is_404(self):
        url = reverse('journal_entry_add', kwargs={
            'app_label': 'assets', 'model_name': 'asset', 'object_id': self.asset_a.pk
        })
        response = self.client.post(url, {'comment': 'injected'})
        self.assertEqual(response.status_code, 404)

    def test_image_attachment_upload_cross_tenant_is_404(self):
        from io import BytesIO
        from django.core.files.uploadedfile import SimpleUploadedFile
        url = reverse('image_attachment_upload', kwargs={
            'app_label': 'assets', 'model_name': 'asset', 'object_id': self.asset_a.pk
        })
        fake_image = SimpleUploadedFile('test.png', b'\x89PNG\r\n', content_type='image/png')
        response = self.client.post(url, {'image': fake_image})
        self.assertEqual(response.status_code, 404)

    def test_image_attachment_delete_cross_tenant_is_404(self):
        from extras.models import ImageAttachment
        obj_ct = ContentType.objects.get_for_model(Asset)
        attachment = ImageAttachment.objects.create(
            model=obj_ct, object_id=self.asset_a.pk, name='x.png', image='test/x.png'
        )
        url = reverse('image_attachment_delete', kwargs={'pk': attachment.pk})
        response = self.client.post(url, {'return_url': '/'})
        self.assertEqual(response.status_code, 404)
        # Attachment must still exist
        self.assertTrue(ImageAttachment.objects.filter(pk=attachment.pk).exists())
        attachment.delete()

    def test_file_attachment_delete_cross_tenant_is_404(self):
        from extras.models import FileAttachment
        obj_ct = ContentType.objects.get_for_model(Asset)
        attachment = FileAttachment.objects.create(
            model=obj_ct, object_id=self.asset_a.pk, name='doc.pdf', file='test/doc.pdf',
        )
        url = reverse('file_attachment_delete', kwargs={'pk': attachment.pk})
        response = self.client.post(url, {'return_url': '/'})
        self.assertEqual(response.status_code, 404)
        self.assertTrue(FileAttachment.objects.filter(pk=attachment.pk).exists())
        attachment.delete()

    # -------------------------------------------------------------------------
    # Family 5: Service/action endpoints (checkout / checkin)
    # -------------------------------------------------------------------------

    def test_checkout_modal_cross_tenant_404(self):
        url = reverse('assets:asset_checkout_modal', kwargs={'pk': self.asset_a.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_checkin_cross_tenant_404(self):
        url = reverse('assets:asset_checkin', kwargs={'pk': self.asset_a.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)

    # -------------------------------------------------------------------------
    # Family 6: REST API (session auth; TenantMiddleware resolves tenant from session)
    # -------------------------------------------------------------------------

    def test_api_list_queryset_does_not_include_tenant_a_asset(self):
        # The API list view calls Asset.objects.all() which uses TenantScopingSoftDeleteManager.
        # Prove the manager filters by active tenant so Tenant B context never returns Tenant A assets.
        set_current_tenant(self.tenant_b)
        set_current_membership(self.membership_b)
        try:
            pks = list(Asset.objects.values_list('pk', flat=True))
            self.assertNotIn(self.asset_a.pk, pks,
                "TenantScopingSoftDeleteManager leaked Tenant A's asset into Tenant B context")
        finally:
            set_current_tenant(None)
            set_current_membership(None)

    def test_api_detail_cross_tenant_404(self):
        url = reverse('api:assets_api:asset-detail', kwargs={'pk': self.asset_a.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_api_patch_cross_tenant_404(self):
        url = reverse('api:assets_api:asset-detail', kwargs={'pk': self.asset_a.pk})
        response = self.client.patch(
            url,
            data=json.dumps({'name': 'HACKED'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)
        self.asset_a.refresh_from_db()
        self.assertEqual(self.asset_a.name, 'CT-Asset-A')

    def test_api_delete_cross_tenant_404(self):
        url = reverse('api:assets_api:asset-detail', kwargs={'pk': self.asset_a.pk})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Asset.all_objects.filter(pk=self.asset_a.pk).exists())

    # -------------------------------------------------------------------------
    # Family 9: Recycle bin restore / purge
    # -------------------------------------------------------------------------

    def test_recycle_bin_restore_cross_tenant_blocked(self):
        # Soft-delete Tenant A asset and try to restore it as Tenant B user
        self.asset_a.delete()
        ct = ContentType.objects.get_for_model(Asset)
        url = reverse('object_restore', kwargs={
            'content_type_id': ct.pk, 'object_id': self.asset_a.pk
        })
        response = self.client.post(url)
        # Expect 403 (PermissionDenied from has_permission) or 404
        self.assertIn(response.status_code, (403, 404))
        # Object must still be soft-deleted (not restored)
        self.assertFalse(Asset.objects.filter(pk=self.asset_a.pk).exists())

    def test_recycle_bin_purge_cross_tenant_blocked(self):
        self.asset_a.delete()
        ct = ContentType.objects.get_for_model(Asset)
        url = reverse('object_purge', kwargs={
            'content_type_id': ct.pk, 'object_id': self.asset_a.pk
        })
        response = self.client.post(url)
        self.assertIn(response.status_code, (403, 404))
        # Object must still exist (not purged)
        self.assertTrue(Asset.all_objects.filter(pk=self.asset_a.pk).exists())


class EmailSettingsEncryptionTestCase(TestCase):
    """smtp_password must be encrypted at rest; mail send receives the plaintext."""

    def test_save_encrypts_plaintext_password(self):
        from core.models import EmailSettings
        obj = EmailSettings(smtp_host='localhost', smtp_port=587, smtp_password='s3cr3t')
        obj.save()
        db_obj = EmailSettings.objects.get(pk=1)
        self.assertTrue(
            db_obj.smtp_password.startswith('enc$'),
            "smtp_password was not encrypted on save",
        )
        self.assertNotEqual(db_obj.smtp_password, 's3cr3t')

    def test_decrypted_property_returns_plaintext(self):
        from core.models import EmailSettings
        obj = EmailSettings(smtp_host='localhost', smtp_port=587, smtp_password='s3cr3t')
        obj.save()
        db_obj = EmailSettings.objects.get(pk=1)
        self.assertEqual(db_obj.smtp_password_decrypted, 's3cr3t')

    def test_save_is_idempotent_for_already_encrypted(self):
        from core.models import EmailSettings
        obj = EmailSettings(smtp_host='localhost', smtp_port=587, smtp_password='s3cr3t')
        obj.save()
        first_cipher = EmailSettings.objects.get(pk=1).smtp_password
        # Save again without changing the password
        obj2 = EmailSettings.objects.get(pk=1)
        obj2.save()
        second_cipher = EmailSettings.objects.get(pk=1).smtp_password
        self.assertEqual(first_cipher, second_cipher, "Re-save should not double-encrypt")

    def test_empty_password_stays_empty(self):
        from core.models import EmailSettings
        obj = EmailSettings(smtp_host='localhost', smtp_port=587, smtp_password='')
        obj.save()
        db_obj = EmailSettings.objects.get(pk=1)
        self.assertFalse(db_obj.smtp_password)
        self.assertEqual(db_obj.smtp_password_decrypted, '')
