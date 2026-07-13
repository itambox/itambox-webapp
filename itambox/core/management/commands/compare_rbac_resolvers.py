"""Exhaustively compare legacy RBAC with phase-5 RoleGrant resolution."""
import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from organization.access import legacy_accessible_tenant_ids
from organization.models import Tenant
from organization.rbac import (
    legacy_effective_permissions,
    new_accessible_tenant_ids,
    new_effective_permissions,
)


class Command(BaseCommand):
    help = (
        'Compare legacy RoleAssignment/UserGroup M2Ms with RoleGrant resolution. '
        'Exits non-zero on every disagreement unless --allow-differences is set.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--user-id', type=int)
        parser.add_argument('--tenant-id', type=int)
        parser.add_argument('--json', action='store_true', dest='as_json')
        parser.add_argument('--summary-only', action='store_true')
        parser.add_argument('--allow-differences', action='store_true')

    def handle(self, *args, **options):
        User = get_user_model()
        users = User.objects.filter(is_active=True, is_superuser=False).order_by('pk')
        tenants = Tenant._base_manager.filter(deleted_at__isnull=True).order_by('pk')
        if options['user_id'] is not None:
            users = users.filter(pk=options['user_id'])
        if options['tenant_id'] is not None:
            tenants = tenants.filter(pk=options['tenant_id'])

        tenant_rows = list(tenants)
        differences = []
        checked_pairs = 0
        for user in users.iterator():
            legacy_access = legacy_accessible_tenant_ids(user)
            new_access = new_accessible_tenant_ids(user)
            if legacy_access != new_access:
                differences.append({
                    'kind': 'accessible_tenants',
                    'user_id': user.pk,
                    'username': user.get_username(),
                    'legacy_only': sorted(legacy_access - new_access),
                    'new_only': sorted(new_access - legacy_access),
                })

            for tenant in tenant_rows:
                checked_pairs += 1
                legacy = legacy_effective_permissions(user, tenant)
                new = new_effective_permissions(user, tenant)
                if legacy == new:
                    continue
                differences.append({
                    'kind': 'permissions',
                    'user_id': user.pk,
                    'username': user.get_username(),
                    'tenant_id': tenant.pk,
                    'tenant': tenant.name,
                    'legacy_only': sorted(legacy - new),
                    'new_only': sorted(new - legacy),
                })

        payload = {
            'checked_user_tenant_pairs': checked_pairs,
            'difference_count': len(differences),
            'differences': differences,
        }
        if options['as_json']:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        elif not options['summary_only']:
            for difference in differences:
                if difference['kind'] == 'permissions':
                    self.stdout.write(
                        'permissions user={username}({user_id}) tenant={tenant}({tenant_id}) '
                        'legacy_only={legacy_only} new_only={new_only}'.format(**difference)
                    )
                else:
                    self.stdout.write(
                        'accessible_tenants user={username}({user_id}) '
                        'legacy_only={legacy_only} new_only={new_only}'.format(**difference)
                    )
        if not options['as_json']:
            self.stdout.write(
                f'Checked {checked_pairs} user/tenant pairs; '
                f'{len(differences)} disagreement(s).'
            )

        if differences and not options['allow_differences']:
            raise CommandError(
                f'RBAC comparison failed with {len(differences)} disagreement(s).'
            )
