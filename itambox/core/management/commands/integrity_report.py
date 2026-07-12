"""Read-only tenant-integrity report (data-model remediation plan, phase 1).

Runs the checks in :mod:`core.integrity` and prints a human-readable report,
optionally emitting machine-readable JSON and a proposed-grants file for
operator review. Never writes to the database.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand

from core.integrity import run_all_checks

CHECK_TITLES = {
    'null_tenant': 'Operational rows with tenant=NULL',
    'stock_tenant_conflict': 'Stock pools with conflicting item/location tenants',
    'cross_tenant_assignment': 'Cross-tenant assignments',
    'location_site_tenant_mismatch': 'Location/Site tenant mismatches',
    'po_tenant_mismatch': 'Purchase orders vs destination location',
    'po_line_tenant_mismatch': 'Purchase-order lines vs purchase order',
    'po_line_item_tenant_mismatch': 'Purchase-order lines vs catalogue item',
    'license_seat_tenant_mismatch': 'License seats vs assignment target',
    'custody_tenant_mismatch': 'Custody receipts: asset vs holder',
    'rbac_grant_inconsistent': 'RBAC grants: role owner vs principal tenant',
    'rbac_group_inconsistent': 'User groups: ownership/membership consistency',
}


class Command(BaseCommand):
    help = (
        "Report tenant-integrity violations across all apps (read-only). "
        "Cross-tenant rows are classified; sharing-eligible rows produce "
        "PROPOSED TenantResourceGrant payloads for operator review — nothing "
        "is written to the database."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--json', action='store_true', dest='as_json',
            help='Emit the full report as JSON on stdout instead of text.',
        )
        parser.add_argument(
            '--proposals', metavar='PATH',
            help='Write proposed TenantResourceGrant payloads (JSON) to PATH.',
        )
        parser.add_argument(
            '--fail-on-findings', action='store_true',
            help='Exit non-zero when any finding is reported (for CI gates).',
        )

    def handle(self, *args, **options):
        findings, proposals, stats = run_all_checks()

        if options['proposals']:
            payload = [p.as_dict() for p in proposals]
            Path(options['proposals']).write_text(
                json.dumps(payload, indent=2), encoding='utf-8',
            )

        if options['as_json']:
            self.stdout.write(json.dumps({
                'findings': [f.as_dict() for f in findings],
                'proposals': [p.as_dict() for p in proposals],
                'stats': stats,
            }, indent=2, default=str))
        else:
            self._render_text(findings, proposals, stats, options)

        if options['fail_on_findings'] and findings:
            raise SystemExit(1)

    def _render_text(self, findings, proposals, stats, options):
        if not findings:
            self.stdout.write(self.style.SUCCESS('No integrity findings.'))
        by_check = {}
        for f in findings:
            by_check.setdefault(f.check, []).append(f)
        for check, group in by_check.items():
            title = CHECK_TITLES.get(check, check)
            self.stdout.write('')
            self.stdout.write(self.style.MIGRATE_HEADING(f'{title} ({len(group)})'))
            for f in group:
                marker = f' [{f.classification}]' if f.classification else ''
                self.stdout.write(f'  - {f.summary}{marker}')

        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('Summary'))
        self.stdout.write(f'  findings: {stats["total_findings"]}')
        for cls, count in sorted(stats['by_classification'].items()):
            self.stdout.write(f'    {cls}: {count}')
        self.stdout.write(f'  proposed grants: {stats["proposals"]}')
        if proposals and not options['proposals']:
            self.stdout.write(self.style.WARNING(
                '  (re-run with --proposals PATH to write them for review)'
            ))
