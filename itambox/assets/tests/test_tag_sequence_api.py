from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from assets.models import AssetTagSequence

User = get_user_model()


class AssetTagSequenceAPITests(APITestCase):
    def setUp(self):
        self.superuser = User.objects.create_user(
            username="superuser",
            email="super@example.com",
            password="password123",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_authenticate(user=self.superuser)
        self.url = reverse("api:assets_api:assettagsequence-list")

    def _post_invalid_payload(self, payload):
        initial_count = AssetTagSequence.objects.count()
        response = self.client.post(self.url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(AssetTagSequence.objects.count(), initial_count)
        return response

    def test_create_rejects_unknown_only_payload(self):
        response = self._post_invalid_payload({"totally_unknown_field": "x"})

        self.assertEqual(response.data["totally_unknown_field"], ["Unknown field."])

    def test_create_rejects_mixed_known_and_unknown_payload(self):
        response = self._post_invalid_payload({"prefix": "X-", "bogus": 1})

        self.assertEqual(response.data["bogus"], ["Unknown field."])

    def test_create_rejects_empty_payload(self):
        response = self._post_invalid_payload({})

        self.assertEqual(response.data["non_field_errors"], ["At least one writable field is required."])

    def test_create_rejects_read_only_only_payload(self):
        response = self._post_invalid_payload({"created_at": "2026-01-01T00:00:00Z"})

        self.assertEqual(response.data["non_field_errors"], ["At least one writable field is required."])

    def test_create_with_writable_field_preserves_model_defaults(self):
        initial_count = AssetTagSequence.objects.count()

        response = self.client.post(self.url, data={"prefix": "API-X-"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(AssetTagSequence.objects.count(), initial_count + 1)
        sequence = AssetTagSequence.objects.get(pk=response.data["id"])
        self.assertEqual(sequence.prefix, "API-X-")
        self.assertEqual(sequence.next_value, 1)
        self.assertEqual(sequence.zero_padding, 6)
        self.assertTrue(sequence.is_active)

    def test_invalid_creates_leave_pre_existing_sequence_untouched(self):
        sequence = AssetTagSequence.objects.create(
            prefix="PRE-",
            next_value=7,
            zero_padding=4,
            is_active=False,
        )
        initial_count = AssetTagSequence.objects.count()
        initial_values = {
            "prefix": sequence.prefix,
            "next_value": sequence.next_value,
            "zero_padding": sequence.zero_padding,
            "is_active": sequence.is_active,
            "tenant_id": sequence.tenant_id,
            "category_id": sequence.category_id,
            "updated_at": sequence.updated_at,
        }

        invalid_requests = [
            ({"totally_unknown_field": "x"}, "totally_unknown_field"),
            ({"prefix": "X-", "bogus": 1}, "bogus"),
            ({}, "non_field_errors"),
        ]
        for payload, error_field in invalid_requests:
            response = self.client.post(self.url, data=payload, format="json")

            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn(error_field, response.data)
            self.assertEqual(AssetTagSequence.objects.count(), initial_count)

        sequence.refresh_from_db()
        for field, value in initial_values.items():
            self.assertEqual(getattr(sequence, field), value)

    def test_bulk_create_with_valid_items_succeeds(self):
        initial_count = AssetTagSequence.objects.count()

        response = self.client.post(
            self.url,
            data=[{"prefix": "BULK-A-"}, {"prefix": "BULK-B-"}],
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(AssetTagSequence.objects.count(), initial_count + 2)
        self.assertEqual(AssetTagSequence.objects.filter(prefix__startswith="BULK-").count(), 2)

    def test_bulk_create_rejects_unknown_only_item(self):
        initial_count = AssetTagSequence.objects.count()

        response = self.client.post(self.url, data=[{"bogus": 1}], format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(AssetTagSequence.objects.count(), initial_count)

    def test_bulk_create_rejects_empty_item(self):
        initial_count = AssetTagSequence.objects.count()

        response = self.client.post(self.url, data=[{}], format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(AssetTagSequence.objects.count(), initial_count)

    def test_partial_update_rejects_unknown_field(self):
        sequence = AssetTagSequence.objects.create(prefix="PATCH-", next_value=3)
        detail_url = reverse("api:assets_api:assettagsequence-detail", kwargs={"pk": sequence.pk})
        etag = self.client.get(detail_url)["ETag"]

        response = self.client.patch(detail_url, data={"bogus": 1}, format="json", HTTP_IF_MATCH=etag)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("bogus", response.data)
        sequence.refresh_from_db()
        self.assertEqual(sequence.prefix, "PATCH-")
        self.assertEqual(sequence.next_value, 3)

    def test_partial_update_with_writable_field_succeeds(self):
        sequence = AssetTagSequence.objects.create(prefix="PATCH-", is_active=True)
        detail_url = reverse("api:assets_api:assettagsequence-detail", kwargs={"pk": sequence.pk})
        etag = self.client.get(detail_url)["ETag"]

        response = self.client.patch(detail_url, data={"is_active": False}, format="json", HTTP_IF_MATCH=etag)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        sequence.refresh_from_db()
        self.assertFalse(sequence.is_active)
