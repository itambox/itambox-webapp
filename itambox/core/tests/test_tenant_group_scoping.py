"""TenantGroup is tenant-scoped: a member sees the groups containing a tenant
they belong to, plus those groups' ancestors (path to root) — not descendants,
siblings, or unrelated groups. Superusers see all.

Scoping lives in core.managers.filter_by_tenant; internal machinery (descendant
walk, middleware group resolution) stays on TenantGroup._base_manager.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model

from organization.models import Tenant, TenantGroup, TenantRole, TenantMembership
from core.managers import set_current_tenant
from itambox.middleware import _current_user

User = get_user_model()


class TenantGroupScopingTests(TestCase):
    def setUp(self):
        #   root ── child ── grandchild        (member's tenant lives in `child`)
        #     └─── sibling
        #   unrelated  (separate tree)
        self.root = TenantGroup.objects.create(name='Root', slug='tg-root')
        self.child = TenantGroup.objects.create(name='Child', slug='tg-child', parent=self.root)
        self.grandchild = TenantGroup.objects.create(name='Grandchild', slug='tg-gc', parent=self.child)
        self.sibling = TenantGroup.objects.create(name='Sibling', slug='tg-sib', parent=self.root)
        self.unrelated = TenantGroup.objects.create(name='Unrelated', slug='tg-unrel')

        self.tenant = Tenant.objects.create(name='T', slug='tg-t', group=self.child)
        self.member = User.objects.create_user(username='tgm', password='pw')
        self.superuser = User.objects.create_superuser(username='tgs', email='s@x.com', password='pw')
        role = TenantRole.objects.create(tenant=self.tenant, name='R', permissions=[])
        TenantMembership.objects.create(user=self.member, tenant=self.tenant, role=role)

    def _visible_slugs(self):
        return set(TenantGroup.objects.values_list('slug', flat=True))

    def test_member_sees_only_member_group_and_ancestors(self):
        _current_user.set(self.member)
        set_current_tenant(self.tenant)
        self.assertEqual(self._visible_slugs(), {'tg-child', 'tg-root'})

    def test_superuser_sees_all_groups(self):
        _current_user.set(self.superuser)
        set_current_tenant(self.tenant)
        self.assertEqual(
            self._visible_slugs(),
            {'tg-root', 'tg-child', 'tg-gc', 'tg-sib', 'tg-unrel'},
        )
