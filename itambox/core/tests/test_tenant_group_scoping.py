"""TenantGroup is tenant-scoped.

Under a single-tenant scope a member sees the groups containing a tenant they
belong to, plus those groups' ancestors (path to root) — not descendants,
siblings, or unrelated groups; superusers see all.

Under an explicit group scope (the "(Group)" switcher) the list is restricted to
the scoped group's subtree (descendants) plus its ancestors — for everyone,
superusers included — mirroring how the Tenant list is restricted to the scoped
group's tenants. Members additionally never see a group they hold no membership
in.

Scoping lives in core.managers.filter_by_tenant; internal machinery (descendant
walk, middleware group resolution) stays on TenantGroup._base_manager.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model

from organization.models import Tenant, TenantGroup, TenantRole, TenantMembership
from core.managers import set_current_tenant, set_current_tenant_group
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

    def test_superuser_group_scope_restricts_to_subtree_and_ancestors(self):
        # Scoping to `child` shows that group, its descendants (grandchild) and
        # its ancestors (root) — but NOT the sibling subtree or the unrelated
        # tree. Without this, a scoped superuser still saw every group.
        _current_user.set(self.superuser)
        set_current_tenant(None)
        set_current_tenant_group(self.child)
        self.assertEqual(self._visible_slugs(), {'tg-child', 'tg-gc', 'tg-root'})

    def test_member_group_scope_excludes_other_member_groups(self):
        # The member also belongs to a tenant in the unrelated tree; without a
        # scope they would see both trees. Scoping to `root` must hide the
        # unrelated group they are a member of.
        unrelated_tenant = Tenant.objects.create(name='U', slug='tg-ut', group=self.unrelated)
        role = TenantRole.objects.create(tenant=unrelated_tenant, name='RU', permissions=[])
        TenantMembership.objects.create(user=self.member, tenant=unrelated_tenant, role=role)

        _current_user.set(self.member)
        set_current_tenant(None)
        set_current_tenant_group(self.root)
        self.assertEqual(self._visible_slugs(), {'tg-child', 'tg-root'})
