"""Operations seed mixin: alerts, reports, event rules, config, dashboards, audit.

Designed to be mixed into ``Command`` in seed_data.py:

    from core.management.commands._seed.operations import SeedOperationsMixin

    class Command(SeedOperationsMixin, BaseCommand):
        ...

``_seed_operations`` runs last of the original phases (it reads
``self._licenses`` / ``self._assets`` / ``self._tenants`` / ``self._tenant_meta``
/ ``self._tenant_locations`` / ``self._asset_types`` / ``self._users`` /
``self._provisioner``).
"""

import datetime
import random

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType

User = get_user_model()

TODAY = datetime.date.today()


def days_ahead(n):
    return TODAY + datetime.timedelta(days=n)


class SeedOperationsMixin:
    """Mixin for Command(BaseCommand).  Reads/writes self._ registries."""

    def _seed_operations(self):
        from extras.models import NotificationChannel, AlertRule, AlertLog
        from extras.models import EventRule, WebhookEndpoint, LabelTemplate, JournalEntry, ReportTemplate, ScheduledReport
        from extras.models import Dashboard
        from assets.models import Asset, AssetType, AssetRequest
        from compliance.models import AuditSession, AssetAudit
        from licenses.models import License
        self.stdout.write('--- Operations: alerts, reports, automation ---')

        # Notification channels
        email_ch = NotificationChannel.objects.create(
            name='Northwind Service Desk', channel_type='email', enabled=True,
            config={'recipients': 'servicedesk@northwind-it.com'})
        slack_ch = NotificationChannel.objects.create(
            name='Northwind Slack #alerts', channel_type='slack', enabled=True,
            config={'webhook_url': 'https://hooks.slack.com/services/T000/B000/XXXX'})

        # Alert rules (system-wide)
        rules = {}
        for name, atype, thr, sev in [
            ('Low Inventory Stock', 'low_stock', 5, 'warning'),
            ('License Expiring Soon', 'license_expiry', 30, 'warning'),
            ('Subscription Renewal Due', 'renewal_due', 45, 'info'),
            ('Hardware Warranty Expiring', 'warranty_expiry', 60, 'warning'),
            ('Asset End-of-Life Planning', 'upcoming_eol', 90, 'info'),
            ('Audit Overdue', 'audit_overdue', 365, 'critical'),
        ]:
            r = AlertRule.objects.create(name=name, alert_type=atype, threshold_value=thr, severity=sev,
                                         is_active=True, description=f'{name} monitoring across managed tenants.')
            r.channels.add(email_ch, slack_ch)
            rules[atype] = r

        # Alert logs referencing real objects (active / acknowledged)
        ct_asset = ContentType.objects.get_for_model(Asset)
        ct_lic = ContentType.objects.get_for_model(License)
        log_count = 0
        expiring_licenses = [lic for lic in self._licenses if lic.expiration_date and lic.expiration_date <= days_ahead(30)]
        for lic in expiring_licenses[:12]:
            AlertLog.objects.create(
                rule=rules['license_expiry'], content_type=ct_lic, object_id=lic.pk,
                subject=f"License '{lic.name}' expires {lic.expiration_date:%Y-%m-%d}",
                message=f"{lic.name} ({lic.seats} seats) is due to expire on {lic.expiration_date:%Y-%m-%d}.",
                severity='warning', status=random.choice(['active', 'active', 'acknowledged']))
            log_count += 1
        warranty_assets = [a for a in self._assets if a.current_warranty_end and a.current_warranty_end <= days_ahead(60)]
        for a in random.sample(warranty_assets, k=min(15, len(warranty_assets))):
            AlertLog.objects.create(
                rule=rules['warranty_expiry'], content_type=ct_asset, object_id=a.pk,
                subject=f"Warranty for {a.asset_tag} expires {a.current_warranty_end:%Y-%m-%d}",
                message=f"{a.name} ({a.asset_tag}) warranty ends {a.current_warranty_end:%Y-%m-%d}.",
                severity='warning', status=random.choice(['active', 'acknowledged', 'resolved']))
            log_count += 1

        # Report templates + schedules
        rt_summary = ReportTemplate.objects.create(
            name='Fleet Inventory Summary', report_type='asset_summary',
            description='All managed assets by status, role and tenant.', include_summary_cards=True,
            include_distribution_chart=True, group_by_field='status')
        rt_lic = ReportTemplate.objects.create(
            name='License Utilization', report_type='license_utilization',
            description='Seat utilization and renewal exposure across customers.', include_summary_cards=True)
        rt_renew = ReportTemplate.objects.create(
            name='Upcoming Subscription Renewals', report_type='subscription_renewals',
            description='Cloud and SaaS renewals due in the next quarter.', include_summary_cards=True)
        rt_dep = ReportTemplate.objects.create(
            name='Asset Depreciation Summary', report_type='asset_depreciation',
            description='Written-down value of the managed fleet.', include_summary_cards=True,
            style_preset='financial')

        sr1 = ScheduledReport.objects.create(name='Weekly Fleet Summary', report=rt_summary, frequency='weekly',
                                             format='html', recipients='ops@northwind-it.com', is_active=True,
                                             start_time=datetime.time(7, 0))
        sr1.channels.add(email_ch)
        sr2 = ScheduledReport.objects.create(name='Monthly License Review', report=rt_lic, frequency='monthly',
                                             format='csv', recipients='licensing@northwind-it.com', is_active=True)
        sr2.channels.add(email_ch)

        # Event rules + webhook
        webhook = WebhookEndpoint.objects.get_or_create(
            name='Northwind Slack Hardware Events',
            defaults={'url': 'https://hooks.slack.com/services/T000/B001/HARDWARE',
                      'secret': 'demo_shared_secret', 'enabled': True})[0]
        EventRule.objects.create(
            name='Notify on new asset', model=ct_asset, events=['create'], action_type='notification',
            action_config={'message': 'A new asset was added to the managed fleet.'}, enabled=True)
        EventRule.objects.create(
            name='Push asset status changes to Slack', model=ct_asset, events=['update'], action_type='webhook',
            action_config={'endpoint': webhook.name}, enabled=True)

        # Dashboards for the MSP operators
        for user in [self._provisioner] + list(User.objects.filter(is_superuser=True)[:1]):
            Dashboard.objects.get_or_create(user=user, name='Operations Overview',
                                            defaults={'is_default': True, 'layout': []})

        # Quarterly audit
        audit = AuditSession.objects.create(name='Q2 Managed Fleet Audit', status='in_progress',
                                            created_by=self._provisioner)
        audit_assets = random.sample(self._assets, k=min(25, len(self._assets)))
        audited = 0
        for a in audit_assets:
            loc = a.location or (self._tenant_locations.get(a.tenant.slug)[0]
                                 if a.tenant and self._tenant_locations.get(a.tenant.slug) else None)
            AssetAudit.objects.get_or_create(session=audit, asset=a, defaults={
                'status': a.status, 'auditor': self._provisioner, 'location': loc})
            audited += 1

        # Asset requests from customer admins
        req_type = self._asset_types['dell-latitude-5550']
        req_type.requestable = True
        req_type.save(update_fields=['requestable'])
        self._asset_types['iphone-15-pro'].requestable = True
        self._asset_types['iphone-15-pro'].save(update_fields=['requestable'])
        customer_admin_users = [u for name, u in self._users.items() if name.startswith('admin@')]
        req_count = 0
        for user in customer_admin_users[:5]:
            AssetRequest.objects.create(requester=user, asset_type=req_type,
                                        notes='New starter joining next month — needs a standard laptop.',
                                        status=random.choice(['pending', 'approved']))
            req_count += 1

        # Journal entries on a few assets
        if self._assets:
            for a in random.sample(self._assets, k=min(6, len(self._assets))):
                JournalEntry.objects.create(content_object=a, user=self._provisioner,
                                            comment=random.choice([
                                                'Device inspected during site visit — minor cosmetic wear.',
                                                'User reported fan noise under load; monitoring.',
                                                'Re-imaged and re-enrolled in MDM after role change.',
                                                'Confirmed asset present and tagged during audit.']))

        # Label template
        qr_cell = ('<table style="width:100%"><tr>'
                   '<td style="width:55%"><div style="font-weight:bold">{{ asset.name }}</div>'
                   '<div style="font-family:monospace">{{ asset.asset_tag }}</div></td>'
                   '<td style="width:45%;text-align:right">{{ barcode_img }}</td></tr></table>')
        label_templates = [
            ('Standard QR Asset Label', '2.0 x 1.0 inch QR label for laptops & desktops', 'qr', 2.0, 1.0, qr_cell),
            ('Compact QR Asset Tag', '1.5 x 0.5 inch QR tag for accessories & small items', 'qr', 1.5, 0.5,
             '<div style="text-align:center">{{ barcode_img }}'
             '<div style="font-family:monospace;font-size:7pt">{{ asset.asset_tag }}</div></div>'),
            ('Datacenter Rack Label (Code 128)', '4.0 x 1.0 inch barcode label for rack/server gear', 'code128', 4.0, 1.0,
             '<table style="width:100%"><tr><td><div style="font-weight:bold;font-size:11pt">{{ asset.name }}</div>'
             '<div>{{ asset.asset_tag }} · {{ asset.serial_number }}</div></td>'
             '<td style="text-align:right">{{ barcode_img }}</td></tr></table>'),
            ('Shipping / Transfer Label (Code 39)', '4.0 x 2.0 inch label for in-transit assets', 'code39', 4.0, 2.0,
             '<div><div style="font-weight:bold">{{ asset.name }}</div>'
             '<div>From: {{ asset.location }}</div><div>{{ asset.asset_tag }}</div>{{ barcode_img }}</div>'),
            ('High-Security Data Matrix Label', '1.0 x 1.0 inch 2D label for regulated / GxP assets', 'datamatrix', 1.0, 1.0,
             '<div style="text-align:center">{{ barcode_img }}'
             '<div style="font-family:monospace;font-size:6pt">{{ asset.asset_tag }}</div></div>'),
        ]
        for name, desc, fmt, w, h, code in label_templates:
            LabelTemplate.objects.get_or_create(name=name, defaults={
                'description': desc, 'barcode_format': fmt, 'page_width': w, 'page_height': h,
                'template_code': code})

        self.stdout.write(f'  {len(rules)} alert rules, {log_count} alert logs, 4 report templates, '
                          f'2 schedules, 2 event rules, config contexts, {audited} audited assets, '
                          f'{req_count} asset requests.')
