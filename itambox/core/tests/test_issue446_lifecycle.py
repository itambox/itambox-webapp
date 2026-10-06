"""Query and registration lifecycle contracts for issue #446 ports."""

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from core.authorization_cache import force_authorization_generation_check


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
