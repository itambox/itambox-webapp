"""Access seed mixin: users, per-tenant roles, memberships, and grants.

Designed to be mixed into ``Command`` in seed_data.py:

    from core.management.commands._seed.access import SeedAccessMixin

    class Command(SeedAccessMixin, BaseCommand):
        ...

``_seed_access`` must run after ``_seed_organizations`` (it reads
``self._tenants`` / ``self._tenant_meta`` / ``self._tenant_holders`` /
``self._provider_tenant`` / ``self._orgs``). It populates ``self._roles``,
``self._users``, ``self._engineer_users`` and ``self._provisioner`` (consumed
by every later phase that needs an acting user).

Grant shape (per-grant RBAC): a person is anchored to a tenant by ONE
``Membership``; everything they may do is carried by ``RoleGrant`` rows and
their additive ``RoleGrantScope`` children. MSP staff hold a single membership
at the managing (``is_provider``) tenant; scopes project their grants into
managed tenants, never a second membership.
"""

from datetime import timedelta
import random

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.utils import timezone

from core.mfa import role_is_privileged

User = get_user_model()

SEED_PASSWORD = 'itambox2026'


class SeedAccessMixin:
    """Mixin for Command(BaseCommand).  Reads/writes self._ registries."""

    def _seed_access(self):
        from organization.models import Membership, Role, RoleGrant, RoleGrantScope

        self.stdout.write('--- Access: users, roles, role grants ---')
        seed_grant_expiry = timezone.now() + timedelta(days=3650)

        def grant(
            user,
            tenant,
            role,
            *,
            granted_by=None,
            scope_type=RoleGrantScope.SCOPE_OWN,
            scoped_tenants=None,
            reason='',
        ):
            """Create a membership-backed grant and its requested additive scopes."""
            membership, _ = Membership.objects.get_or_create(user=user, tenant=tenant)
            privileged = role_is_privileged(role)
            if privileged and not reason.strip():
                raise ValueError(f'Privileged seed grant {role} requires a reason.')

            role_grant = RoleGrant.objects.filter(
                membership=membership,
                role=role,
            ).order_by('pk').first()
            if role_grant is None:
                role_grant = RoleGrant(
                    membership=membership,
                    role=role,
                    granted_by=granted_by,
                    reason=reason,
                    valid_until=seed_grant_expiry if privileged else None,
                )
                role_grant.full_clean()
                role_grant.save()
            elif privileged:
                # ``--skip-drop`` refreshes seeded elevated grants to an
                # explicitly justified, future-dated state.
                role_grant.granted_by = granted_by
                role_grant.reason = reason
                role_grant.valid_until = seed_grant_expiry
                role_grant.full_clean()
                role_grant.save(update_fields=['granted_by', 'reason', 'valid_until'])

            if scope_type == RoleGrantScope.SCOPE_TENANT:
                if not scoped_tenants:
                    raise ValueError('A tenant-scoped seed grant requires at least one tenant.')
                scope_specs = [
                    {'scope_type': scope_type, 'tenant': scoped_tenant}
                    for scoped_tenant in (scoped_tenants or ())
                ]
            else:
                scope_specs = [{'scope_type': scope_type}]

            for scope_spec in scope_specs:
                if RoleGrantScope.objects.filter(role_grant=role_grant, **scope_spec).exists():
                    continue
                scope = RoleGrantScope(role_grant=role_grant, **scope_spec)
                scope.full_clean()
                scope.save()
            return role_grant

        # Build permission catalogs from Django's permission table.
        all_perms = [
            permission
            for permission in Permission.objects.select_related('content_type')
            if permission.content_type.model_class() is not None
        ]

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

        # Per-tenant local roles: every tenant owns its own four role definitions.
        self._roles = {}  # (tenant_slug, role_name) -> Role
        for slug, tenant in self._tenants.items():
            for role_name, perms in ROLE_PERMS.items():
                role, _ = Role.objects.get_or_create(
                    tenant=tenant, name=role_name,
                    defaults={'permissions': perms, 'description': f'{role_name} role for {tenant.name}'})
                self._roles[(slug, role_name)] = role

        # MSP-owned shared roles. The managing tenant additionally owns "MSP Technician"
        # and shares it (plus its local "Read-Only") with the managed tenants: managed
        # tenants may assign these to their own members, only the MSP can edit them.
        # There is no separate provider-admin role any more — the engineers' MSP-wide
        # power (tenant/membership/group CRUD) is the MSP tenant's local Administrator.
        msp_tenant = self._provider_tenant
        msp_slug = msp_tenant.slug
        msp_tech_role, _ = Role.objects.get_or_create(
            tenant=msp_tenant, name='MSP Technician',
            defaults={'permissions': TECH, 'shared_with_managed': True,
                      'description': 'Operate managed customer tenants. Shared definition: '
                                     'managed tenants may assign it, only the MSP edits it.'})
        self._roles[(msp_slug, 'MSP Technician')] = msp_tech_role
        shared_readonly_role = self._roles[(msp_slug, 'Read-Only')]
        for shared_role in (msp_tech_role, shared_readonly_role):
            if not shared_role.shared_with_managed:
                shared_role.shared_with_managed = True
                shared_role.save(update_fields=['shared_with_managed'])

        # MSP staff (login users). (username, full_name, kind, assigned_group_slugs or None=all)
        # ONE membership at the MSP tenant per person; reach into managed tenants is a
        # scoped RoleGrant (no per-customer-tenant staff memberships).
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
        own_role_for_kind = {'engineer': 'Administrator', 'helpdesk': 'Technician', 'account': 'Read-Only'}
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

            # Local power inside the MSP tenant itself (engineers = full Administrator).
            grant(
                user,
                msp_tenant,
                self._roles[(msp_slug, own_role_for_kind[kind])],
                reason=f'Demo seed: {kind} access in the provider tenant.',
            )

            # Reach into the managed tenants.
            if kind == 'account':
                # Account managers: read-only, limited to their assigned customer groups.
                assigned = [t for slug, t in self._tenants.items()
                            if self._tenant_meta[slug]['group_slug'] in group_scope]
                grant(user, msp_tenant, shared_readonly_role,
                      scope_type=RoleGrantScope.SCOPE_TENANT,
                      scoped_tenants=assigned,
                      reason='Demo seed: account access to assigned managed tenants.')
            else:
                # Engineers and helpdesk operate every managed tenant as technicians.
                grant(user, msp_tenant, msp_tech_role,
                      scope_type=RoleGrantScope.SCOPE_ALL_MANAGED,
                      reason=f'Demo seed: {kind} access to all managed tenants.')

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
                grant(user, self._tenants[slug], self._roles[(slug, 'Administrator')],
                      granted_by=self._provisioner,
                      reason='Demo seed: customer administrator access.')
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
                grant(user, tenant, self._roles[(slug, role_name)],
                      granted_by=self._provisioner,
                      reason=f'Demo seed: {role_name} access for a customer user.')
                if role_name == 'Asset Manager':
                    team_leads += 1
                else:
                    end_users += 1

        managed_count = sum(1 for t in self._tenants.values()
                            if t.managed_by_id == msp_tenant.pk)
        staff_reach = RoleGrant.objects.filter(
            membership__tenant=msp_tenant,
            scopes__scope_type__in=(
                RoleGrantScope.SCOPE_TENANT,
                RoleGrantScope.SCOPE_TENANT_GROUP,
                RoleGrantScope.SCOPE_ALL_MANAGED,
            ),
        ).distinct().count()
        self.stdout.write(f'  MSP layer: 1 managing tenant, {managed_count} managed tenants, '
                          f'{staff_reach} managed-scope staff grants.')

        total_memberships = Membership.objects.count()
        total_grants = RoleGrant.objects.count()
        self.stdout.write(f'  {team_leads} single-tenant team leads (Asset Manager), '
                          f'{end_users} single-tenant read-only end users.')
        self.stdout.write(f'  {len(self._roles)} roles, {len(self._users)} login users '
                          f'({customer_admins} customer admins), {total_memberships} memberships, '
                          f'{total_grants} role grants.')
