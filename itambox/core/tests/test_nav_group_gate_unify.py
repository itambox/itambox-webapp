"""Regression tests for FIX #10 / defect #9.

The navigation gate ``core.navigation.menu.can_manage_user_groups`` used to have its
own truncated implementation that omitted the direct single-company
``organization.manage_groups`` ``user_permissions`` grant honoured by the canonical
gate ``core.auth.provider.can_manage_user_groups``. This meant a single-company admin
granted the permission directly got the capability from the backend but had the
"User Groups" menu hidden.

These tests assert the nav gate is now in parity with the canonical gate.
"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType

from core.auth.provider import can_manage_user_groups as canonical_gate
from core.navigation.menu import can_manage_user_groups as nav_gate
from core.tests.mixins import TenantTestMixin
from organization.models import Provider

User = get_user_model()


def _manage_groups_permission():
    """The ``organization.manage_groups`` Permission (declared on Provider.Meta)."""
    ct = ContentType.objects.get_for_model(Provider)
    return Permission.objects.get(content_type=ct, codename='manage_groups')


@pytest.mark.django_db
class TestNavGroupGateUnify(TenantTestMixin):
    def test_direct_permission_grant_recognized_by_nav_gate(self):
        """A user with ONLY the direct organization.manage_groups user_permission
        (no provider capability, not a superuser) is recognized by BOTH the nav gate
        and the canonical gate."""
        user = User.objects.create_user(
            username='direct_grant_user',
            email='direct_grant@example.com',
            password='password',
        )
        user.user_permissions.add(_manage_groups_permission())
        # Re-fetch to clear any cached permission state on the instance.
        user = User.objects.get(pk=user.pk)

        # Parity: the canonical gate honours the direct grant, and now so does the nav gate.
        assert canonical_gate(user) is True
        assert nav_gate(user) is True

    def test_user_without_grant_denied_by_both_gates(self):
        """A user with neither the direct permission nor a provider capability is
        denied by BOTH gates."""
        user = User.objects.create_user(
            username='no_grant_user',
            email='no_grant@example.com',
            password='password',
        )

        assert canonical_gate(user) is False
        assert nav_gate(user) is False

    def test_nav_gate_delegates_to_canonical(self):
        """The nav gate result matches the canonical gate for both grant states."""
        granted = User.objects.create_user(
            username='parity_granted',
            email='parity_granted@example.com',
            password='password',
        )
        granted.user_permissions.add(_manage_groups_permission())
        granted = User.objects.get(pk=granted.pk)

        denied = User.objects.create_user(
            username='parity_denied',
            email='parity_denied@example.com',
            password='password',
        )

        assert nav_gate(granted) == canonical_gate(granted) is True
        assert nav_gate(denied) == canonical_gate(denied) is False
