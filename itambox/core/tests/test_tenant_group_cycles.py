"""TenantGroup hierarchy cycle safety.

Two layers under test:
  * prevention — ``TenantGroup.clean()`` rejects a parent edge that would
    close a cycle (self-parent or any multi-hop loop);
  * tolerance  — the descendant walk inside
    ``TenantScopingQuerySet.filter_by_tenant`` terminates even when a cycle
    already exists in the data (seeded via ``.update()``, which bypasses
    validation), instead of hanging every group-scoped request.
"""
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.managers import set_current_tenant_group
from core.tests.mixins import TenantTestMixin
from organization.models import Location, Site, Tenant, TenantGroup


class TenantGroupCycleValidationTests(TenantTestMixin, TestCase):
    def test_self_parent_rejected(self):
        group = TenantGroup.objects.create(name='Root', slug='root')
        group.parent = group
        with self.assertRaises(ValidationError):
            group.clean()

    def test_two_node_cycle_rejected(self):
        a = TenantGroup.objects.create(name='A', slug='a')
        b = TenantGroup.objects.create(name='B', slug='b', parent=a)
        a.parent = b
        with self.assertRaises(ValidationError):
            a.clean()

    def test_three_node_cycle_rejected(self):
        a = TenantGroup.objects.create(name='A3', slug='a3')
        b = TenantGroup.objects.create(name='B3', slug='b3', parent=a)
        c = TenantGroup.objects.create(name='C3', slug='c3', parent=b)
        a.parent = c
        with self.assertRaises(ValidationError):
            a.clean()

    def test_valid_reparent_passes(self):
        a = TenantGroup.objects.create(name='A4', slug='a4')
        b = TenantGroup.objects.create(name='B4', slug='b4', parent=a)
        c = TenantGroup.objects.create(name='C4', slug='c4', parent=a)
        c.parent = b
        c.clean()  # must not raise

    def test_save_path_enforces_cycle_guard(self):
        # The global pre_save validator runs clean() on every ChangeLogging
        # save, so a cycle cannot be persisted through the ORM save path.
        a = TenantGroup.objects.create(name='A5', slug='a5')
        b = TenantGroup.objects.create(name='B5', slug='b5', parent=a)
        a.parent = b
        with self.assertRaises(ValidationError):
            a.save()


class TenantGroupCycleTraversalTests(TenantTestMixin, TestCase):
    def test_descendant_walk_terminates_on_cycled_data(self):
        a = TenantGroup.objects.create(name='CycA', slug='cyc-a')
        b = TenantGroup.objects.create(name='CycB', slug='cyc-b', parent=a)
        tenant = Tenant.objects.create(name='Cycled Tenant', slug='cycled-tenant', group=a)
        site = Site.objects.create(name='Cyc Site', slug='cyc-site', tenant=tenant)
        Location.objects.create(name='Cyc Loc', slug='cyc-loc', site=site, tenant=tenant)

        # Seed the cycle behind validation's back (bad legacy data).
        TenantGroup._base_manager.filter(pk=a.pk).update(parent=b)

        set_current_tenant_group(a)
        try:
            # Evaluating any tenant-scoped queryset under a group scope runs
            # the descendant walk; before the fix this never returned.
            names = set(Location.objects.values_list('name', flat=True))
            assert 'Cyc Loc' in names
        finally:
            set_current_tenant_group(None)

    def test_descendant_walk_still_finds_subtree(self):
        root = TenantGroup.objects.create(name='TreeRoot', slug='tree-root')
        child = TenantGroup.objects.create(name='TreeChild', slug='tree-child', parent=root)
        tenant = Tenant.objects.create(name='Leaf Tenant', slug='leaf-tenant', group=child)
        site = Site.objects.create(name='Leaf Site', slug='leaf-site', tenant=tenant)
        Location.objects.create(name='Leaf Loc', slug='leaf-loc', site=site, tenant=tenant)

        set_current_tenant_group(root)
        try:
            names = set(Location.objects.values_list('name', flat=True))
            assert 'Leaf Loc' in names
        finally:
            set_current_tenant_group(None)
