import importlib
from types import SimpleNamespace
from unittest import TestCase

_matched_supplier = importlib.import_module("subscriptions.migrations.0103_unified_vendor_cutover")._matched_supplier


class VendorCutoverMappingTests(TestCase):
    def test_live_supplier_is_preferred_over_an_earlier_tombstone(self):
        tombstone = SimpleNamespace(deleted_at=object())
        live = SimpleNamespace(deleted_at=None)
        provider = SimpleNamespace(deleted_at=None)

        self.assertIs(_matched_supplier([tombstone, live], provider), live)

    def test_deleted_provider_can_reuse_a_tombstone(self):
        tombstone = SimpleNamespace(deleted_at=object())
        provider = SimpleNamespace(deleted_at=object())

        self.assertIs(_matched_supplier([tombstone], provider), tombstone)

    def test_live_provider_does_not_reuse_a_tombstone(self):
        tombstone = SimpleNamespace(deleted_at=object())
        provider = SimpleNamespace(deleted_at=None)

        self.assertIsNone(_matched_supplier([tombstone], provider))

    def test_missing_match_returns_none(self):
        provider = SimpleNamespace(deleted_at=None)

        self.assertIsNone(_matched_supplier([], provider))
