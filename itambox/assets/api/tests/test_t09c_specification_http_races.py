"""Real PostgreSQL contention through the supported legacy HTTP adapter."""

import pytest
from django.db import connection, transaction
from django.urls import reverse
from rest_framework.test import APITransactionTestCase

from assets.models import AssetType, Manufacturer
from assets.services.specifications.commands import update_asset_type_specifications
from assets.services.specifications.contracts import OwnerChangedDTO, SpecificationPatchDTO
from assets.specification_adapters import actor_context_for_user, current_specification_plan
from assets.tests.test_specification_composition_races import _assert_waiting, _finish, _start
from core.tests.mixins import TenantTestMixin
from extras.models import CustomField
from django.contrib.contenttypes.models import ContentType


@pytest.mark.serial_only
class TestLegacySpecificationHTTPContention(TenantTestMixin, APITransactionTestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(connection.vendor, "postgresql")
        self.setup_tenant_context(name="HTTP contention", slug="http-contention", permissions=[])
        field = CustomField.objects.create(
            namespace="local",
            name="http_contention_note",
            label="HTTP contention note",
            field_type=CustomField.FIELD_TYPE_TEXT,
            activation=CustomField.ACTIVATION_GLOBAL,
        )
        field.object_types.add(ContentType.objects.get_for_model(AssetType))
        manufacturer = Manufacturer.objects.create(name="HTTP contention maker", slug="http-contention-maker")
        self.owner = AssetType.objects.create(
            manufacturer=manufacturer, model="HTTP contention type", slug="http-contention-type"
        )
        self.client_login_to_tenant(self.tenant_admin, self.tenant)

    def test_http_update_holds_shared_catalogue_before_rejecting_stale_etag(self):
        url = reverse("api:assets_api:assettype-detail", args=[self.owner.pk])
        etag = self.client.get(url)["ETag"]
        plan = current_specification_plan(self.owner, target_kind="asset_type")
        actor = actor_context_for_user(self.tenant_admin)
        started = None
        try:
            with transaction.atomic():
                result = update_asset_type_specifications(
                    actor=actor,
                    asset_type_id=self.owner.pk,
                    expected_resource_revision=plan.resource_revision,
                    expected_definition_revision=plan.definition_revision,
                    patch=SpecificationPatchDTO(set_values={"http_contention_note": "winner"}, clear_keys=()),
                )
                self.assertIsInstance(result, OwnerChangedDTO)

                def request():
                    return self.client.patch(
                        url,
                        {
                            "model": "Stale competing native edit",
                            "specification_patch": {"set": {"http_contention_note": "loser"}, "clear": []},
                        },
                        format="json",
                        HTTP_IF_MATCH=etag,
                    )

                started = _start(request)
                _assert_waiting(started[1], advisory=False)
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT mode FROM pg_locks WHERE pid = %s AND locktype = 'advisory' AND granted",
                        [started[1]],
                    )
                    self.assertEqual(cursor.fetchall(), [("ShareLock",)])
        finally:
            if started is not None:
                response = _finish(started)
        self.assertEqual(response.status_code, 412, response.data)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.model, "HTTP contention type")
        self.assertEqual(self.owner.custom_field_data, {"http_contention_note": "winner"})
