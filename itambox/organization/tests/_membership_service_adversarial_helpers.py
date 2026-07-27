"""Shared loader, world builder, and write-fingerprint for the #86 service suites.

The services these suites exercise — ``organization.services.errors``,
``organization.services.rolegrants`` and ``organization.services.membership`` —
are the extraction target of issue #86. Importing them at module scope before
they exist would turn every suite into a *collection* error, which hides whether
the tests themselves are well formed. :func:`membership_services` resolves the
whole documented surface once instead and fails the individual test with the
exact module or symbol that is missing, so a red run names the absent service and
a green run has already proved the published contract exists.

The loader is deliberately unforgiving: a module that imports but omits a name
still fails, so it can never mask a partial implementation.

``ServiceWorldMixin`` builds the provider/customer topology every suite shares and
owns :meth:`ServiceWorldMixin.assert_writes_nothing`, the context manager the
security cases use. Per
``itambox/docs/development/security-test-expectations.md`` a rejection is only
proved by the state afterwards, so the fingerprint compares full row tuples —
not counts — for ``Membership``, ``RoleGrant``, ``RoleGrantScope``, ``User`` and
``ObjectChange``. Counts alone would pass an in-place mutation.
"""

import uuid
from contextlib import contextmanager
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from core.models import ObjectChange
from itambox.middleware import _current_user, _request_id
from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant, TenantGroup

User = get_user_model()

#: The public surface issue #86's design (§4.1–§4.3) publishes, per module. The
#: loader below reports precisely which of these is missing, which is what makes
#: a red run legible before the implementation lands.
SERVICE_SURFACE = {
    "organization.services.errors": (
        "ServiceError",
        "MembershipServiceError",
        "ActorNotAuthorized",
        "CrossTenantObject",
        "EscalationDenied",
        "ElevatedGrantIncomplete",
        "AmbiguousIdentity",
        "DuplicateMembership",
        "ConcurrentGrantChange",
    ),
    "organization.services.rolegrants": (
        "SCOPE_EXPLICIT",
        "OwnGrantSpec",
        "ManagedGrantSpec",
        "GrantPlan",
        "ValidatedGrantPlan",
        "GrantChange",
        "GrantSyncResult",
        "role_assignable_in",
        "assignable_roles_qs",
        "validate_grant_plan",
        "sync_membership_grants",
    ),
    "organization.services.membership": (
        "NewIdentitySpec",
        "MembershipIntent",
        "MembershipWritePlan",
        "MembershipWriteResult",
        "authorize_membership_write",
        "may_manage_memberships",
        "resolve_identity",
        "plan_membership_write",
        "execute_membership_write",
        "apply_membership_grants",
    ),
}


class _ServiceNamespace:
    """Attribute access over the loaded service surface.

    A plain namespace rather than a module alias so a test reads
    ``self.svc.validate_grant_plan(...)`` regardless of which of the three
    modules ends up owning the symbol.
    """

    def __init__(self, members):
        self.__dict__.update(members)


def membership_services():
    """Return the #86 service surface, or fail with the exact missing piece.

    Raises ``AssertionError`` (so unittest reports a failure, not an error) naming
    every module that could not be imported and every documented symbol that is
    absent from one that could.
    """
    # inline import: optional-dependency: the #86 service package is the module
    # under construction; resolving it here keeps a missing service a legible test
    # failure instead of a collection error for the whole suite.
    from importlib import import_module

    members = {}
    problems = []
    for module_name, expected in SERVICE_SURFACE.items():
        try:
            module = import_module(module_name)
        except ImportError as exc:
            problems.append(f"{module_name}: not importable ({exc})")
            continue
        missing = [name for name in expected if not hasattr(module, name)]
        if missing:
            problems.append(f"{module_name}: missing {', '.join(missing)}")
        for name in expected:
            if hasattr(module, name):
                members[name] = getattr(module, name)
    if problems:
        raise AssertionError(
            "The issue #86 membership/RBAC service layer is not available:\n  " + "\n  ".join(problems)
        )
    return _ServiceNamespace(members)


def future(hours=8):
    return timezone.now() + timedelta(hours=hours)


def past(hours=8):
    return timezone.now() - timedelta(hours=hours)


def _rows(model, fields):
    return sorted(model._base_manager.values_list(*fields))


def state_fingerprint():
    """Every row that a membership/grant write could touch, by value.

    Compared for equality by :meth:`ServiceWorldMixin.assert_writes_nothing`.
    Row *tuples* rather than counts: a rejected write that mutated a surviving
    grant's ``reason`` or re-pointed a scope keeps every count identical.
    """
    return {
        "Membership": _rows(Membership, ("pk", "user_id", "tenant_id", "is_active")),
        "RoleGrant": _rows(
            RoleGrant,
            ("pk", "membership_id", "user_group_id", "role_id", "granted_by_id", "granted_at", "reason", "valid_until"),
        ),
        "RoleGrantScope": _rows(
            RoleGrantScope,
            ("pk", "role_grant_id", "scope_type", "tenant_id", "tenant_group_id"),
        ),
        "User": _rows(User, ("pk", "username", "email", "is_active")),
        "ObjectChange": _rows(ObjectChange, ("pk", "action", "changed_object_type_id", "changed_object_id")),
    }


class ServiceWorldMixin:
    """Provider/customer topology, role catalogue, and write assertions.

    ``setup_service_world()`` also arms the change-log contextvars. Without them
    ``ChangeLoggingMixin._log_change`` returns early (``core/models.py``), so an
    "no ``ObjectChange`` was written" assertion would hold vacuously in every
    test and prove nothing about ``INV-8``/``INV-16``.
    """

    def setup_service_world(self, prefix="svc"):
        self.superuser = User.objects.create_superuser(username=f"{prefix}-root")
        self.member = User.objects.create_user(username=f"{prefix}-member")

        # The ambient changelog principal is deliberately the superuser: with an
        # authenticated NON-superuser bound and no active tenant,
        # ``TenantScopingQuerySet.filter_by_tenant`` fails closed to ``.none()``
        # (core/managers.py), which would silently empty the fixtures rather than
        # test anything. Attribution is asserted explicitly where it matters.
        self.request_id = uuid.uuid4()
        _current_user.set(self.superuser)
        _request_id.set(self.request_id)
        self.addCleanup(_request_id.set, None)
        self.addCleanup(_current_user.set, None)

        self.provider = Tenant.objects.create(name=f"{prefix} Provider", slug=f"{prefix}-provider", is_provider=True)
        self.customer_a = Tenant.objects.create(
            name=f"{prefix} Customer A", slug=f"{prefix}-customer-a", managed_by=self.provider
        )
        self.customer_z = Tenant.objects.create(
            name=f"{prefix} Customer Z", slug=f"{prefix}-customer-z", managed_by=self.provider
        )
        #: A second, unrelated provider — the cross-tenant reach cases target it.
        self.rival = Tenant.objects.create(name=f"{prefix} Rival", slug=f"{prefix}-rival", is_provider=True)
        self.rival_customer = Tenant.objects.create(
            name=f"{prefix} Rival Customer", slug=f"{prefix}-rival-customer", managed_by=self.rival
        )

        # ``role_is_privileged`` (core/mfa.py) classifies by canonical name OR any
        # non-``view_`` codename, so "reader" roles below are deliberately
        # view-only and the "editor" role deliberately is not.
        self.read_role = self.make_role("Reader", ["assets.view_asset"])
        self.other_read_role = self.make_role("Second reader", ["assets.view_asset"])
        self.editor_role = self.make_role("Asset editor", ["assets.view_asset", "assets.change_asset"])
        self.rival_role = Role.objects.create(
            tenant=self.rival, name=f"{prefix} rival reader", permissions=["assets.view_asset"]
        )

    def make_role(self, name, permissions, *, tenant=None, shared_with_managed=False):
        return Role.objects.create(
            tenant=tenant or self.provider,
            name=name,
            permissions=list(permissions),
            shared_with_managed=shared_with_managed,
        )

    def make_tenant_group(self, name, slug, *, parent=None):
        return TenantGroup.objects.create(name=name, slug=slug, parent=parent)

    def actor_with(
        self,
        username,
        permissions,
        *,
        tenant=None,
        coverage=(),
        coverage_permissions=(),
        all_managed=False,
    ):
        """An actor holding ``permissions`` in ``tenant``, optionally with managed reach.

        Mirrors ``test_escalation_surface.CanonicalEscalationGuardTests.make_actor``
        so the service suites and the existing guard suite describe the same
        principals.
        """
        # inline import: cycle: core.tests.mixins imports organization.models, which
        # this module's own importers already pull in; keeping it here avoids a
        # test-helper import loop through core.tests.
        from core.tests.mixins import grant

        tenant = tenant or self.provider
        actor = User.objects.create_user(username=username)
        own_role = Role.objects.create(tenant=tenant, name=f"{username} own role", permissions=list(permissions))
        grant(actor, tenant, own_role)
        if coverage or all_managed:
            coverage_role = Role.objects.create(
                tenant=tenant,
                name=f"{username} coverage role",
                permissions=list(coverage_permissions),
            )
            grant(
                actor,
                tenant,
                coverage_role,
                reach=RoleGrant.REACH_MANAGED,
                managed_scope=(RoleGrantScope.SCOPE_ALL_MANAGED if all_managed else RoleGrantScope.SCOPE_TENANT),
                assigned_tenants=list(coverage),
            )
        return actor

    def membership_for(self, user, tenant, *, is_active=True):
        return Membership.objects.create(user=user, tenant=tenant, is_active=is_active)

    def make_user(self, username, **kwargs):
        return User.objects.create_user(username=username, **kwargs)

    # ------------------------------------------------------------ assertions
    @contextmanager
    def assert_writes_nothing(self, reason="the rejected call"):
        """Assert the guarded block left every membership/grant/identity row alone.

        Rejecting is half the contract; the other half is that nothing was
        persisted before the rejection. Both halves are required by
        ``docs/development/security-test-expectations.md``.
        """
        before = state_fingerprint()
        yield
        after = state_fingerprint()
        for model_label, rows in before.items():
            self.assertEqual(
                after[model_label],
                rows,
                f"{reason} must leave {model_label} untouched, but its rows changed",
            )
