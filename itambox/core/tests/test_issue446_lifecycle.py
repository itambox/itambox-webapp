"""Query and registration lifecycle contracts for issue #446 ports."""

from django.apps import apps
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext


class Issue446ProviderLifecycleTests(TestCase):
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
