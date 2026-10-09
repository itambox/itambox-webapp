"""ObjectChange uses an explicit-scope manager (follow-up to #618, issue #683)."""

import uuid

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from core.managers import ExplicitScopeManager, Scope, set_current_tenant
from core.models import ObjectChange
from core.tests.mixins import grant
from organization.models import Tenant
from users.models import Role

User = get_user_model()


class ObjectChangeExplicitScopeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant_a = Tenant.objects.create(name="OC A", slug="oc-a")
        cls.tenant_b = Tenant.objects.create(name="OC B", slug="oc-b")
        cls.user = User.objects.create_user(username="oc-member", password="x")
        role = Role.objects.create(tenant=cls.tenant_a, name="OC Role", permissions=["core.view_objectchange"])
        grant(cls.user, cls.tenant_a, role)
        cls.ct = ContentType.objects.get_for_model(Tenant)
        cls.row_a = cls._make(cls.tenant_a, "a")
        cls.row_b = cls._make(cls.tenant_b, "b")
        cls.row_global = cls._make(None, "global")

    @classmethod
    def _make(cls, tenant, repr_):
        return ObjectChange.objects.create(
            tenant=tenant,
            user_name="tester",
            request_id=uuid.uuid4(),
            action="update",
            changed_object_type=cls.ct,
            changed_object_id=1,
            object_repr=repr_,
        )

    def test_default_manager_is_explicit(self):
        self.assertIsInstance(ObjectChange.objects, ExplicitScopeManager)
        self.assertTrue(ObjectChange.allow_global_tenant)

    def test_default_manager_has_no_ambient_scope(self):
        set_current_tenant(self.tenant_a)
        pks = set(ObjectChange.objects.values_list("pk", flat=True))
        self.assertEqual(pks, {self.row_a.pk, self.row_b.pk, self.row_global.pk})

    def test_for_scope_tenant_keeps_own_and_global_rows(self):
        scope = Scope(kind=Scope.TENANT, user=self.user, tenant=self.tenant_a)
        pks = set(ObjectChange.objects.for_scope(scope).values_list("pk", flat=True))
        self.assertEqual(pks, {self.row_a.pk, self.row_global.pk})

    def test_for_scope_denied_returns_nothing(self):
        self.assertFalse(ObjectChange.objects.for_scope(Scope(kind=Scope.DENIED)).exists())

    def test_for_scope_system_returns_everything(self):
        self.assertEqual(ObjectChange.objects.for_scope(Scope(kind=Scope.SYSTEM)).count(), 3)

    def test_filter_by_tenant_adapter_matches_for_scope(self):
        set_current_tenant(self.tenant_a)
        self.assertEqual(
            set(ObjectChange.objects.filter_by_tenant().values_list("pk", flat=True)),
            set(ObjectChange.objects.for_scope(Scope.current()).values_list("pk", flat=True)),
        )
