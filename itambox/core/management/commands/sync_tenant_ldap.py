import logging
try:
    import ldap
except ImportError:
    import sys
    class DummyLDAP:
        SCOPE_BASE = 0
        SCOPE_ONELEVEL = 1
        SCOPE_SUBTREE = 2
        RES_SEARCH_ENTRY = 100
        OPT_REFERRALS = 2
        OPT_PROTOCOL_VERSION = 4
        class LDAPError(Exception):
            pass
    ldap = DummyLDAP()
    sys.modules['ldap'] = ldap

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from core.managers import set_current_tenant, get_current_tenant
from core.tasks.context import TaskContext
from organization.models import Tenant

logger = logging.getLogger('django_auth_ldap')
User = get_user_model()


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
        # No user_id is available when the command is invoked directly from the
        # CLI (no --user argument). Changes are attributed to a system/anonymous
        # actor (user=None). When called via sync_tenant_ldap_task the outer
        # TaskContext in core/tasks/ldap.py already provides the actor user;
        # the nested TaskContext here is a no-op override that restores correctly
        # on exit thanks to TaskContext's save/restore logic.
        with TaskContext(tenant_id=tenant.pk, user_id=None):
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
            from django_auth_ldap.config import LDAPSearch
        except ImportError:
            raise CommandError("django-auth-ldap is not installed. Run: pip install django-auth-ldap")

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
                        from organization.models import Membership, Role
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
                        membership.roles.add(tenant_role)

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
