from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from assets.models import Asset, AssetType, StatusLabel, Manufacturer
from organization.models import Tenant, TenantRole, TenantMembership
from core.models import Job
from extras.models import JournalEntry, Tag

User = get_user_model()


class JournalEntryTenantIsolationAPITests(APITestCase):
    """JournalEntry REST must be tenant-scoped: a member of one tenant cannot
    read, retrieve, create, update, or delete journal entries belonging to
    another tenant. The denormalised ``tenant`` is derived from the object."""

    def setUp(self):
        self.tenant_a = Tenant.objects.create(name='JE Tenant A', slug='je-tenant-a')
        self.tenant_b = Tenant.objects.create(name='JE Tenant B', slug='je-tenant-b')

        self.superuser = User.objects.create_user(
            username='je_super', password='pw', is_staff=True, is_superuser=True
        )
        perms = [
            'extras.view_journalentry', 'extras.add_journalentry',
            'extras.change_journalentry', 'extras.delete_journalentry',
        ]
        role_a = TenantRole.objects.create(tenant=self.tenant_a, name='JE Role A', permissions=perms)
        self.staff_a = User.objects.create_user(username='je_staff_a', password='pw')
        TenantMembership.objects.create(user=self.staff_a, tenant=self.tenant_a, role=role_a)
        # A second tenant-A member with change rights, to prove edits do not
        # reassign authorship.
        self.staff_a2 = User.objects.create_user(username='je_staff_a2', password='pw')
        TenantMembership.objects.create(user=self.staff_a2, tenant=self.tenant_a, role=role_a)

        self.mfr = Manufacturer.objects.create(name='JE-Mfr', slug='je-mfr')
        self.atype = AssetType.objects.create(manufacturer=self.mfr, model='JE-Model')
        self.status = StatusLabel.objects.create(
            name='JE-Ready', slug='je-ready', type=StatusLabel.TYPE_DEPLOYABLE
        )

        self.asset_a = Asset.objects.create(
            name='JE Asset A', asset_tag='JE-A-1', asset_type=self.atype,
            status=self.status, tenant=self.tenant_a,
        )
        self.asset_b = Asset.objects.create(
            name='JE Asset B', asset_tag='JE-B-1', asset_type=self.atype,
            status=self.status, tenant=self.tenant_b,
        )
        self.ct_asset = ContentType.objects.get_for_model(Asset)
        # ContentTypeField (de)serialises as '<app_label>.<model>', not a pk.
        self.asset_ct_str = f'{self.ct_asset.app_label}.{self.ct_asset.model}'

        # A tenant-B Job: a TENANT-OWNED model whose default manager is NOT
        # tenant-scoping (plain Manager). The create guard must still reject it.
        self.job_b = Job.objects.create(name='JE Job B', tenant=self.tenant_b)

        # One entry per tenant; tenant is derived from the asset on save().
        self.entry_a = JournalEntry.objects.create(
            content_object=self.asset_a, user=self.staff_a, comment='A note',
        )
        self.entry_b = JournalEntry.objects.create(
            content_object=self.asset_b, user=self.superuser, comment='B note',
        )

    def _ids(self, resp):
        data = resp.data
        rows = data['results'] if isinstance(data, dict) and 'results' in data else data
        return {row['id'] for row in rows}

    def _detail(self, pk):
        return reverse('api:extras_api:journalentry-detail', kwargs={'pk': pk})

    def _etag(self, entry):
        # Mutating API requests require an If-Match precondition (optimistic
        # concurrency). The weak ETag is W/"<updated_at iso>".
        entry.refresh_from_db()
        return f'W/"{entry.updated_at.isoformat()}"'

    # --- read scoping -------------------------------------------------------

    def test_save_derives_tenant_from_object(self):
        self.assertEqual(self.entry_a.tenant, self.tenant_a)
        self.assertEqual(self.entry_b.tenant, self.tenant_b)

    def test_list_scoped_to_own_tenant(self):
        self.client.force_authenticate(user=self.staff_a)
        resp = self.client.get(reverse('api:extras_api:journalentry-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = self._ids(resp)
        self.assertIn(self.entry_a.pk, ids)
        self.assertNotIn(self.entry_b.pk, ids)

    def test_retrieve_other_tenant_entry_404(self):
        self.client.force_authenticate(user=self.staff_a)
        resp = self.client.get(self._detail(self.entry_b.pk))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_superuser_sees_all(self):
        self.client.force_authenticate(user=self.superuser)
        resp = self.client.get(reverse('api:extras_api:journalentry-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = self._ids(resp)
        self.assertIn(self.entry_a.pk, ids)
        self.assertIn(self.entry_b.pk, ids)

    def test_global_object_entry_visible_cross_tenant(self):
        # allow_global_tenant=True: an entry on a global object (tenant=None) is
        # visible to any tenant member that can see the object.
        tag = Tag.objects.create(name='JE-Global', slug='je-global')
        global_entry = JournalEntry.objects.create(
            content_object=tag, user=self.superuser, comment='global note',
        )
        self.assertIsNone(global_entry.tenant)
        self.client.force_authenticate(user=self.staff_a)
        resp = self.client.get(reverse('api:extras_api:journalentry-list'))
        self.assertIn(global_entry.pk, self._ids(resp))
        self.assertEqual(self.client.get(self._detail(global_entry.pk)).status_code, status.HTTP_200_OK)

    # --- create boundary ----------------------------------------------------

    def test_create_on_own_object(self):
        self.client.force_authenticate(user=self.staff_a)
        resp = self.client.post(
            reverse('api:extras_api:journalentry-list'),
            data={'model': self.asset_ct_str, 'object_id': self.asset_a.pk, 'comment': 'new'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        entry = JournalEntry.objects.get(pk=resp.data['id'])
        self.assertEqual(entry.tenant, self.tenant_a)
        self.assertEqual(entry.user, self.staff_a)

    def test_create_on_other_tenant_object_rejected(self):
        # Tenant-scoped target (Asset): resolved out by the scoped manager.
        self.client.force_authenticate(user=self.staff_a)
        resp = self.client.post(
            reverse('api:extras_api:journalentry-list'),
            data={'model': self.asset_ct_str, 'object_id': self.asset_b.pk, 'comment': 'sneaky'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn('tenant', str(resp.data).lower())
        self.assertFalse(JournalEntry.objects.filter(comment='sneaky').exists())

    def test_create_on_nonscoping_manager_target_rejected(self):
        # Job is tenant-owned but its default manager is NOT tenant-scoping, so a
        # plain .exists() guard would pass. validate_gfk_target_tenant compares
        # obj.tenant to the active tenant and must reject the tenant-B Job.
        self.client.force_authenticate(user=self.staff_a)
        resp = self.client.post(
            reverse('api:extras_api:journalentry-list'),
            data={'model': 'core.job', 'object_id': self.job_b.pk, 'comment': 'cross'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn('another tenant', str(resp.data).lower())
        self.assertFalse(JournalEntry.objects.filter(comment='cross').exists())

    # --- update / delete scoping & immutability -----------------------------

    def test_update_own_entry_succeeds(self):
        self.client.force_authenticate(user=self.staff_a)
        resp = self.client.patch(
            self._detail(self.entry_a.pk), {'comment': 'edited'}, format='json',
            HTTP_IF_MATCH=self._etag(self.entry_a),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.entry_a.refresh_from_db()
        self.assertEqual(self.entry_a.comment, 'edited')

    def test_update_other_tenant_entry_404(self):
        self.client.force_authenticate(user=self.staff_a)
        resp = self.client.patch(self._detail(self.entry_b.pk), {'comment': 'hijack'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.entry_b.refresh_from_db()
        self.assertEqual(self.entry_b.comment, 'B note')

    def test_delete_own_entry_succeeds(self):
        self.client.force_authenticate(user=self.staff_a)
        resp = self.client.delete(
            self._detail(self.entry_a.pk), HTTP_IF_MATCH=self._etag(self.entry_a),
        )
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_delete_other_tenant_entry_404(self):
        self.client.force_authenticate(user=self.staff_a)
        resp = self.client.delete(self._detail(self.entry_b.pk))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        # entry_b survives (objects fails open in the test body -> sees all).
        self.assertTrue(JournalEntry.objects.filter(pk=self.entry_b.pk).exists())

    def test_update_does_not_reassign_author(self):
        # A different same-tenant member edits the entry; authorship is immutable.
        self.client.force_authenticate(user=self.staff_a2)
        resp = self.client.patch(
            self._detail(self.entry_a.pk), {'comment': 'by a2'}, format='json',
            HTTP_IF_MATCH=self._etag(self.entry_a),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.entry_a.refresh_from_db()
        self.assertEqual(self.entry_a.comment, 'by a2')
        self.assertEqual(self.entry_a.user, self.staff_a)  # NOT staff_a2

    def test_update_cannot_retarget_object(self):
        # model/object_id are immutable on update: a retarget attempt is ignored.
        self.client.force_authenticate(user=self.staff_a)
        resp = self.client.patch(
            self._detail(self.entry_a.pk),
            {'object_id': self.asset_b.pk}, format='json',
            HTTP_IF_MATCH=self._etag(self.entry_a),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.entry_a.refresh_from_db()
        self.assertEqual(self.entry_a.object_id, self.asset_a.pk)
        self.assertEqual(self.entry_a.tenant, self.tenant_a)


class JournalEntrySaveTenantDerivationTests(APITestCase):
    """Unit coverage for JournalEntry.save() tenant derivation."""

    def test_global_object_yields_null_tenant(self):
        # A Tag has no tenant field -> derived tenant is None (system/global).
        tag = Tag.objects.create(name='JE-Global-Tag', slug='je-global-tag')
        entry = JournalEntry.objects.create(content_object=tag, comment='on a global object')
        self.assertIsNone(entry.tenant)
