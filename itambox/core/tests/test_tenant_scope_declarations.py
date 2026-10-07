"""Contract tests for the explicit tenant scoping declaration (#618)."""

import inspect

from django.apps import apps
from django.test import SimpleTestCase

from core import managers
from core.managers import (
    Scope,
    TenantScopeDeclaration,
    TenantScopingQuerySet,
    tenant_scope_declaration,
)


def _tenant_bearing_models():
    for model in apps.get_models():
        meta = model._meta
        if meta.abstract or meta.proxy:
            continue
        has_field = any(f.name == "tenant" for f in meta.get_fields())
        if has_field or getattr(model, "tenant_lookup", None) or getattr(model, "tenant_scope_self", None):
            yield model


def _scoping_manager_models():
    for model in apps.get_models():
        manager = model._default_manager
        if issubclass(manager._queryset_class, TenantScopingQuerySet):
            yield model


class TenantScopeDeclarationContractTests(SimpleTestCase):
    def test_every_tenant_bearing_model_has_a_non_empty_declaration(self):
        missing = [
            model._meta.label
            for model in _tenant_bearing_models()
            if tenant_scope_declaration(model).strategy == TenantScopeDeclaration.NONE
        ]
        self.assertEqual(missing, [])

    def test_every_scoped_manager_model_has_a_non_empty_declaration(self):
        missing = [
            model._meta.label
            for model in _scoping_manager_models()
            if tenant_scope_declaration(model).strategy == TenantScopeDeclaration.NONE
        ]
        self.assertEqual(missing, [])

    def test_declaration_is_introspectable_and_deterministic(self):
        for model in _tenant_bearing_models():
            first = tenant_scope_declaration(model)
            self.assertEqual(first, tenant_scope_declaration(model))
            if first.strategy == TenantScopeDeclaration.LOOKUP:
                self.assertTrue(first.lookup, model._meta.label)

    def test_self_scoping_models_are_declared_not_named(self):
        Tenant = apps.get_model("organization", "Tenant")
        TenantGroup = apps.get_model("organization", "TenantGroup")
        self.assertEqual(tenant_scope_declaration(Tenant).strategy, TenantScopeDeclaration.SELF_TENANT)
        self.assertEqual(tenant_scope_declaration(TenantGroup).strategy, TenantScopeDeclaration.SELF_GROUP)

    def test_scoping_code_has_no_model_name_special_cases(self):
        source = inspect.getsource(managers.TenantScopingQuerySet)
        for literal in ('"tenant"', '"tenantgroup"', "'tenantgroup'", "_meta.model_name"):
            if literal == '"tenant"':
                self.assertNotIn("model_name == " + literal, source)
            else:
                self.assertNotIn(literal, source)

    def test_scope_kinds_are_closed(self):
        self.assertEqual(
            {Scope.TENANT, Scope.GROUP, Scope.ALL_ACCESSIBLE, Scope.SYSTEM, Scope.DENIED},
            {"tenant", "group", "all_accessible", "system", "denied"},
        )
