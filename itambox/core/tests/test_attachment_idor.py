from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from assets.models import Asset, StatusLabel
from extras.models import FileAttachment, ImageAttachment
from organization.models import Tenant, Role, Membership

User = get_user_model()


class AttachmentCrossTenantIDORTests(TestCase):
    """WS7-3: the attachment proxy is the only barrier against downloading another tenant's
    file/image by guessing a pk (attachments have no tenant — they inherit it from the
    parent). This boundary previously had zero regression coverage."""

    def setUp(self):
        self.tenant_a = Tenant.objects.create(name='A', slug='a-att')
        self.tenant_b = Tenant.objects.create(name='B', slug='b-att')
        self.status = StatusLabel.objects.create(name='Dep', slug='dep-att', type='deployable')
        self.asset_a = Asset.objects.create(name='AA', asset_tag='ATT-A', status=self.status, tenant=self.tenant_a)
        self.asset_b = Asset.objects.create(name='BB', asset_tag='ATT-B', status=self.status, tenant=self.tenant_b)
        self.user = User.objects.create_user(username='attuser', password='pw')
        m = Membership.objects.create(person_type=Membership.PERSON_MEMBER, user=self.user, tenant=self.tenant_a)
        m.roles.add(Role.objects.create(tenant=self.tenant_a, name='R', permissions=['assets.view_asset']))
        ct = ContentType.objects.get_for_model(Asset)
        self.file_b = FileAttachment.objects.create(
            model=ct, object_id=self.asset_b.pk, file=SimpleUploadedFile('b.txt', b'secret'), name='b.txt',
        )
        self.file_a = FileAttachment.objects.create(
            model=ct, object_id=self.asset_a.pk, file=SimpleUploadedFile('a.txt', b'mine'), name='a.txt',
        )
        self.image_b = ImageAttachment.objects.create(
            model=ct, object_id=self.asset_b.pk, image=SimpleUploadedFile('b.png', b'\x89PNG\r\n'), name='b.png',
        )

    def _login_a(self):
        self.client.force_login(self.user)
        session = self.client.session
        session['active_tenant_id'] = self.tenant_a.pk
        session.save()

    def test_cross_tenant_file_download_returns_404(self):
        self._login_a()
        url = reverse('file_attachment_download', kwargs={'pk': self.file_b.pk})
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_cross_tenant_image_serve_returns_404(self):
        self._login_a()
        url = reverse('image_attachment_serve', kwargs={'pk': self.image_b.pk})
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_own_tenant_file_download_ok_with_headers(self):
        self._login_a()
        url = reverse('file_attachment_download', kwargs={'pk': self.file_a.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['X-Content-Type-Options'], 'nosniff')
        self.assertIn('attachment', resp['Content-Disposition'])
