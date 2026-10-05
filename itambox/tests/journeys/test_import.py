"""Import journeys: import never mutates beyond the role's grants and never writes lifecycle rows."""

from django.apps import apps
from django.test import TestCase
from django.urls import reverse

from assets.models import Manufacturer
from core.importers.bulk_forms import is_model_importable
from core.models import Job
from organization.models import AssetHolder

from .support import JourneyMixin


class ImportJourneyTests(JourneyMixin, TestCase):
    def setUp(self):
        self.make_tenant("journey-import")

    def _import(self, user, app_label, model_name, csv_text):
        self.client_login_to_tenant(user, self.tenant)
        url = reverse("generic_import", kwargs={"app_label": app_label, "model_name": model_name})
        payload = {"import_text": csv_text, "active_tab": "editor", "import_format": "csv", "delimiter": ","}
        preview = self.client.post(url, {**payload, "_preview": "1"})
        confirm = self.client.post(url, {"_confirm": "1"})
        return preview, confirm

    def test_add_only_role_cannot_update_an_existing_row_by_id(self):
        existing = Manufacturer.objects.create(name="Journey Original", slug="journey-original")
        adder = self.make_member("import-adder", {"assets.add_manufacturer"})

        self._import(adder, "assets", "manufacturer", f"id,name,slug\n{existing.pk},Journey Renamed,journey-original\n")

        existing.refresh_from_db()
        self.assertEqual(existing.name, "Journey Original", "an add-only role rewrote an existing row by id")

    def test_add_only_role_cannot_update_an_existing_row_by_natural_key(self):
        existing = Manufacturer.objects.create(name="Journey Keyed", slug="journey-keyed", description="before")
        adder = self.make_member("import-key-adder", {"assets.add_manufacturer"})

        self._import(adder, "assets", "manufacturer", "name,description\nJourney Keyed,after\n")

        existing.refresh_from_db()
        self.assertEqual(existing.description, "before", "an add-only role updated a row through the natural key")
        job = Job.objects.latest("created")
        self.assertIn("permission to update", job.logs)

    def test_change_role_can_update_by_natural_key(self):
        existing = Manufacturer.objects.create(name="Journey Editable", slug="journey-editable", description="before")
        editor = self.make_member("import-editor", {"assets.add_manufacturer", "assets.change_manufacturer"})

        self._import(editor, "assets", "manufacturer", "name,description\nJourney Editable,after\n")

        existing.refresh_from_db()
        self.assertEqual(existing.description, "after")

    def test_add_only_role_cannot_rewrite_an_asset_holder_by_id(self):
        holder = self.make_holder(email="holder@example.test", upn="holder@example.test")
        adder = self.make_member(
            "import-holder-adder", {"organization.add_assetholder", "organization.view_assetholder"}
        )

        self._import(
            adder,
            "organization",
            "assetholder",
            f"id,first_name,last_name,upn,email\n{holder.pk},Journey,Holder,holder@example.test,attacker@evil.test\n",
        )

        holder.refresh_from_db()
        self.assertEqual(holder.email, "holder@example.test")
        self.assertEqual(AssetHolder.objects.filter(upn="holder@example.test").count(), 1)

    def test_custody_receipts_and_asset_assignments_are_not_importable(self):
        for label in ("compliance.custodyreceipt", "assets.assetassignment"):
            model = apps.get_model(label)
            self.assertFalse(is_model_importable(model), f"{label} must be written only by its service")

    def test_service_owned_models_answer_404_on_the_import_route(self):
        user = self.make_member("import-lifecycle", {"compliance.add_custodyreceipt", "assets.add_assetassignment"})
        self.client_login_to_tenant(user, self.tenant)
        for app_label, model_name in (("compliance", "custodyreceipt"), ("assets", "assetassignment")):
            url = reverse("generic_import", kwargs={"app_label": app_label, "model_name": model_name})
            with self.subTest(model=f"{app_label}.{model_name}"):
                self.assertEqual(self.client.get(url).status_code, 404)
                self.assertEqual(self.client.post(url, {"_preview": "1", "import_text": "x"}).status_code, 404)
