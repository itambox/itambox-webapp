from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase

from users.api.scim.provider_patch import SCIMPatchError
from users.api.scim.provider_services import _require_provider_actor


class ProviderServiceGuardTests(SimpleTestCase):
    def test_requires_provider_tenant(self):
        actor = SimpleNamespace(is_authenticated=True, is_superuser=False)
        tenant = SimpleNamespace(is_provider=False)

        with self.assertRaisesRegex(SCIMPatchError, "provider tenant") as context:
            _require_provider_actor(tenant, actor, permission="organization.change_membership")

        self.assertEqual(context.exception.status_code, 403)

    def test_requires_authenticated_actor(self):
        tenant = SimpleNamespace(is_provider=True)
        actor = SimpleNamespace(is_authenticated=False, is_superuser=False)

        with self.assertRaisesRegex(SCIMPatchError, "authenticated actor") as context:
            _require_provider_actor(tenant, actor, permission="organization.change_membership")

        self.assertEqual(context.exception.status_code, 401)

    def test_requires_permission_unless_superuser(self):
        tenant = SimpleNamespace(is_provider=True)
        actor = Mock(is_authenticated=True, is_superuser=False)
        actor.has_perm.return_value = False

        with self.assertRaisesRegex(SCIMPatchError, "permission") as context:
            _require_provider_actor(tenant, actor, permission="organization.change_membership")

        self.assertEqual(context.exception.status_code, 403)
        actor.has_perm.assert_called_once_with("organization.change_membership", obj=tenant)

        superuser = SimpleNamespace(is_authenticated=True, is_superuser=True)
        _require_provider_actor(tenant, superuser, permission="organization.change_membership")
