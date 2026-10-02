"""Import journeys: import never mutates beyond the role's grants and never writes lifecycle rows."""

import pytest
from django.apps import apps
from django.test import TestCase
from django.urls import reverse

from assets.models import Manufacturer
from core.importers.bulk_forms import is_model_importable

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

    @pytest.mark.xfail(strict=True, reason="import upserts by id with only add_<model> (#605)")
    def test_add_only_role_cannot_update_an_existing_row_by_id(self):
        existing = Manufacturer.objects.create(name="Journey Original", slug="journey-original")
        adder = self.make_member("import-adder", {"assets.add_manufacturer"})

        self._import(adder, "assets", "manufacturer", f"id,name,slug\n{existing.pk},Journey Renamed,journey-original\n")

        existing.refresh_from_db()
        self.assertEqual(existing.name, "Journey Original", "an add-only role rewrote an existing row by id")

    @pytest.mark.xfail(strict=True, reason="lifecycle rows are importable (#605)")
    def test_custody_receipts_and_asset_assignments_are_not_importable(self):
        for label in ("compliance.custodyreceipt", "assets.assetassignment"):
            model = apps.get_model(label)
            self.assertFalse(is_model_importable(model), f"{label} must be written only by its service")
