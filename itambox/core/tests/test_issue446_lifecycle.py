"""Query and registration lifecycle contracts for issue #446 ports."""

from django.apps import apps
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from core.authorization_cache import force_authorization_generation_check
from core.provider_slot import SingleProviderSlot
from inventory.models_kit_checkout import KitCheckoutProvider, get_kit_checkout, register_kit_checkout


class Issue446ProviderLifecycleTests(TestCase):
    def test_generation_recheck_has_zero_orm_queries_when_generation_is_unchanged(self):
        user_id = 446991
        user_version = "issue446-user-version"
        topology_version = "issue446-topology-version"
        cache.set(f"itambox:authz-version:{user_id}", user_version)
        cache.set("itambox:authz-topology-version", topology_version)
        user = type("CachedUser", (), {})()
        user.pk = user_id
        user._authorization_cache_version = (user_version, topology_version)
        user._tenant_permissions_map = {1: (frozenset({"assets.change_asset"}), None)}

        try:
            with CaptureQueriesContext(connection) as queries:
                force_authorization_generation_check(user)
            self.assertEqual(len(queries), 0, queries.captured_queries)
            self.assertIn("_tenant_permissions_map", user.__dict__)
        finally:
            cache.delete(f"itambox:authz-version:{user_id}")

    def test_assets_ready_repeated_registration_is_exact_and_zero_query(self):
        assets_config = apps.get_app_config("assets")
        from assets.services import checkout_kit

        with CaptureQueriesContext(connection) as queries:
            assets_config.ready()
            assets_config.ready()
            provider = get_kit_checkout()
            registered = register_kit_checkout(checkout_kit)

        self.assertEqual(len(queries), 0, queries.captured_queries)
        self.assertIs(provider, checkout_kit)
        self.assertIs(registered, checkout_kit)

    def test_kit_port_missing_different_object_and_override_contract(self):
        slot = SingleProviderSlot[KitCheckoutProvider]("inventory kit checkout")
        first = object()
        second = object()

        with self.assertRaisesMessage(
            ImproperlyConfigured,
            "inventory kit checkout provider is not configured",
        ):
            slot.get()

        slot.register(first)
        with self.assertRaisesMessage(
            ImproperlyConfigured,
            "inventory kit checkout provider is already configured with a different object",
        ):
            slot.register(second)
        self.assertIs(slot.get(), first)

        with slot.override(second):
            self.assertIs(slot.get(), second)
        self.assertIs(slot.get(), first)

    def test_licenses_ready_and_lookup_are_zero_query_and_identity_safe(self):
        licenses_config = apps.get_app_config("licenses")
        with CaptureQueriesContext(connection) as queries:
            licenses_config.ready()
            licenses_config.ready()
            from licenses.reconciliation import reconcile_software
            from software.models_reconciliation import get_software_reconciliation_provider

            provider = get_software_reconciliation_provider()
        self.assertEqual(len(queries), 0, queries.captured_queries)
        self.assertIs(provider, reconcile_software)

    def test_subscriptions_ready_and_lookup_are_zero_query_and_identity_safe(self):
        subscriptions_config = apps.get_app_config("subscriptions")
        with CaptureQueriesContext(connection) as queries:
            subscriptions_config.ready()
            subscriptions_config.ready()
            from subscriptions.models_seat_usage import get_seat_usage_provider
            from subscriptions.seat_services import count_assigned_seats

            provider = get_seat_usage_provider()
        self.assertEqual(len(queries), 0, queries.captured_queries)
        self.assertIs(provider, count_assigned_seats)

    def test_port_lookup_does_not_evaluate_an_orm_query(self):
        from software.models_reconciliation import get_software_reconciliation_provider
        from subscriptions.models_seat_usage import get_seat_usage_provider

        with CaptureQueriesContext(connection) as queries:
            get_software_reconciliation_provider()
            get_seat_usage_provider()
        self.assertEqual(len(queries), 0, queries.captured_queries)
