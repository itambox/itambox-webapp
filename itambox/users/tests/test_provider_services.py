import importlib
from collections.abc import Collection, Sequence
from inspect import Parameter, signature
from types import SimpleNamespace
from typing import get_origin, get_type_hints
from unittest.mock import Mock

from django.test import SimpleTestCase

from users.api.scim.provider_patch import SCIMPatchError
from users.api.scim.provider_services import (
    _require_provider_actor,
    create_provider_group,
    sync_provider_group_members,
)


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

    def test_group_member_service_contract_accepts_set_like_collections(self):
        for service in (create_provider_group, sync_provider_group_members):
            annotation = signature(service).parameters["member_ids"].annotation
            self.assertIs(get_origin(annotation), Collection)


class ProviderViewSignatureTests(SimpleTestCase):
    def test_provider_framework_and_config_signatures_are_typed(self):
        # Warm up the first-party API package to avoid its DRF default-authentication import cycle.
        importlib.import_module("itambox.api")
        from rest_framework.authentication import BaseAuthentication
        from rest_framework.permissions import BasePermission, OperandHolder, SingleOperandHolder
        from rest_framework.request import Request
        from rest_framework.response import Response

        from users.api.scim.provider_views import ProviderServiceProviderConfigView, SCIMProviderMixin

        contracts = (
            (
                SCIMProviderMixin.require_group_permission,
                [
                    ("self", Parameter.POSITIONAL_OR_KEYWORD),
                    ("request", Parameter.POSITIONAL_OR_KEYWORD),
                    ("permission", Parameter.POSITIONAL_OR_KEYWORD),
                ],
                {"request": Request, "permission": str, "return": type(None)},
            ),
            (
                SCIMProviderMixin.handle_exception,
                [("self", Parameter.POSITIONAL_OR_KEYWORD), ("exc", Parameter.POSITIONAL_OR_KEYWORD)],
                {"exc": Exception, "return": Response},
            ),
            (
                SCIMProviderMixin.initial,
                [
                    ("self", Parameter.POSITIONAL_OR_KEYWORD),
                    ("request", Parameter.POSITIONAL_OR_KEYWORD),
                    ("args", Parameter.VAR_POSITIONAL),
                    ("kwargs", Parameter.VAR_KEYWORD),
                ],
                {"request": Request, "args": object, "kwargs": object, "return": type(None)},
            ),
            (
                ProviderServiceProviderConfigView.get,
                [
                    ("self", Parameter.POSITIONAL_OR_KEYWORD),
                    ("request", Parameter.POSITIONAL_OR_KEYWORD),
                    ("args", Parameter.VAR_POSITIONAL),
                    ("kwargs", Parameter.VAR_KEYWORD),
                ],
                {"request": Request, "args": object, "kwargs": object, "return": Response},
            ),
        )

        for method, expected_parameters, expected_annotations in contracts:
            with self.subTest(method=method.__qualname__):
                self.assertEqual(
                    [(name, parameter.kind) for name, parameter in signature(method).parameters.items()],
                    expected_parameters,
                )
                self.assertEqual(get_type_hints(method), expected_annotations)

        expected_mixin_annotations = {
            "authentication_classes": Sequence[type[BaseAuthentication]],
            "permission_classes": Sequence[type[BasePermission] | OperandHolder | SingleOperandHolder],
        }
        mixin_hints = get_type_hints(SCIMProviderMixin)
        self.assertEqual(
            {name: mixin_hints[name] for name in expected_mixin_annotations},
            expected_mixin_annotations,
        )
