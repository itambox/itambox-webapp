"""Phase 0 preparatory-cleanup tests (RBAC MSP redesign).

Covers:
  - ``validate_permission_grant`` escalation guard (core.auth.guards)
  - ``TokenPermissions.has_object_permission`` now resolves against the object's tenant
  - the ``validate_role_permissions`` management command
"""
from io import StringIO
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import RequestFactory, TestCase

from core.auth.guards import validate_permission_grant
from core.managers import set_current_tenant, set_current_membership
from itambox.api.permissions import TokenPermissions
from organization.models import Tenant, Membership, Role

User = get_user_model()


def _tenant(name, slug):
    return Tenant.objects.create(name=name, slug=slug)


def _role(tenant, name, perms=None):
    return Role.objects.create(tenant=tenant, name=name, permissions=perms or [])


def _user(username):
    return User.objects.create_user(username=username, email=f"{username}@e.com", password="pw")


def _superuser(username):
    return User.objects.create_superuser(username=username, email=f"{username}@e.com", password="pw")


def _membership(user, tenant, roles=None):
    m = Membership.objects.create(person_type=Membership.PERSON_MEMBER, user=user, tenant=tenant, is_active=True)
    if roles:
        m.roles.set(roles)
    return m


class _Ctx:
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def _flush(self, user):
        for attr in list(user.__dict__):
            if (attr.startswith('_perms_tenant_') or attr.startswith('_tenant_membership_')
                    or attr == '_global_caps_cache'):
                delattr(user, attr)


class ValidatePermissionGrantTests(_Ctx, TestCase):
    def setUp(self):
        super().setUp()
        self.t = _tenant("G", "g")
        self.reader = _user("reader")
        _membership(self.reader, self.t, roles=[_role(self.t, "Reader", ["assets.view_asset"])])
        self._flush(self.reader)

    def test_superuser_bypasses(self):
        su = _superuser("su")
        # No raise even for perms nobody holds.
        validate_permission_grant(su, ["assets.delete_asset"], self.t)

    def test_none_user_is_noop(self):
        validate_permission_grant(None, ["assets.delete_asset"], self.t)

    def test_empty_permissions_is_noop(self):
        validate_permission_grant(self.reader, [], self.t)

    def test_held_permission_allowed(self):
        # reader holds view_asset in t -> granting it is fine.
        validate_permission_grant(self.reader, ["assets.view_asset"], self.t)

    def test_unheld_permission_raises(self):
        with self.assertRaises(ValidationError) as cm:
            validate_permission_grant(self.reader, ["assets.delete_asset"], self.t)
        self.assertIn("escalation", str(cm.exception).lower())

    def test_none_tenant_denies_nonsuperuser(self):
        with self.assertRaises(ValidationError):
            validate_permission_grant(self.reader, ["assets.view_asset"], None)

    def test_evaluated_per_tenant(self):
        # reader holds view_asset only in t, not in other.
        other = _tenant("Other", "other")
        with self.assertRaises(ValidationError):
            validate_permission_grant(self.reader, ["assets.view_asset"], other)


class TokenObjectPermissionTests(_Ctx, TestCase):
    """has_object_permission must resolve perms against the OBJECT's tenant, not ambient."""

    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        self.ta = _tenant("TA", "ta")
        self.tb = _tenant("TB", "tb")
        self.user = _user("apiuser")
        _membership(self.user, self.ta, roles=[_role(self.ta, "Viewer", ["organization.view_role"])])
        # Objects in each tenant (Role is tenant-scoped and exposes .tenant).
        self.obj_a = _role(self.ta, "ObjA")
        self.obj_b = _role(self.tb, "ObjB")
        self._flush(self.user)

    def _check(self, obj):
        perm = TokenPermissions()
        request = self.factory.get('/')
        request.user = self.user
        view = SimpleNamespace(queryset=Role._base_manager.all())
        return perm.has_object_permission(request, view, obj)

    def test_object_in_member_tenant_allowed(self):
        self.assertTrue(self._check(self.obj_a))

    def test_object_in_foreign_tenant_denied(self):
        # Without obj=obj this would have leaked from ambient context; with obj it
        # resolves to tenant B where the user is not a member -> denied.
        self._flush(self.user)
        self.assertFalse(self._check(self.obj_b))


class ValidateRolePermissionsCommandTests(_Ctx, TestCase):
    def setUp(self):
        super().setUp()
        self.t = _tenant("Cmd", "cmd")

    def test_stale_codename_reported_and_exit_nonzero(self):
        _role(self.t, "Bad", ["assets.view_asset", "assets.bogus_codename"])
        out = StringIO()
        with self.assertRaises(SystemExit):
            call_command('validate_role_permissions', stdout=out, stderr=StringIO())
        output = out.getvalue()
        self.assertIn("assets.bogus_codename", output)
        self.assertIn("stale", output.lower())

    def test_all_valid_reports_success(self):
        _role(self.t, "Good", ["assets.view_asset", "assets.add_asset"])
        out = StringIO()
        call_command('validate_role_permissions', stdout=out, stderr=StringIO())
        self.assertIn("valid", out.getvalue().lower())
