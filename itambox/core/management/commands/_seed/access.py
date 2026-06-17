"""Access seed mixin: users, per-tenant roles, cross-tenant memberships.

Designed to be mixed into ``Command`` in seed_data.py:

    from core.management.commands._seed.access import SeedAccessMixin

    class Command(SeedAccessMixin, BaseCommand):
        ...

``_seed_access`` must run after ``_seed_organizations`` (it reads
``self._tenants`` / ``self._tenant_meta`` / ``self._tenant_holders`` /
``self._orgs``). It populates ``self._roles``, ``self._users``,
``self._engineer_users`` and ``self._provisioner`` (consumed by every later
phase that needs an acting user).
"""

import random

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission

User = get_user_model()

SEED_PASSWORD = 'itambox2026'


class SeedAccessMixin:
    """Mixin for Command(BaseCommand).  Reads/writes self._ registries."""

    def _seed_access(self):
        from organization.models import TenantRole, TenantMembership
        self.stdout.write('--- Access: users, roles, memberships ---')

        # Build permission catalogs from Django's permission table.
        all_perms = list(Permission.objects.select_related('content_type').all())

        def perm_str(p):
            return f"{p.content_type.app_label}.{p.codename}"

        op_apps = {'assets', 'inventory', 'organization', 'compliance', 'licenses',
                   'subscriptions', 'software', 'procurement', 'extras', 'core'}
        ADMIN = [perm_str(p) for p in all_perms]
        TECH = [perm_str(p) for p in all_perms
                if p.content_type.app_label in op_apps and not p.codename.startswith('delete_')]
        ASSETMGR = [perm_str(p) for p in all_perms
                    if p.content_type.app_label in {'assets', 'inventory', 'compliance', 'organization', 'procurement'}
                    and p.codename.split('_')[0] in {'view', 'add', 'change'}]
        READONLY = [perm_str(p) for p in all_perms if p.codename.startswith('view_')]
        ROLE_PERMS = {'Administrator': ADMIN, 'Technician': TECH, 'Asset Manager': ASSETMGR, 'Read-Only': READONLY}

        # Per-tenant roles
        self._roles = {}  # (tenant_slug, role_name) -> TenantRole
        for slug, tenant in self._tenants.items():
            for role_name, perms in ROLE_PERMS.items():
                role, _ = TenantRole.objects.get_or_create(
                    tenant=tenant, name=role_name,
                    defaults={'permissions': perms, 'description': f'{role_name} role for {tenant.name}'})
                self._roles[(slug, role_name)] = role

        # MSP staff (login users). (username, full_name, kind, assigned_group_slugs or None=all)
        self._users = {}
        self._engineer_users = []
        msp_domain = 'northwind-it.com'
        staff = [
            ('lars.eklund', 'Lars Eklund', 'engineer', None),     # Lead infra engineer
            ('deepa.rao', 'Deepa Rao', 'engineer', None),         # Senior engineer
            ('tom.berger', 'Tom Berger', 'engineer', None),       # Field engineer
            ('sara.lind', 'Sara Lind', 'engineer', None),         # Field engineer
            ('ravi.anand', 'Ravi Anand', 'helpdesk', None),       # Service desk L1
            ('mia.koch', 'Mia Koch', 'helpdesk', None),           # Service desk L1
            ('nadia.haas', 'Nadia Haas', 'account', ['helix-biopharma', 'sterling-am']),
            ('peter.voss', 'Peter Voss', 'account', ['meridian-bank']),
        ]
        role_for_kind = {'engineer': 'Administrator', 'helpdesk': 'Technician', 'account': 'Read-Only'}
        for username, full_name, kind, group_scope in staff:
            first, last = full_name.split(' ', 1)
            user, created = User.objects.get_or_create(username=username, defaults={
                'email': f'{username}@{msp_domain}', 'first_name': first, 'last_name': last,
                'is_staff': False, 'is_superuser': False})
            if created:
                user.set_password(SEED_PASSWORD)
                user.save()
            self._users[username] = user
            if kind == 'engineer':
                self._engineer_users.append(user)
            role_name = role_for_kind[kind]
            for slug, tenant in self._tenants.items():
                meta = self._tenant_meta[slug]
                # Account managers are scoped to their assigned customer groups; others span all tenants.
                if group_scope is not None and meta['group_slug'] not in group_scope:
                    continue
                TenantMembership.objects.get_or_create(
                    user=user, tenant=tenant, defaults={'role': self._roles[(slug, role_name)]})

        if not self._engineer_users:
            self._engineer_users = list(self._users.values())
        self._provisioner = self._engineer_users[0]

        # One customer-admin login per customer org, scoped to their own tenants.
        customer_admins = 0
        for org in self._orgs:
            if org['kind'] != 'customer':
                continue
            domain = org['domain']
            label = org['group'][0] if org['group'] else org['tenants'][0]['name']
            username = f"admin@{domain}"
            user, created = User.objects.get_or_create(username=username, defaults={
                'email': username, 'first_name': 'IT', 'last_name': f'Admin ({label})',
                'is_staff': False, 'is_superuser': False})
            if created:
                user.set_password(SEED_PASSWORD)
                user.save()
            self._users[username] = user
            customer_admins += 1
            for t in org['tenants']:
                slug = t['slug']
                TenantMembership.objects.get_or_create(
                    user=user, tenant=self._tenants[slug], defaults={'role': self._roles[(slug, 'Administrator')]})
                # Link this login to a holder profile in their first tenant.
                holders = self._tenant_holders.get(slug, [])
                if holders and holders[0].user_id is None:
                    holders[0].user = user
                    holders[0].save(update_fields=['user'])

        # Realistic permission spread: the vast majority of customer logins are NOT
        # admins. Per tenant we promote one existing holder to a single-tenant
        # "Asset Manager" (team lead) and a few more to single-tenant "Read-Only"
        # (self-service end users). They log in with their own holder identity.
        team_leads = 0
        end_users = 0
        for slug, tenant in self._tenants.items():
            if self._tenant_meta[slug]['kind'] == 'msp':
                continue  # MSP staff are handled above
            holders = [h for h in self._tenant_holders.get(slug, []) if h.user_id is None]
            if not holders:
                continue
            # 1 team lead (Asset Manager), scoped to this tenant only.
            lead = holders[0]
            scoped_logins = [(lead, 'Asset Manager')]
            # 2-3 read-only self-service users, scoped to this tenant only.
            for h in holders[1:1 + random.randint(2, 3)]:
                scoped_logins.append((h, 'Read-Only'))
            for holder, role_name in scoped_logins:
                username = holder.upn  # email-style UPN as the login
                user, created = User.objects.get_or_create(username=username, defaults={
                    'email': holder.email or username, 'first_name': holder.first_name,
                    'last_name': holder.last_name, 'is_staff': False, 'is_superuser': False})
                if created:
                    user.set_password(SEED_PASSWORD)
                    user.save()
                self._users[username] = user
                holder.user = user
                holder.save(update_fields=['user'])
                TenantMembership.objects.get_or_create(
                    user=user, tenant=tenant, defaults={'role': self._roles[(slug, role_name)]})
                if role_name == 'Asset Manager':
                    team_leads += 1
                else:
                    end_users += 1

        total_memberships = TenantMembership.objects.count()
        self.stdout.write(f'  {team_leads} single-tenant team leads (Asset Manager), '
                          f'{end_users} single-tenant read-only end users.')
        self.stdout.write(f'  {len(self._roles)} tenant roles, {len(self._users)} login users '
                          f'({customer_admins} customer admins), {total_memberships} memberships.')
