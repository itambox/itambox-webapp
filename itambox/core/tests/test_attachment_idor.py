from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from assets.models import Asset, StatusLabel
from core.models import Job
from core.tests.mixins import grant
from extras.models import FileAttachment, ImageAttachment
from organization.models import Role, Tenant

User = get_user_model()


class AttachmentCrossTenantIDORTests(TestCase):
    """WS7-3: the attachment proxy is the only barrier against downloading another tenant's
    file/image by guessing a pk (attachments have no tenant — they inherit it from the
    parent). This boundary previously had zero regression coverage."""

    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="A", slug="a-att")
        self.tenant_b = Tenant.objects.create(name="B", slug="b-att")
        self.status = StatusLabel.objects.create(name="Dep", slug="dep-att", type="deployable")
        self.asset_a = Asset.objects.create(name="AA", asset_tag="ATT-A", status=self.status, tenant=self.tenant_a)
        self.asset_b = Asset.objects.create(name="BB", asset_tag="ATT-B", status=self.status, tenant=self.tenant_b)
        self.user = User.objects.create_user(username="attuser", password="pw")
        grant(
            self.user,
            self.tenant_a,
            Role.objects.create(tenant=self.tenant_a, name="R", permissions=["assets.view_asset"]),
        )
        ct = ContentType.objects.get_for_model(Asset)
        self.file_b = FileAttachment.objects.create(
            model=ct,
            object_id=self.asset_b.pk,
            file=SimpleUploadedFile("b.txt", b"secret"),
            name="b.txt",
        )
        self.file_a = FileAttachment.objects.create(
            model=ct,
            object_id=self.asset_a.pk,
            file=SimpleUploadedFile("a.txt", b"mine"),
            name="a.txt",
        )
        self.image_b = ImageAttachment.objects.create(
            model=ct,
            object_id=self.asset_b.pk,
            image=SimpleUploadedFile("b.png", b"\x89PNG\r\n"),
            name="b.png",
        )

    def _login_a(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["active_tenant_id"] = self.tenant_a.pk
        session.save()

    def test_cross_tenant_file_download_returns_404(self):
        self._login_a()
        url = reverse("file_attachment_download", kwargs={"pk": self.file_b.pk})
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_cross_tenant_image_serve_returns_404(self):
        self._login_a()
        url = reverse("image_attachment_serve", kwargs={"pk": self.image_b.pk})
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_own_tenant_file_download_ok_with_headers(self):
        self._login_a()
        url = reverse("file_attachment_download", kwargs={"pk": self.file_a.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["X-Content-Type-Options"], "nosniff")
        self.assertIn("attachment", resp["Content-Disposition"])

    def test_view_permission_allows_read_but_not_delete_or_upload(self):
        self._login_a()

        delete_url = reverse("file_attachment_delete", kwargs={"pk": self.file_a.pk})
        self.assertEqual(self.client.post(delete_url, {"return_url": "/"}).status_code, 404)
        self.assertTrue(FileAttachment.objects.filter(pk=self.file_a.pk).exists())

        upload_url = reverse(
            "file_attachment_upload",
            kwargs={"app_label": "assets", "model_name": "asset", "object_id": self.asset_a.pk},
        )
        response = self.client.post(upload_url, {"file": SimpleUploadedFile("blocked.txt", b"blocked")})
        self.assertEqual(response.status_code, 404)


class JobAttachmentAuthorizationTests(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Job Attachment A", slug="job-attachment-a")
        self.tenant_b = Tenant.objects.create(name="Job Attachment B", slug="job-attachment-b")
        self.user = User.objects.create_user(username="job_attachment_user", password="pw")
        self.role = Role.objects.create(
            tenant=self.tenant_a,
            name="Job Attachment Viewer",
            permissions=["core.view_job"],
        )
        grant(self.user, self.tenant_a, self.role)
        self.job_content_type = ContentType.objects.get_for_model(Job)

    def make_attachment(self, job, name):
        return FileAttachment.objects.create(
            model=self.job_content_type,
            object_id=job.pk,
            file=SimpleUploadedFile(name, name.encode()),
            name=name,
        )

    def login_aggregate(self):
        self.client.force_login(self.user)
        session = self.client.session
        session.pop("active_tenant_id", None)
        session["active_all_accessible"] = True
        session.save()

    def test_global_parent_without_object_view_permission_returns_404(self):
        denied_user = User.objects.create_user(username="job_attachment_denied", password="pw")
        grant(
            denied_user,
            self.tenant_a,
            Role.objects.create(tenant=self.tenant_a, name="Job Attachment Denied", permissions=[]),
        )
        job = Job.objects.create(name="Global parent", tenant=None)
        attachment = self.make_attachment(job, "global-parent.txt")
        self.client.force_login(denied_user)
        session = self.client.session
        session["active_tenant_id"] = self.tenant_a.pk
        session.save()

        response = self.client.get(reverse("file_attachment_download", kwargs={"pk": attachment.pk}))

        self.assertEqual(response.status_code, 404)

    def test_authorized_aggregate_scope_job_attachment_succeeds_with_headers(self):
        job = Job.objects.create(name="Aggregate parent", tenant=self.tenant_a)
        attachment = self.make_attachment(job, "aggregate-parent.txt")
        self.login_aggregate()

        response = self.client.get(reverse("file_attachment_download", kwargs={"pk": attachment.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("attachment", response["Content-Disposition"])

    def test_inaccessible_aggregate_scope_job_attachment_returns_404(self):
        job = Job.objects.create(name="Foreign aggregate parent", tenant=self.tenant_b)
        attachment = self.make_attachment(job, "foreign-aggregate-parent.txt")
        self.login_aggregate()

        response = self.client.get(reverse("file_attachment_download", kwargs={"pk": attachment.pk}))

        self.assertEqual(response.status_code, 404)

    def test_cross_workspace_read_follows_object_bound_membership_permission(self):
        """Adaptation #8 (review): reads use the canonical membership-based object
        permission semantics of ``TenantMembershipBackend`` instead of the old
        active-tenant-equality check.

        The user already holds the object-bound ``view_job`` permission through
        the tenant_a membership from ``setUp``; activation of the unrelated
        tenant_b membership only switches the ACTIVE workspace. The read still
        succeeds, pinning that the workspace no longer decides attachment
        access — only the object-bound permission does. This is the deliberate
        replacement mandated by Design v1.1 section 8: the old check denied
        authorized aggregate-scope Job attachments (no single active tenant)
        and permitted unscoped global parents without any permission at all;
        the new check binds reads to the object permission for every parent,
        while tenant-scoped parents keep ordinary cross-tenant denial through
        their scoped default manager.
        """
        job = Job.objects.create(name="A-side parent", tenant=self.tenant_a)
        attachment = self.make_attachment(job, "a-side-parent.txt")
        grant(
            self.user,
            self.tenant_b,
            Role.objects.create(tenant=self.tenant_b, name="Job Attachment B Member", permissions=["core.view_job"]),
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["active_tenant_id"] = self.tenant_b.pk
        session.pop("active_all_accessible", None)
        session.save()

        response = self.client.get(reverse("file_attachment_download", kwargs={"pk": attachment.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
