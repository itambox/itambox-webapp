import logging

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from core.models import LDAPSettings

logger = logging.getLogger(__name__)
User = get_user_model()


class Command(BaseCommand):
    help = "Sync users from LDAP directory into local Django users"

    def handle(self, *args, **options):
        ldap_config = LDAPSettings.load()
        if not ldap_config or not ldap_config.is_active:
            self.stderr.write(self.style.ERROR("LDAP is not configured or not active."))
            return

        if not ldap_config.server_uri or not ldap_config.bind_dn:
            self.stderr.write(self.style.ERROR("LDAP server_uri and bind_dn are required."))
            return

        try:
            import ldap
            from django_auth_ldap.config import LDAPSearch
        except ImportError:
            self.stderr.write(self.style.ERROR("django-auth-ldap is not installed. Run: pip install django-auth-ldap"))
            return

        conn = ldap.initialize(ldap_config.server_uri)
        conn.set_option(ldap.OPT_REFERRALS, 0)
        conn.set_option(ldap.OPT_PROTOCOL_VERSION, 3)

        try:
            conn.simple_bind_s(ldap_config.bind_dn, ldap_config.bind_password)
        except ldap.LDAPError as e:
            self.stderr.write(self.style.ERROR(f"LDAP bind failed: {e}"))
            return

        search_base = ldap_config.user_search_base
        search_filter = ldap_config.user_search_filter
        if not search_base:
            self.stderr.write(self.style.ERROR("user_search_base is required."))
            conn.unbind_s()
            return

        if '%(user)s' in search_filter:
            search_filter = search_filter.replace('%(user)s', '*')

        scope = ldap.SCOPE_SUBTREE
        retrieve_attrs = ['uid', 'cn', 'sn', 'givenName', 'mail', 'memberOf']

        try:
            result_id = conn.search(search_base, scope, search_filter, retrieve_attrs)
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

                        if ldap_config.require_group:
                            member_of = []
                            for v in entry.get('memberOf', []):
                                val = v.decode('utf-8') if isinstance(v, bytes) else v
                                member_of.append(val)
                            if ldap_config.require_group not in member_of:
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
                        if created:
                            created_count += 1
                            self.stdout.write(self.style.SUCCESS(f"Created user: {username}"))
                        else:
                            updated_count += 1
                            self.stdout.write(f"Updated user: {username}")

            self.stdout.write(self.style.SUCCESS(
                f"LDAP sync complete. Created: {created_count}, Updated: {updated_count}"
            ))

        except ldap.LDAPError as e:
            self.stderr.write(self.style.ERROR(f"LDAP search failed: {e}"))
        finally:
            conn.unbind_s()
