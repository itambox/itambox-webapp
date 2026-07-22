import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from core.auth.ldap import django_auth_ldap_installed, ldap
from core.mfa import role_is_privileged
from core.tasks.context import TaskContext
from itambox.middleware import get_current_user
from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant

logger = logging.getLogger('django_auth_ldap')
User = get_user_model()

LDAP_GRANT_REASON = 'LDAP directory synchronization'
LDAP_PRIVILEGED_GRANT_LIFETIME = timedelta(days=1)


def _require_real_ldap_backend():
    if not django_auth_ldap_installed:
        raise ImportError


def _ensure_ldap_role_grant(membership, role):
    """Ensure LDAP's own-scope role without taking ownership of manual grants."""
    now = timezone.now()
    desired_valid_until = (
        now + LDAP_PRIVILEGED_GRANT_LIFETIME
        if role_is_privileged(role)
        else None
    )

    # LDAP owns a grant only when both durable markers match exactly. The outer
    # TaskContext actor is intentionally change-log attribution, not granted_by:
    # granted_by=None distinguishes this system-managed row from a manual grant.
    ldap_owned = list(
        membership.role_grants.filter(
            role=role,
            reason=LDAP_GRANT_REASON,
            granted_by__isnull=True,
        ).order_by('pk')
    )
    if len(ldap_owned) == 1:
        grant = ldap_owned[0]
        if grant.valid_until != desired_valid_until:
            grant.valid_until = desired_valid_until
            grant.full_clean()
            grant.save(update_fields=['valid_until'])
        RoleGrantScope.objects.get_or_create(
            role_grant=grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )
        return grant

    equivalent_grants = membership.role_grants.filter(
        role=role,
        scopes__scope_type=RoleGrantScope.SCOPE_OWN,
    ).distinct()
    active_equivalent = equivalent_grants.filter(
        Q(valid_until__isnull=True) | Q(valid_until__gt=now)
    )

    # An active manual grant already supplies the requested access. Multiple
    # LDAP-marked rows are also left alone: choosing one would be ambiguous.
    if active_equivalent.exists():
        return active_equivalent.order_by('pk').first()

    grant = RoleGrant(
        membership=membership,
        role=role,
        reason=LDAP_GRANT_REASON,
        valid_until=desired_valid_until,
        granted_by=None,
    )
    grant.full_clean()
    grant.save()
    RoleGrantScope.objects.create(
        role_grant=grant,
        scope_type=RoleGrantScope.SCOPE_OWN,
    )
    return grant


class Command(BaseCommand):
    help = "Sync users from LDAP directory into local Django users for a specific tenant scope"

    def add_arguments(self, parser):
        parser.add_argument(
            '--tenant',
            required=True,
            help='Slug of the tenant to sync users for'
        )

    def handle(self, *args, **options):
        tenant_slug = options['tenant']
        try:
            tenant = Tenant.objects.get(slug=tenant_slug)
        except Tenant.DoesNotExist:
            raise CommandError(f"Tenant with slug '{tenant_slug}' does not exist.")

        # TaskContext sets the tenant scope AND wires _request_id + _current_user
        # so that ChangeLoggingMixin records ObjectChange entries for all User/
        # Membership saves that happen during the sync.
        # Direct CLI execution has no actor and remains system-attributed. When
        # called from sync_tenant_ldap_task, carry its actor into this nested
        # context instead of replacing it with user=None.
        outer_actor = get_current_user()
        with TaskContext(
            tenant_id=tenant.pk,
            user_id=getattr(outer_actor, 'pk', None),
        ):
            self._run_sync(tenant)

    def _run_sync(self, tenant):
        self.stdout.write(self.style.NOTICE(f"Scoping LDAP synchronization to tenant: {tenant.name} ({tenant.slug})"))

        # Retrieve configurations from settings
        tenant_configs = getattr(settings, 'ITAMBOX_TENANT_LDAP_CONFIGS', {})
        config = tenant_configs.get(tenant.slug)

        if not config:
            raise CommandError(f"No LDAP configuration found for tenant slug '{tenant.slug}' in settings.")

        server_uri = config.get('SERVER_URI') or config.get('server_uri')
        bind_dn = config.get('BIND_DN') or config.get('bind_dn')
        bind_password = config.get('BIND_PASSWORD') or config.get('bind_password')
        user_search_base = config.get('USER_SEARCH_BASE') or config.get('user_search_base')
        user_search_filter = config.get('USER_SEARCH_FILTER') or config.get('user_search_filter')

        # Fallback to nested dict USER_SEARCH if base or filter are not directly configured
        if not user_search_base or not user_search_filter:
            user_search = config.get('USER_SEARCH') or config.get('user_search')
            if user_search and isinstance(user_search, dict):
                if not user_search_base:
                    user_search_base = user_search.get('base_dn') or user_search.get('base')
                if not user_search_filter:
                    user_search_filter = user_search.get('filter')

        if not user_search_filter:
            user_search_filter = '(uid=%(user)s)'
        require_group = config.get('REQUIRE_GROUP') or config.get('require_group')

        if not server_uri or not bind_dn:
            raise CommandError("LDAP server_uri and bind_dn are required in the configuration.")

        if not user_search_base:
            raise CommandError("LDAP user_search_base is required in the configuration.")

        try:
            _require_real_ldap_backend()
        except ImportError:
            raise CommandError(
                "django-auth-ldap is unavailable. Use the locked Linux/WSL or Docker environment; "
                "native Windows does not support LDAP synchronization."
            ) from None

        self.stdout.write(f"Connecting to LDAP server: {server_uri}...")
        conn = ldap.initialize(server_uri)
        conn.set_option(ldap.OPT_REFERRALS, 0)
        conn.set_option(ldap.OPT_PROTOCOL_VERSION, 3)

        try:
            conn.simple_bind_s(bind_dn, bind_password)
            self.stdout.write(self.style.SUCCESS("LDAP bind successful."))
        except ldap.LDAPError as e:
            raise CommandError(f"LDAP bind failed: {e}")

        # Resolve wildcards for bulk synchronization search
        search_filter = user_search_filter
        if '%(user)s' in search_filter:
            search_filter = search_filter.replace('%(user)s', '*')

        scope = ldap.SCOPE_SUBTREE
        retrieve_attrs = ['uid', 'cn', 'sn', 'givenName', 'mail', 'memberOf']

        try:
            result_id = conn.search(user_search_base, scope, search_filter, retrieve_attrs)
            created_count = 0
            updated_count = 0

            while True:
                result_type, result_data = conn.result(result_id, 0)
                if not result_data:
                    break
                if result_type == ldap.RES_SEARCH_ENTRY:
                    for dn, entry in result_data:
                        uid_vals = entry.get('uid', [])
                        mail_vals = entry.get('mail', [])
                        cn_vals = entry.get('cn', [])
                        sn_vals = entry.get('sn', [])
                        gn_vals = entry.get('givenName', [])

                        if not uid_vals:
                            continue

                        username = uid_vals[0].decode('utf-8') if isinstance(uid_vals[0], bytes) else uid_vals[0]
                        email = mail_vals[0].decode('utf-8') if mail_vals and isinstance(mail_vals[0], bytes) else (mail_vals[0] if mail_vals else '')
                        first_name = gn_vals[0].decode('utf-8') if gn_vals and isinstance(gn_vals[0], bytes) else (gn_vals[0] if gn_vals else '')
                        last_name = sn_vals[0].decode('utf-8') if sn_vals and isinstance(sn_vals[0], bytes) else (sn_vals[0] if sn_vals else '')
                        if not last_name and cn_vals:
                            last_name = cn_vals[0].decode('utf-8') if isinstance(cn_vals[0], bytes) else cn_vals[0]

                        # Filter by group membership if specified
                        if require_group:
                            member_of = []
                            for v in entry.get('memberOf', []):
                                val = v.decode('utf-8') if isinstance(v, bytes) else v
                                member_of.append(val)
                            if require_group not in member_of:
                                continue

                        user, created = User.objects.update_or_create(
                            username=username,
                            defaults={
                                'email': email,
                                'first_name': first_name,
                                'last_name': last_name,
                                'is_active': True,
                            }
                        )

                        # Add user to tenant membership as member by default
                        tenant_role, _ = Role.objects.get_or_create(
                            tenant=tenant,
                            name='Member',
                            defaults={
                                'description': 'Default Standard Member',
                                'permissions': [
                                    'assets.view_asset', 'assets.add_asset', 'assets.change_asset',
                                    'inventory.view_accessory', 'inventory.add_accessory', 'inventory.change_accessory',
                                    'inventory.view_consumable', 'inventory.add_consumable', 'inventory.change_consumable',
                                    'inventory.view_kit', 'inventory.add_kit', 'inventory.change_kit',
                                    'inventory.view_component', 'inventory.add_component', 'inventory.change_component',
                                    'organization.view_location', 'organization.add_location', 'organization.change_location',
                                    'organization.view_site', 'organization.add_site', 'organization.change_site',
                                    'organization.view_assetholder', 'organization.add_assetholder', 'organization.change_assetholder',
                                    'extras.view_dashboard', 'extras.add_dashboard', 'extras.change_dashboard',
                                ]
                            }
                        )
                        membership, _ = Membership.objects.get_or_create(
                            user=user,
                            tenant=tenant,
                        )
                        _ensure_ldap_role_grant(membership, tenant_role)

                        if created:
                            created_count += 1
                            self.stdout.write(self.style.SUCCESS(f"Created user: {username}"))
                        else:
                            updated_count += 1
                            self.stdout.write(f"Updated user: {username}")

            self.stdout.write(self.style.SUCCESS(
                f"LDAP sync complete for tenant '{tenant.slug}'. Created: {created_count}, Updated: {updated_count}"
            ))

        except ldap.LDAPError as e:
            raise CommandError(f"LDAP search failed: {e}")
        finally:
            # Tenant context cleanup is handled by TaskContext.__exit__; do NOT
            # call set_current_tenant(None) here as it would fire before TaskContext
            # restores the previous context, breaking nested invocations.
            conn.unbind_s()
