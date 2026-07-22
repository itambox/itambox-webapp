from unittest.mock import patch

from django.test import RequestFactory, TestCase
from model_bakery import baker

from assets.forms.asset_form import AssetForm
from assets.models import Asset, AssetType


class AssetFormAssetTypePrecedenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.instance_type = baker.make(AssetType)
        cls.initial_type = baker.make(AssetType)
        cls.request_type = baker.make(AssetType)
        cls.bound_type = baker.make(AssetType)
        cls.asset = baker.make(Asset, asset_type=cls.instance_type)

    def _selected_asset_type(self, *args, **kwargs):
        with patch(
            'assets.models.AssetTagSequence.resolve_sequence_for_asset',
            return_value=None,
        ) as resolve_sequence:
            form = AssetForm(*args, **kwargs)

        preview_asset = resolve_sequence.call_args.args[0]
        return form, preview_asset.asset_type

    def test_new_form_accepts_initial_asset_type_instance(self):
        form, selected_type = self._selected_asset_type(
            initial={'asset_type': self.initial_type},
        )

        self.assertEqual(form.initial['asset_type'], self.initial_type)
        self.assertEqual(selected_type, self.initial_type)

    def test_new_form_accepts_initial_asset_type_id(self):
        form, selected_type = self._selected_asset_type(
            initial={'asset_type': self.initial_type.pk},
        )

        self.assertEqual(form.initial['asset_type'], self.initial_type.pk)
        self.assertEqual(selected_type, self.initial_type)

    def test_explicit_initial_overrides_existing_instance(self):
        form, selected_type = self._selected_asset_type(
            instance=self.asset,
            initial={'asset_type': self.initial_type},
        )

        self.assertEqual(form.initial['asset_type'], self.initial_type)
        self.assertEqual(selected_type, self.initial_type)

    def test_existing_instance_is_used_as_fallback(self):
        _, selected_type = self._selected_asset_type(instance=self.asset)

        self.assertEqual(selected_type, self.instance_type)

    def test_quick_add_query_overrides_initial_and_instance(self):
        request = RequestFactory().get(
            '/',
            {'asset_type': str(self.request_type.pk)},
        )

        _, selected_type = self._selected_asset_type(
            instance=self.asset,
            initial={'asset_type': self.initial_type},
            request=request,
        )

        self.assertEqual(selected_type, self.request_type)

    def test_bound_data_overrides_query_initial_and_instance(self):
        request = RequestFactory().get(
            '/',
            {'asset_type': str(self.request_type.pk)},
        )

        _, selected_type = self._selected_asset_type(
            data={'asset_type': str(self.bound_type.pk)},
            instance=self.asset,
            initial={'asset_type': self.initial_type},
            request=request,
        )

        self.assertEqual(selected_type, self.bound_type)

    def test_bound_invalid_asset_type_produces_validation_error(self):
        request = RequestFactory().get(
            '/',
            {'asset_type': str(self.request_type.pk)},
        )

        invalid_values = ('not-an-id', '', str(max(
            self.instance_type.pk,
            self.initial_type.pk,
            self.request_type.pk,
            self.bound_type.pk,
        ) + 1000))
        for invalid_value in invalid_values:
            with self.subTest(asset_type=invalid_value):
                form, selected_type = self._selected_asset_type(
                    data={'asset_type': invalid_value},
                    instance=self.asset,
                    initial={'asset_type': self.initial_type},
                    request=request,
                )

                self.assertIsNone(selected_type)
                self.assertFalse(form.is_valid())
                self.assertIn('asset_type', form.errors)
