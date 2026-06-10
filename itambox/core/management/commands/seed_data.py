"""
Seed the database with a coherent, presentation-ready demo dataset.

The narrative is a **Managed Service Provider (MSP)** — "Northwind Managed
Services" — that runs its own internal IT and manages IT for a handful of
customers across regulated industries (pharma, banking, asset management, plus
a few smaller single-tenant clients). MSP staff hold cross-tenant memberships
with scoped roles, which is what makes the multi-tenant story tangible.

Every app is touched with realistic, headcount-driven daily data: organization
hierarchy, access (users/roles/memberships), assets + assignments + custody,
inventory stock, licensing, subscriptions, maintenance, procurement, and the
operational layer (alerts, reports, event rules, config contexts, dashboards).

Usage:
    python manage.py seed_data                  # full demo dataset (clears first)
    python manage.py seed_data --skip-drop      # add without clearing
    python manage.py seed_data --production      # minimal essential data only
"""

import datetime
import hashlib
import random

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

User = get_user_model()

SEED_PASSWORD = 'itambox2026'
TODAY = datetime.date.today()


def days_ago(n):
    return TODAY - datetime.timedelta(days=n)


def days_ahead(n):
    return TODAY + datetime.timedelta(days=n)


class Command(BaseCommand):
    help = "Seed the database with a presentation-ready MSP demo dataset."

    def add_arguments(self, parser):
        parser.add_argument('--skip-drop', action='store_true', default=False,
                            help='Add data without clearing existing records.')
        parser.add_argument('--production', action='store_true', default=False,
                            help='Only create minimal essential data (admin user, status labels).')

    def handle(self, *args, **options):
        random.seed(42)  # reproducible dataset

        if not options['skip_drop']:
            self._clear_all_data()

        if options['production']:
            self._seed_minimal()
        else:
            self._seed_all()

        self.stdout.write(self.style.SUCCESS('\nDatabase seeding complete.'))

    # ─────────────────────────────────────────────────────────────────
    # Clear
    # ─────────────────────────────────────────────────────────────────

    def _clear_all_data(self):
        self.stdout.write('Clearing all existing data...')

        # Truncate tables left behind by uninstalled plugins (e.g. itambox_esign,
        # which was moved to a separate repo). Their rows still FK-reference assets
        # but their models are no longer registered, so the ORM cannot clear them
        # and asset deletion would fail. No-op on a fresh database.
        from django.db import connection
        orphan_tables = [t for t in connection.introspection.table_names()
                         if t.startswith('itambox_esign')]
        if orphan_tables:
            with connection.cursor() as cur:
                cur.execute('TRUNCATE TABLE %s CASCADE'
                            % ', '.join('"%s"' % t for t in orphan_tables))
            self.stdout.write(f'  Truncated orphaned plugin tables: {", ".join(orphan_tables)}')

        models_to_clear = [
            ('core', 'AlertLog'), ('core', 'AlertRule'),
            ('core', 'ScheduledReport'), ('core', 'ReportTemplate'),
            ('core', 'NotificationChannel'), ('extras', 'EventRule'),
            ('extras', 'WebhookEndpoint'), ('extras', 'LabelTemplate'),
            ('extras', 'ExportTemplate'), ('extras', 'JournalEntry'),
            ('core', 'Notification'), ('core', 'ObjectChange'),
            ('extras', 'ConfigContext'), ('extras', 'Dashboard'),
            ('procurement', 'FulfillmentLink'), ('procurement', 'PurchaseOrderLine'),
            ('procurement', 'PurchaseOrder'),
            ('assets', 'AssetRequest'), ('compliance', 'AssetAudit'), ('compliance', 'AuditSession'),
            ('compliance', 'CustodyReceipt'), ('compliance', 'CustodyTemplate'),
            ('assets', 'AssetMaintenance'),
            ('licenses', 'LicenseSeatAssignment'),
            ('subscriptions', 'SubscriptionAssignment'),
            ('inventory', 'AccessoryAssignment'), ('inventory', 'ConsumableAssignment'),
            ('inventory', 'ComponentAllocation'), ('inventory', 'ComponentStock'),
            ('inventory', 'AccessoryStock'), ('inventory', 'ConsumableStock'),
            ('inventory', 'KitItem'), ('inventory', 'Kit'),
            ('software', 'InstalledSoftware'), ('assets', 'AssetAssignment'),
            ('assets', 'Asset'), ('assets', 'AssetType'),
            ('inventory', 'Component'), ('inventory', 'Accessory'), ('inventory', 'Consumable'),
            ('licenses', 'License'), ('software', 'Software'),
            ('subscriptions', 'Subscription'), ('subscriptions', 'Provider'),
            ('organization', 'TenantMembership'), ('organization', 'TenantInvitation'),
            ('organization', 'TenantRole'),
            ('organization', 'ContactAssignment'), ('organization', 'Contact'),
            ('organization', 'ContactRole'),
            ('organization', 'Location'), ('organization', 'Site'),
            ('organization', 'AssetHolder'),
            ('organization', 'Tenant'), ('organization', 'TenantGroup'),
            ('organization', 'Region'), ('organization', 'SiteGroup'),
            ('assets', 'AssetRole'), ('assets', 'StatusLabel'),
            ('assets', 'Manufacturer'), ('assets', 'Supplier'), ('assets', 'Category'),
            ('assets', 'CustomFieldset'), ('assets', 'CustomField'), ('assets', 'Depreciation'),
            ('extras', 'Tag'),
        ]
        # Delete via _base_manager (bypasses tenant scoping + soft-delete filters so
        # EVERY row is removed) and retry across passes so PROTECT ordering between
        # the existing dataset and this one resolves regardless of FK direction.
        pending = list(models_to_clear)
        for _attempt in range(5):
            failed = []
            for app_label, model_name in pending:
                try:
                    model = apps.get_model(app_label, model_name)
                    count, _ = model._base_manager.all().delete()
                    if count:
                        self.stdout.write(f'  Deleted {count} {model_name}(s)')
                except Exception:
                    failed.append((app_label, model_name))
            if not failed:
                break
            pending = failed
        for app_label, model_name in pending:
            self.stdout.write(self.style.WARNING(f'  Could not fully clear {model_name}'))

        User.objects.filter(is_superuser=False).delete()
        ContentType.objects.clear_cache()
        self.stdout.write('  Kept superuser accounts, deleted regular users.')

    # ─────────────────────────────────────────────────────────────────
    # Minimal Seed (--production)
    # ─────────────────────────────────────────────────────────────────

    def _seed_minimal(self):
        from assets.models import StatusLabel
        if not User.objects.filter(is_superuser=True).exists():
            User.objects.create_superuser(username='admin', email='admin@itambox.local', password='admin123')
            self.stdout.write('  Created admin user (admin / admin123)')
        for name, slug, stype, color in self._status_label_defs():
            StatusLabel.objects.get_or_create(slug=slug, defaults={'name': name, 'type': stype, 'color': color})
        self.stdout.write('  Seeded default StatusLabels.')

    # ─────────────────────────────────────────────────────────────────
    # Full Seed
    # ─────────────────────────────────────────────────────────────────

    def _seed_all(self):
        self.stdout.write('\nSeeding MSP demo dataset...\n')
        with transaction.atomic():
            self._seed_catalog()
            self._seed_organizations()
            self._seed_access()
            self._seed_assets()
            self._seed_inventory_stock()
            self._seed_licensing()
            self._seed_subscriptions()
            self._seed_maintenance()
            self._seed_procurement()
            self._seed_operations()

    # ─────────────────────────────────────────────────────────────────
    # Catalog (tenant-agnostic reference data)
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _status_label_defs():
        return [
            ('Available', 'available', 'deployable', '28a745'),
            ('In Use', 'in-use', 'deployed', '007bff'),
            ('Pending Repair', 'pending-repair', 'pending', 'ffc107'),
            ('Retired', 'retired', 'archived', 'dc3545'),
            ('In Transit', 'in-transit', 'pending', '6f42c1'),
            ('Decommissioned', 'decommissioned', 'undeployable', '6c757d'),
            ('Quarantined', 'quarantined', 'pending', 'fd7e14'),
        ]

    def _seed_catalog(self):
        from assets.models import StatusLabel, AssetRole, Manufacturer, Depreciation, Supplier, AssetType, Category
        from extras.models import Tag, CustomField, CustomFieldset
        from inventory.models import Component, Accessory, Consumable
        from software.models import Software
        from subscriptions.models import Provider

        self.stdout.write('--- Catalog: reference data ---')

        # Status labels
        self._status_labels = {}
        for name, slug, stype, color in self._status_label_defs():
            obj, _ = StatusLabel.objects.get_or_create(slug=slug, defaults={'name': name, 'type': stype, 'color': color})
            self._status_labels[slug] = obj

        # Tags
        self._tags = {}
        for name, slug, color in [
            ('Production', 'production', '28a745'), ('Development', 'development', '007bff'),
            ('VIP', 'vip', 'dc3545'), ('GxP Validated', 'gxp-validated', '198754'),
            ('PCI Scope', 'pci-scope', '0b5ed7'), ('Finance', 'finance', '198754'),
            ('Field', 'field', 'fd7e14'), ('Loaner', 'loaner', 'adb5bd'),
            ('Critical', 'critical', 'dc3545'), ('Legacy', 'legacy', '6c757d'),
            ('Encrypted', 'encrypted', '20c997'), ('MDM Enrolled', 'mdm-enrolled', '6f42c1'),
        ]:
            obj, _ = Tag.objects.get_or_create(slug=slug, defaults={'name': name, 'color': color})
            self._tags[slug] = obj

        # Asset roles
        self._asset_roles = {}
        for name, slug, color, desc in [
            ('Standard Workstation', 'standard-workstation', '007bff', 'Laptop/desktop for general office staff'),
            ('Developer Workstation', 'developer-workstation', '6f42c1', 'High-performance workstation for engineers'),
            ('Executive Workstation', 'executive-workstation', 'e83e8c', 'Premium device for executives'),
            ('CAD/Design Workstation', 'cad-design-workstation', 'fd7e14', 'GPU workstation for CAD/3D'),
            ('Lab / Cleanroom Terminal', 'lab-terminal', 'adb5bd', 'Restricted terminal for lab or production-floor use'),
            ('Field Tablet', 'field-tablet', '20c997', 'Ruggedized tablet for field/warehouse work'),
            ('Corporate Smartphone', 'corporate-smartphone', 'fd7e14', 'Company smartphone for voice/chat/MFA'),
            ('Virtualization Host', 'virtualization-host-server', 'dc3545', 'Hypervisor host (ESXi/Proxmox/Hyper-V)'),
            ('Database Server', 'database-server', '17a2b8', 'Production database host'),
            ('Application Server', 'application-server', '20c997', 'Line-of-business application host'),
            ('Backup / Storage', 'backup-server', 'fd7e14', 'Backup target or NAS'),
            ('Core Router / Firewall', 'core-router-firewall', 'dc3545', 'Edge security gateway'),
            ('Access / Distribution Switch', 'access-switch', '0d6efd', 'Network switch'),
            ('Wireless Access Point', 'wireless-ap', '20c997', 'Enterprise WiFi access point'),
            ('Conference Room AV', 'conference-av', 'e83e8c', 'Meeting-room camera/audio hub'),
            ('Desktop Monitor', 'desktop-monitor', '6f42c1', 'External display'),
        ]:
            obj, _ = AssetRole.objects.get_or_create(slug=slug, defaults={'name': name, 'color': color, 'description': desc})
            self._asset_roles[slug] = obj

        # Manufacturers
        self._manufacturers = {}
        for name, slug in [
            ('Dell Technologies', 'dell-technologies'), ('Apple Inc.', 'apple-inc'),
            ('HP Inc.', 'hp-inc'), ('Lenovo Group', 'lenovo-group'),
            ('Cisco Systems', 'cisco-systems'), ('Samsung Electronics', 'samsung-electronics'),
            ('Microsoft Corporation', 'microsoft-corporation'), ('Logitech International', 'logitech-international'),
            ('Brother Industries', 'brother-industries'), ('Synology Inc.', 'synology-inc'),
            ('Ubiquiti Inc.', 'ubiquiti-inc'),
        ]:
            obj, _ = Manufacturer.objects.get_or_create(slug=slug, defaults={'name': name})
            self._manufacturers[slug] = obj

        # Suppliers
        self._suppliers = {}
        for name, slug, email, phone, website in [
            ('Northwind Procurement', 'northwind-procurement', 'buy@northwind-it.com', '+49-30-555-0100', 'https://northwind-it.com'),
            ('Dell Direct', 'dell-direct', 'enterprise@dell.com', '+1-800-555-0199', 'https://dell.com'),
            ('Apple Business', 'apple-business', 'business@apple.com', '+1-800-555-0200', 'https://apple.com/business'),
            ('CDW Deutschland', 'cdw-deutschland', 'de.sales@cdw.com', '+49-211-555-0500', 'https://cdw.de'),
            ('Bechtle AG', 'bechtle-ag', 'b2b@bechtle.com', '+49-7132-555-0700', 'https://bechtle.com'),
            ('Insight Enterprises', 'insight-enterprises', 'eu@insight.com', '+44-20-555-0800', 'https://insight.com'),
        ]:
            obj, _ = Supplier.objects.get_or_create(slug=slug, defaults={
                'name': name, 'contact_email': email, 'contact_phone': phone, 'website': website})
            self._suppliers[slug] = obj

        # Depreciation schedules
        self._depreciations = {}
        for name, months in [('3-Year Straight-Line', 36), ('4-Year Straight-Line', 48),
                             ('5-Year Straight-Line', 60), ('7-Year Straight-Line', 84)]:
            obj, _ = Depreciation.objects.get_or_create(name=name, defaults={'months': months})
            self._depreciations[name] = obj

        # Custom fields + fieldsets
        cf_data = [
            ('hostname', 'Hostname', 'text', False, None, False),
            ('os_version', 'OS Version', 'text', False, None, False),
            ('department', 'Department', 'select', False, 'Engineering\nFinance\nHR\nMarketing\nSales\nOperations\nResearch\nLegal', False),
            ('encrypted', 'Disk Encrypted', 'boolean', False, None, False),
            ('sim_number', 'SIM Number', 'text', False, None, False),
            ('imei', 'IMEI', 'text', False, None, False),
            ('ip_address', 'IP Address', 'text', False, None, False),
            ('firmware_version', 'Firmware Version', 'text', False, None, False),
            ('port_count', 'Port Count', 'number', False, None, True),
            ('poe_budget_w', 'PoE Budget (Watts)', 'number', False, None, True),
            ('screen_size', 'Screen Size (inches)', 'number', False, None, True),
            ('mounted_state', 'Mounted State', 'select', False, 'Wall-Mounted\nCeiling-Mounted\nTable-Top\nMobile-Stand', False),
            ('cpu', 'CPU Model', 'text', False, None, True),
            ('ram_gb', 'RAM (GB)', 'number', False, None, True),
            ('storage_gb', 'Storage (GB)', 'number', False, None, True),
            ('storage_type', 'Storage Type', 'select', False, 'NVMe\nSSD\nHDD\nSSD RAID\nSATA SSD', True),
            ('gpu', 'GPU Model', 'text', False, None, True),
            ('cpu_architecture', 'CPU Architecture', 'select', False, 'x86_64\nARM64', True),
        ]
        from django.contrib.contenttypes.models import ContentType
        from assets.models import Asset as AssetModel, AssetType as AssetTypeModel
        asset_ct = ContentType.objects.get_for_model(AssetModel)
        assettype_ct = ContentType.objects.get_for_model(AssetTypeModel)

        self._custom_fields = {}
        for name, label, ftype, required, choices, model_level in cf_data:
            obj, _ = CustomField.objects.get_or_create(name=name, defaults={
                'label': label, 'field_type': ftype, 'required': required,
                'choices': choices})
            # model_level=True described the hardware type (a spec); otherwise
            # the field is a per-device detail on the asset.
            obj.object_types.add(assettype_ct if model_level else asset_ct)
            self._custom_fields[name] = obj

        def fieldset(name, *field_names):
            fs = CustomFieldset.objects.create(name=name)
            fs.fields.add(*[self._custom_fields[f] for f in field_names])
            return fs

        self._fs_laptop = fieldset('Laptop / Workstation Specs', 'cpu', 'ram_gb', 'storage_gb', 'storage_type',
                                   'gpu', 'cpu_architecture', 'hostname', 'os_version', 'encrypted', 'department')
        self._fs_mobile = fieldset('Mobile Device Specs', 'cpu', 'ram_gb', 'storage_gb', 'screen_size',
                                   'os_version', 'sim_number', 'imei')
        self._fs_server = fieldset('Server Specs', 'cpu', 'ram_gb', 'storage_gb', 'storage_type',
                                   'hostname', 'os_version')
        self._fs_switch = fieldset('Network Device Specs', 'port_count', 'poe_budget_w', 'hostname',
                                   'ip_address', 'firmware_version')
        self._fs_av = fieldset('AV & Conference Specs', 'screen_size', 'mounted_state')

        # Categories
        self._categories = {}
        for slug in ['laptops', 'desktops', 'servers', 'monitors', 'mobile-phones', 'tablets',
                     'network-devices', 'storage-devices', 'conference-systems',
                     'charger', 'adaptor', 'mouse', 'keyboard', 'webcam', 'headset', 'cable',
                     'display', 'dock', 'toner', 'ink', 'batteries', 'thermal-paste', 'other',
                     'ram-memory', 'ssd-nvme', 'hdd', 'nic', 'gpu', 'cpu']:
            applies = {'asset': True, 'accessory': True, 'consumable': True, 'component': True}
            obj, _ = Category.objects.get_or_create(slug=slug, defaults={
                'name': slug.replace('-', ' ').title(), 'applies_to': applies})
            self._categories[slug] = obj

        # Asset types: (model, slug, mfr, part_number, eol_months, fieldset, depreciation, category, role, specs)
        at_data = [
            ('Latitude 5550', 'dell-latitude-5550', 'dell-technologies', 'LAT5550-2025', 36, self._fs_laptop,
             '3-Year Straight-Line', 'laptops', 'standard-workstation',
             {'cpu': 'Intel Core i7-1365U', 'ram_gb': 16, 'storage_gb': 512, 'storage_type': 'NVMe', 'cpu_architecture': 'x86_64'}),
            ('EliteBook 860 G11', 'hp-elitebook-860-g11', 'hp-inc', '866S7EA', 36, self._fs_laptop,
             '3-Year Straight-Line', 'laptops', 'standard-workstation',
             {'cpu': 'Intel Core i7-1370P', 'ram_gb': 32, 'storage_gb': 1024, 'storage_type': 'NVMe', 'cpu_architecture': 'x86_64'}),
            ('ThinkPad X1 Carbon Gen 12', 'thinkpad-x1-carbon-g12', 'lenovo-group', '21KC004PGE', 36, self._fs_laptop,
             '3-Year Straight-Line', 'laptops', 'developer-workstation',
             {'cpu': 'Intel Core i7-1365U', 'ram_gb': 32, 'storage_gb': 1024, 'storage_type': 'NVMe', 'cpu_architecture': 'x86_64'}),
            ('MacBook Pro 16"', 'macbook-pro-16-2024', 'apple-inc', 'MBP16-M4', 36, self._fs_laptop,
             '3-Year Straight-Line', 'laptops', 'developer-workstation',
             {'cpu': 'Apple M4 Pro', 'ram_gb': 36, 'storage_gb': 1024, 'storage_type': 'NVMe', 'cpu_architecture': 'ARM64'}),
            ('MacBook Air 15"', 'macbook-air-15-2024', 'apple-inc', 'MBA15-M3', 36, self._fs_laptop,
             '3-Year Straight-Line', 'laptops', 'standard-workstation',
             {'cpu': 'Apple M3', 'ram_gb': 16, 'storage_gb': 512, 'storage_type': 'NVMe', 'cpu_architecture': 'ARM64'}),
            ('Precision 5680', 'dell-precision-5680', 'dell-technologies', 'PREC5680-WS', 48, self._fs_laptop,
             '4-Year Straight-Line', 'laptops', 'developer-workstation',
             {'cpu': 'Intel Core i9-13900H', 'ram_gb': 64, 'storage_gb': 2048, 'storage_type': 'NVMe', 'gpu': 'NVIDIA RTX 3000 Ada', 'cpu_architecture': 'x86_64'}),
            ('OptiPlex 7010 SFF', 'dell-optiplex-7010', 'dell-technologies', 'OPT7010-SFF', 48, self._fs_laptop,
             '4-Year Straight-Line', 'desktops', 'standard-workstation',
             {'cpu': 'Intel Core i5-13500', 'ram_gb': 16, 'storage_gb': 512, 'storage_type': 'NVMe', 'cpu_architecture': 'x86_64'}),
            ('Mac Studio', 'mac-studio-2024', 'apple-inc', 'MSTUDIO-M2U', 60, self._fs_laptop,
             '5-Year Straight-Line', 'desktops', 'cad-design-workstation',
             {'cpu': 'Apple M2 Ultra', 'ram_gb': 64, 'storage_gb': 1024, 'storage_type': 'NVMe', 'cpu_architecture': 'ARM64'}),
            ('Precision 7960 Tower', 'dell-precision-7960-tower', 'dell-technologies', 'PREC7960-TWR', 60, self._fs_laptop,
             '5-Year Straight-Line', 'desktops', 'cad-design-workstation',
             {'cpu': 'Intel Xeon w7-3465X', 'ram_gb': 128, 'storage_gb': 4096, 'storage_type': 'SSD RAID', 'gpu': 'NVIDIA RTX 6000 Ada', 'cpu_architecture': 'x86_64'}),
            ('PowerEdge R760', 'dell-poweredge-r760', 'dell-technologies', 'R760-XEON', 60, self._fs_server,
             '5-Year Straight-Line', 'servers', 'virtualization-host-server',
             {'cpu': '2x Intel Xeon Gold 6430', 'ram_gb': 256, 'storage_gb': 8000, 'storage_type': 'SSD RAID'}),
            ('ProLiant DL380 Gen11', 'hpe-proliant-dl380-g11', 'hp-inc', 'P52534-B21', 60, self._fs_server,
             '5-Year Straight-Line', 'servers', 'application-server',
             {'cpu': '2x Intel Xeon Silver 4416+', 'ram_gb': 128, 'storage_gb': 4000, 'storage_type': 'SSD RAID'}),
            ('DiskStation DS1823xs+', 'synology-ds1823xs', 'synology-inc', 'DS1823XS+', 60, self._fs_server,
             '5-Year Straight-Line', 'storage-devices', 'backup-server',
             {'cpu': 'AMD Ryzen V1780B', 'ram_gb': 32, 'storage_gb': 64000, 'storage_type': 'HDD'}),
            ('iPhone 15 Pro', 'iphone-15-pro', 'apple-inc', 'A2847', 24, self._fs_mobile,
             '3-Year Straight-Line', 'mobile-phones', 'corporate-smartphone',
             {'cpu': 'Apple A17 Pro', 'ram_gb': 8, 'storage_gb': 256, 'screen_size': 6.1}),
            ('Galaxy S24 Ultra', 'galaxy-s24-ultra', 'samsung-electronics', 'SM-S928B', 24, self._fs_mobile,
             '3-Year Straight-Line', 'mobile-phones', 'corporate-smartphone',
             {'cpu': 'Snapdragon 8 Gen 3', 'ram_gb': 12, 'storage_gb': 256, 'screen_size': 6.8}),
            ('iPad Pro 12.9"', 'ipad-pro-129-2024', 'apple-inc', 'A2436', 36, self._fs_mobile,
             '3-Year Straight-Line', 'tablets', 'field-tablet',
             {'cpu': 'Apple M4', 'ram_gb': 8, 'storage_gb': 256, 'screen_size': 12.9}),
            ('Surface Pro 10', 'surface-pro-10', 'microsoft-corporation', 'SURFPRO10-I7', 36, self._fs_mobile,
             '3-Year Straight-Line', 'tablets', 'field-tablet',
             {'cpu': 'Intel Core i7-1365U', 'ram_gb': 16, 'storage_gb': 512, 'screen_size': 13.0}),
            ('Catalyst 9300', 'cisco-catalyst-9300', 'cisco-systems', 'C9300-48P', 84, self._fs_switch,
             '7-Year Straight-Line', 'network-devices', 'access-switch', {'port_count': 48, 'poe_budget_w': 740}),
            ('UniFi Switch Pro 48 PoE', 'unifi-switch-pro-48', 'ubiquiti-inc', 'USW-PRO-48-POE', 60, self._fs_switch,
             '5-Year Straight-Line', 'network-devices', 'access-switch', {'port_count': 48, 'poe_budget_w': 600}),
            ('Meraki MR46', 'meraki-mr46', 'cisco-systems', 'MR46-HW', 60, None,
             '5-Year Straight-Line', 'network-devices', 'wireless-ap', {}),
            ('UniFi Dream Machine Pro', 'unifi-dream-machine-pro', 'ubiquiti-inc', 'UDM-Pro', 60, self._fs_switch,
             '5-Year Straight-Line', 'network-devices', 'core-router-firewall', {'port_count': 8, 'poe_budget_w': 0}),
            ('Dell P2723DE 27" Monitor', 'dell-p2723de-monitor', 'dell-technologies', 'P2723DE', 60, None,
             '5-Year Straight-Line', 'monitors', 'desktop-monitor', {}),
            ('Dell P2422HE 24" Monitor', 'dell-p2422he-monitor', 'dell-technologies', 'P2422HE', 60, None,
             '5-Year Straight-Line', 'monitors', 'desktop-monitor', {}),
            ('Logitech Rally Bar', 'logitech-rally-bar', 'logitech-international', '960-001308', 60, self._fs_av,
             '5-Year Straight-Line', 'conference-systems', 'conference-av', {'screen_size': 0}),
        ]
        self._asset_types = {}
        for model_name, slug, mfr, part, eol, fs, dep, cat, role, specs in at_data:
            obj, _ = AssetType.objects.get_or_create(slug=slug, defaults={
                'model': model_name, 'manufacturer': self._manufacturers[mfr], 'part_number': part,
                'eol_months': eol, 'custom_fieldset': fs, 'depreciation': self._depreciations[dep],
                'category': self._categories[cat], 'asset_role': self._asset_roles[role],
                'custom_field_data': specs})
            self._asset_types[slug] = obj

        # Components
        self._components = {}
        for name, slug, mfr, cat, part, specs in [
            ('Samsung 32GB DDR5-4800', 'samsung-32gb-ddr5', 'samsung-electronics', 'ram-memory', 'M324R4GA3BB0', {'capacity_gb': 32, 'type': 'DDR5'}),
            ('Crucial 16GB DDR5-5600', 'crucial-16gb-ddr5', 'samsung-electronics', 'ram-memory', 'CT16G56C46S5', {'capacity_gb': 16, 'type': 'DDR5'}),
            ('Samsung 1TB 990 Pro NVMe', 'samsung-1tb-nvme', 'samsung-electronics', 'ssd-nvme', 'MZ-V9P1T0B', {'capacity_gb': 1000, 'type': 'NVMe'}),
            ('Samsung 2TB 990 Pro NVMe', 'samsung-2tb-nvme', 'samsung-electronics', 'ssd-nvme', 'MZ-V9P2T0B', {'capacity_gb': 2000, 'type': 'NVMe'}),
            ('WD Red Pro 8TB HDD', 'wd-red-8tb', 'dell-technologies', 'hdd', 'WD8003FFBX', {'capacity_gb': 8000, 'type': 'HDD'}),
            ('Seagate IronWolf Pro 12TB', 'seagate-ironwolf-12tb', 'dell-technologies', 'hdd', 'ST12000NE0008', {'capacity_gb': 12000, 'type': 'HDD'}),
            ('Intel X710 10GbE NIC', 'intel-x710-nic', 'dell-technologies', 'nic', 'X710DA2', {'speed': '10GbE'}),
            ('NVIDIA RTX 6000 Ada 48GB', 'nvidia-rtx-6000', 'dell-technologies', 'gpu', 'RTX6000-ADA', {'vram_gb': 48}),
            ('Intel Xeon Gold 6430', 'xeon-gold-6430', 'dell-technologies', 'cpu', 'SRMZS', {'cores': 32}),
            ('Dell PERC H755 RAID Controller', 'dell-perc-h755', 'dell-technologies', 'other', 'PERC-H755', {'interface': 'SAS 12Gb/s'}),
        ]:
            obj, _ = Component.objects.get_or_create(slug=slug, defaults={
                'name': name, 'manufacturer': self._manufacturers[mfr],
                'category': self._categories[cat], 'part_number': part, 'specs': specs})
            self._components[slug] = obj

        # Accessories (tenant set later per stock; catalog rows are global definitions)
        self._accessory_defs = [
            ('USB-C Charger 65W', 'usb-c-charger-65w', 'dell-technologies', 'charger', '450-AFGM', 10),
            ('USB-C to HDMI Adapter', 'usb-c-hdmi-adapter', 'dell-technologies', 'adaptor', '470-AEGM', 10),
            ('Wireless Mouse MX Master 3S', 'mx-master-3s', 'logitech-international', 'mouse', '910-006556', 10),
            ('Wireless Keyboard MX Keys', 'mx-keys', 'logitech-international', 'keyboard', '920-009413', 10),
            ('Webcam Brio 500', 'webcam-brio-500', 'logitech-international', 'webcam', '960-001422', 8),
            ('Headset Zone Wireless 2', 'zone-wireless-2', 'logitech-international', 'headset', '981-000886', 8),
            ('Thunderbolt 4 Dock', 'tb4-dock', 'dell-technologies', 'dock', 'WD22TB4', 6),
            ('Dell 27" Monitor P2723DE', 'dell-p2723de', 'dell-technologies', 'display', 'DELL-P2723DE', 6),
        ]
        self._consumable_defs = [
            ('HP 26X Laser Toner - Black', 'hp-26x-toner-black', 'hp-inc', 'toner', 'CF226X', 5),
            ('Brother DR-241CL Drum Unit', 'brother-dr-241cl', 'brother-industries', 'toner', 'DR-241CL', 3),
            ('Arctic MX-6 Thermal Paste', 'arctic-mx-6', 'dell-technologies', 'thermal-paste', 'MX6-4G', 8),
            ('AA Batteries Pack 24', 'aa-batteries-24', 'logitech-international', 'batteries', 'AA-24PK', 15),
        ]
        # Accessory/Consumable catalogue objects are created per primary tenant
        # (they are tenant-scoped); store the definitions for the stock phase.
        self._accessories = {}
        self._consumables = {}

        # Software
        self._software = {}
        for name, mfr in [
            ('Windows 11 Enterprise', 'microsoft-corporation'), ('macOS Sequoia', 'apple-inc'),
            ('Microsoft 365 E5', 'microsoft-corporation'), ('Microsoft Office LTSC 2024', 'microsoft-corporation'),
            ('Adobe Creative Cloud', 'microsoft-corporation'), ('JetBrains All Products Pack', 'microsoft-corporation'),
            ('VMware vSphere 8 Enterprise Plus', 'dell-technologies'), ('CrowdStrike Falcon', 'microsoft-corporation'),
            ('1Password Business', 'microsoft-corporation'), ('Zoom Workplace Enterprise', 'microsoft-corporation'),
            ('Veeam Backup & Replication', 'dell-technologies'), ('Autodesk AutoCAD', 'microsoft-corporation'),
            ('SAS Analytics Pro', 'dell-technologies'), ('Bloomberg Terminal', 'microsoft-corporation'),
            ('Ubuntu Pro 24.04', 'dell-technologies'),
        ]:
            obj, _ = Software.objects.get_or_create(name=name, defaults={'manufacturer': self._manufacturers[mfr]})
            self._software[name] = obj

        # Cloud / SaaS providers
        self._providers = {}
        for name, acct, url in [
            ('Amazon Web Services', 'aws-org', 'https://console.aws.amazon.com'),
            ('Microsoft Azure', 'azure-ea', 'https://portal.azure.com'),
            ('Google Cloud Platform', 'gcp-org', 'https://console.cloud.google.com'),
            ('GitHub Enterprise', 'github-ent', 'https://github.com/enterprises'),
            ('Cloudflare', 'cloudflare', 'https://dash.cloudflare.com'),
            ('Datadog', 'datadog', 'https://app.datadoghq.eu'),
        ]:
            obj, _ = Provider.objects.get_or_create(name=name, defaults={'account_id': acct, 'portal_url': url})
            self._providers[name] = obj

        self.stdout.write(f'  {len(self._asset_types)} asset types, {len(self._components)} components, '
                          f'{len(self._software)} software products, {len(self._providers)} providers.')

    # ─────────────────────────────────────────────────────────────────
    # Organizations: groups, tenants, sites, locations, holders, contacts
    # ─────────────────────────────────────────────────────────────────

    # Industry profile -> how assets are distributed for a tenant.
    #   laptops: candidate workstation asset-type slugs (one picked per employee)
    #   mobile:  fraction of employees issued a corporate phone
    #   monitors: avg external monitors per deskbound employee
    #   depts:   department choices for custom fields
    #   shared:  list of (asset_type_slug, count) for shared / infrastructure assets
    PROFILES = {
        'msp_internal': dict(laptops=['macbook-pro-16-2024', 'thinkpad-x1-carbon-g12', 'dell-precision-5680'],
                             mobile=0.8, monitors=1.5, depts=['Engineering', 'Operations'],
                             shared=[('dell-poweredge-r760', 4), ('hpe-proliant-dl380-g11', 2),
                                     ('synology-ds1823xs', 2), ('cisco-catalyst-9300', 2),
                                     ('unifi-switch-pro-48', 3), ('meraki-mr46', 6),
                                     ('unifi-dream-machine-pro', 2), ('mac-studio-2024', 2),
                                     ('logitech-rally-bar', 2)]),
        'msp_corp': dict(laptops=['dell-latitude-5550', 'macbook-air-15-2024'], mobile=0.5, monitors=1.0,
                         depts=['Finance', 'HR', 'Sales', 'Marketing'],
                         shared=[('dell-optiplex-7010', 4), ('logitech-rally-bar', 2), ('meraki-mr46', 2)]),
        'pharma_rnd': dict(laptops=['thinkpad-x1-carbon-g12', 'dell-precision-5680', 'macbook-pro-16-2024'],
                           mobile=0.6, monitors=1.3, depts=['Research', 'Engineering', 'Operations'],
                           shared=[('dell-poweredge-r760', 2), ('hpe-proliant-dl380-g11', 1),
                                   ('synology-ds1823xs', 1), ('cisco-catalyst-9300', 2),
                                   ('meraki-mr46', 4), ('unifi-dream-machine-pro', 1),
                                   ('ipad-pro-129-2024', 4), ('dell-optiplex-7010', 4),
                                   ('logitech-rally-bar', 2)]),
        'pharma_mfg': dict(laptops=['dell-latitude-5550', 'hp-elitebook-860-g11'], mobile=0.4, monitors=0.5,
                           depts=['Operations', 'Engineering'],
                           shared=[('surface-pro-10', 6), ('dell-optiplex-7010', 6), ('cisco-catalyst-9300', 2),
                                   ('meraki-mr46', 6), ('unifi-dream-machine-pro', 1)]),
        'pharma_commercial': dict(laptops=['dell-latitude-5550', 'macbook-air-15-2024'], mobile=0.7, monitors=1.0,
                                  depts=['Sales', 'Marketing', 'Finance'],
                                  shared=[('dell-optiplex-7010', 3), ('meraki-mr46', 3), ('logitech-rally-bar', 2)]),
        'bank_retail': dict(laptops=['dell-latitude-5550', 'hp-elitebook-860-g11'], mobile=0.5, monitors=1.5,
                            depts=['Sales', 'Operations', 'Finance'],
                            shared=[('dell-poweredge-r760', 2), ('cisco-catalyst-9300', 2), ('meraki-mr46', 4),
                                    ('unifi-dream-machine-pro', 1), ('dell-optiplex-7010', 6), ('logitech-rally-bar', 2)]),
        'bank_invest': dict(laptops=['macbook-pro-16-2024', 'dell-precision-5680'], mobile=0.9, monitors=2.0,
                            depts=['Sales', 'Finance', 'Operations'],
                            shared=[('dell-poweredge-r760', 2), ('cisco-catalyst-9300', 2),
                                    ('unifi-dream-machine-pro', 1), ('logitech-rally-bar', 2)]),
        'bank_risk': dict(laptops=['dell-latitude-5550'], mobile=0.3, monitors=1.5, depts=['Finance', 'Operations'],
                          shared=[('dell-optiplex-7010', 4), ('meraki-mr46', 2)]),
        'fund_portfolio': dict(laptops=['macbook-pro-16-2024', 'dell-latitude-5550'], mobile=0.8, monitors=2.0,
                               depts=['Finance', 'Operations'],
                               shared=[('dell-poweredge-r760', 1), ('unifi-dream-machine-pro', 1), ('logitech-rally-bar', 1)]),
        'fund_ops': dict(laptops=['dell-latitude-5550'], mobile=0.4, monitors=1.5, depts=['Operations', 'Finance'],
                         shared=[('dell-optiplex-7010', 4), ('meraki-mr46', 2)]),
        'legal': dict(laptops=['hp-elitebook-860-g11', 'dell-latitude-5550'], mobile=0.6, monitors=1.0,
                      depts=['Legal', 'Operations'],
                      shared=[('dell-optiplex-7010', 4), ('unifi-dream-machine-pro', 1), ('meraki-mr46', 2),
                              ('logitech-rally-bar', 1)]),
        'architecture': dict(laptops=['macbook-pro-16-2024', 'dell-precision-5680'], mobile=0.4, monitors=2.0,
                             depts=['Engineering', 'Operations'],
                             shared=[('mac-studio-2024', 2), ('dell-precision-7960-tower', 2),
                                     ('dell-poweredge-r760', 1), ('unifi-dream-machine-pro', 1),
                                     ('logitech-rally-bar', 1)]),
        'logistics': dict(laptops=['dell-latitude-5550'], mobile=0.6, monitors=0.4, depts=['Operations'],
                          shared=[('surface-pro-10', 8), ('ipad-pro-129-2024', 4), ('dell-optiplex-7010', 4),
                                  ('cisco-catalyst-9300', 2), ('meraki-mr46', 6), ('unifi-dream-machine-pro', 1)]),
    }

    FIRST_NAMES = ['Anna', 'Lukas', 'Sophie', 'Felix', 'Marie', 'Jonas', 'Lena', 'Paul', 'Emma', 'Leon',
                   'Hannah', 'Noah', 'Mia', 'Elias', 'Clara', 'Finn', 'Laura', 'Ben', 'Julia', 'Tim',
                   'Sara', 'David', 'Nina', 'Jan', 'Lea', 'Tom', 'Eva', 'Max', 'Pia', 'Niklas',
                   'Yusuf', 'Aisha', 'Marco', 'Chloe', 'Omar', 'Elena', 'Raj', 'Wei', 'Ingrid', 'Pierre',
                   'Sofia', 'Karl', 'Maya', 'Henrik', 'Lucia', 'Andre', 'Petra', 'Samir', 'Greta', 'Viktor']
    LAST_NAMES = ['Muller', 'Schmidt', 'Schneider', 'Fischer', 'Weber', 'Meyer', 'Wagner', 'Becker', 'Hoffmann',
                  'Schafer', 'Koch', 'Bauer', 'Richter', 'Klein', 'Wolf', 'Neumann', 'Schwarz', 'Zimmermann',
                  'Braun', 'Krueger', 'Hartmann', 'Lange', 'Werner', 'Krause', 'Lehmann', 'Koehler', 'Maier',
                  'Walter', 'Huber', 'Kaiser', 'Fuchs', 'Peters', 'Lang', 'Scholz', 'Jung', 'Hahn', 'Vogel',
                  'Friedrich', 'Keller', 'Gunther', 'Frank', 'Berger', 'Winkler', 'Roth', 'Beck', 'Lorenz',
                  'Baumann', 'Franke', 'Albrecht', 'Ludwig']

    def _org_spec(self):
        """Compact specification of the MSP and its customers."""
        return [
            # kind, group(name,slug), domain, [tenants...]
            dict(kind='msp', group=('Northwind Managed Services', 'northwind-msp'), domain='northwind-it.com',
                 tenants=[
                     dict(name='Northwind — Internal IT', slug='northwind-internal-it', code='NW-IT',
                          profile='msp_internal', headcount=12,
                          site=('Northwind Berlin HQ', 'nw-berlin-hq', 'Berlin', 'Friedrichstrasse 88\n10117 Berlin\nGermany',
                                'dach', 'corporate-offices', '52.5200', '13.4050'),
                          extra_sites=[('Northwind Frankfurt DC', 'nw-frankfurt-dc', 'Frankfurt',
                                        'Hanauer Landstrasse 200\n60314 Frankfurt\nGermany', 'dach', 'datacenters', '50.1109', '8.6821')],
                          locations=[('Engineering Floor', 'nw-eng-floor'), ('Service Desk', 'nw-service-desk'),
                                     ('DC Rack Row 1', 'nw-dc-rack-1'), ('DC Rack Row 2', 'nw-dc-rack-2')]),
                     dict(name='Northwind — Corporate', slug='northwind-corporate', code='NW-CORP',
                          profile='msp_corp', headcount=16,
                          site=('Northwind Berlin HQ', 'nw-berlin-hq', 'Berlin', '', 'dach', 'corporate-offices', None, None),
                          locations=[('Finance & HR', 'nw-finance-hr'), ('Sales Floor', 'nw-sales-floor')]),
                 ]),
            dict(kind='customer', industry='Pharmaceuticals', group=('Helix Biopharma', 'helix-biopharma'),
                 domain='helixbio.com',
                 tenants=[
                     dict(name='Helix Biopharma — R&D', slug='helix-rnd', code='HLX-RD', profile='pharma_rnd', headcount=22,
                          site=('Helix Basel Research Campus', 'helix-basel', 'Basel',
                                'Hochbergerstrasse 60\n4057 Basel\nSwitzerland', 'western-europe', 'labs-plants', '47.5596', '7.5886'),
                          locations=[('Lab Block A', 'helix-lab-a'), ('Lab Block B', 'helix-lab-b'),
                                     ('R&D Offices', 'helix-rnd-offices'), ('Server Room', 'helix-basel-srv')]),
                     dict(name='Helix Biopharma — Manufacturing', slug='helix-mfg', code='HLX-MF', profile='pharma_mfg', headcount=16,
                          site=('Helix Visp Plant', 'helix-visp', 'Visp',
                                'Schachenstrasse 12\n3930 Visp\nSwitzerland', 'western-europe', 'labs-plants', '46.2940', '7.8810'),
                          locations=[('Production Line 1', 'helix-line-1'), ('Production Line 2', 'helix-line-2'),
                                     ('QA Lab', 'helix-qa-lab'), ('Plant IT Room', 'helix-visp-srv')]),
                     dict(name='Helix Biopharma — Commercial', slug='helix-commercial', code='HLX-CO', profile='pharma_commercial', headcount=14,
                          site=('Helix Zurich Office', 'helix-zurich', 'Zurich',
                                'Bahnhofstrasse 45\n8001 Zurich\nSwitzerland', 'western-europe', 'corporate-offices', '47.3769', '8.5417'),
                          locations=[('Commercial Floor', 'helix-commercial-floor'), ('Meeting Suites', 'helix-meeting-suites')]),
                 ]),
            dict(kind='customer', industry='Banking', group=('Meridian Capital Bank', 'meridian-bank'),
                 domain='meridianbank.com',
                 tenants=[
                     dict(name='Meridian — Retail Banking', slug='meridian-retail', code='MER-RT', profile='bank_retail', headcount=26,
                          site=('Meridian Frankfurt Tower', 'meridian-frankfurt', 'Frankfurt',
                                'Taunusanlage 12\n60325 Frankfurt\nGermany', 'dach', 'corporate-offices', '50.1109', '8.6700'),
                          locations=[('Retail Floor 2', 'mer-retail-f2'), ('Retail Floor 3', 'mer-retail-f3'),
                                     ('Branch Ops', 'mer-branch-ops'), ('Data Center', 'mer-frankfurt-dc')]),
                     dict(name='Meridian — Investment', slug='meridian-investment', code='MER-IB', profile='bank_invest', headcount=16,
                          site=('Meridian London Office', 'meridian-london', 'London',
                                '30 St Mary Axe\nLondon EC3A 8BF\nUnited Kingdom', 'western-europe', 'corporate-offices', '51.5145', '-0.0803'),
                          locations=[('Trading Floor', 'mer-trading-floor'), ('Deal Rooms', 'mer-deal-rooms')]),
                     dict(name='Meridian — Risk & Compliance', slug='meridian-risk', code='MER-RC', profile='bank_risk', headcount=10,
                          site=('Meridian Frankfurt Tower', 'meridian-frankfurt', 'Frankfurt', '', 'dach', 'corporate-offices', None, None),
                          locations=[('Risk Analytics', 'mer-risk-analytics')]),
                 ]),
            dict(kind='customer', industry='Asset Management', group=('Sterling Asset Management', 'sterling-am'),
                 domain='sterling-am.com',
                 tenants=[
                     dict(name='Sterling — Portfolio Management', slug='sterling-portfolio', code='STG-PM', profile='fund_portfolio', headcount=14,
                          site=('Sterling Munich Office', 'sterling-munich', 'Munich',
                                'Maximilianstrasse 35\n80539 Munich\nGermany', 'dach', 'corporate-offices', '48.1391', '11.5802'),
                          locations=[('Portfolio Desk', 'stg-portfolio-desk'), ('Partner Suites', 'stg-partner-suites'),
                                     ('Server Closet', 'stg-server-closet')]),
                     dict(name='Sterling — Operations', slug='sterling-ops', code='STG-OP', profile='fund_ops', headcount=8,
                          site=('Sterling Munich Office', 'sterling-munich', 'Munich', '', 'dach', 'corporate-offices', None, None),
                          locations=[('Fund Operations', 'stg-fund-ops')]),
                 ]),
            dict(kind='customer', industry='Legal Services', group=None, domain='brightwell-legal.com',
                 tenants=[
                     dict(name='Brightwell Legal', slug='brightwell-legal', code='BWL', profile='legal', headcount=16,
                          site=('Brightwell Berlin Chambers', 'brightwell-berlin', 'Berlin',
                                'Kurfurstendamm 21\n10719 Berlin\nGermany', 'dach', 'corporate-offices', '52.5030', '13.3270'),
                          locations=[('Partner Offices', 'bwl-partner-offices'), ('Associate Bullpen', 'bwl-associates'),
                                     ('Records Room', 'bwl-records')]),
                 ]),
            dict(kind='customer', industry='Architecture & Design', group=None, domain='aurora-arch.com',
                 tenants=[
                     dict(name='Aurora Architects', slug='aurora-architects', code='AUR', profile='architecture', headcount=11,
                          site=('Aurora Hamburg Studio', 'aurora-hamburg', 'Hamburg',
                                'Am Kaiserkai 10\n20457 Hamburg\nGermany', 'dach', 'corporate-offices', '53.5413', '9.9920'),
                          locations=[('Design Studio', 'aur-design-studio'), ('Render Farm', 'aur-render-farm')]),
                 ]),
            dict(kind='customer', industry='Logistics', group=None, domain='vantage-logistics.com',
                 tenants=[
                     dict(name='Vantage Logistics', slug='vantage-logistics', code='VAN', profile='logistics', headcount=18,
                          site=('Vantage Frankfurt Airport Depot', 'vantage-fra', 'Frankfurt',
                                'CargoCity Sued, Gebaude 535\n60549 Frankfurt\nGermany', 'dach', 'field-depots', '50.0490', '8.5870'),
                          locations=[('Warehouse Floor', 'van-warehouse'), ('Dispatch Office', 'van-dispatch'),
                                     ('Network Cabinet', 'van-network')]),
                 ]),
        ]

    def _seed_organizations(self):
        from organization.models import (Region, SiteGroup, TenantGroup, Tenant, Site, Location,
                                          AssetHolder, ContactRole, Contact, ContactAssignment)
        self.stdout.write('--- Organizations ---')

        # Regions
        self._regions = {}
        eu, _ = Region.objects.get_or_create(slug='europe', defaults={'name': 'Europe'})
        self._regions['europe'] = eu
        for name, slug in [('DACH', 'dach'), ('Western Europe', 'western-europe'), ('Nordics', 'nordics')]:
            obj, _ = Region.objects.get_or_create(slug=slug, defaults={'name': name, 'parent': eu})
            self._regions[slug] = obj

        # Site groups
        self._sitegroups = {}
        for name, slug in [('Corporate Offices', 'corporate-offices'), ('Datacenters', 'datacenters'),
                           ('Labs & Plants', 'labs-plants'), ('Field & Depots', 'field-depots')]:
            obj, _ = SiteGroup.objects.get_or_create(slug=slug, defaults={'name': name})
            self._sitegroups[slug] = obj

        self._tgroups = {}
        self._tenants = {}
        self._tenant_meta = {}          # slug -> dict(profile, domain, code, group_slug, industry, kind)
        self._sites = {}
        self._locations = {}            # slug -> Location
        self._tenant_locations = {}     # tenant slug -> [Location]
        self._tenant_holders = {}       # tenant slug -> [AssetHolder]
        self._orgs = self._org_spec()

        for org in self._orgs:
            group_obj = None
            if org['group']:
                gname, gslug = org['group']
                group_obj, _ = TenantGroup.objects.get_or_create(slug=gslug, defaults={'name': gname})
                self._tgroups[gslug] = group_obj

            for t in org['tenants']:
                tenant, _ = Tenant.objects.get_or_create(slug=t['slug'], defaults={
                    'name': t['name'], 'group': group_obj,
                    'description': f"{org.get('industry', 'Managed Service Provider')} — managed by Northwind Managed Services."})
                self._tenants[t['slug']] = tenant
                self._tenant_meta[t['slug']] = dict(profile=t['profile'], domain=org['domain'], code=t['code'],
                                                    group_slug=org['group'][1] if org['group'] else None,
                                                    industry=org.get('industry'), kind=org['kind'])
                self._tenant_locations[t['slug']] = []

                # Primary site (may be shared across tenants of the same org)
                for site_spec in [t['site']] + t.get('extra_sites', []):
                    sname, sslug, city, addr, region_slug, sg_slug, lat, lon = site_spec
                    if sslug not in self._sites:
                        self._sites[sslug] = Site.objects.get_or_create(slug=sslug, defaults={
                            'name': sname, 'tenant': tenant, 'group': self._sitegroups[sg_slug],
                            'region': self._regions[region_slug], 'physical_address': addr,
                            'latitude': lat, 'longitude': lon, 'time_zone': 'Europe/Berlin'})[0]
                primary_site = self._sites[t['site'][1]]

                # Locations
                for lname, lslug in t['locations']:
                    loc, _ = Location.objects.get_or_create(slug=lslug, defaults={
                        'name': lname, 'site': primary_site, 'tenant': tenant})
                    self._locations[lslug] = loc
                    self._tenant_locations[t['slug']].append(loc)

                # Holders (employees)
                holders = self._make_holders(tenant, org['domain'], t['headcount'])
                self._tenant_holders[t['slug']] = holders

        # Contacts: a primary customer contact per customer group/tenant + vendor reps
        self._contact_roles = {}
        for name, slug in [('Account Manager', 'account-manager'), ('Customer Primary Contact', 'customer-primary'),
                           ('Vendor Support', 'vendor-support')]:
            self._contact_roles[slug] = ContactRole.objects.get_or_create(slug=slug, defaults={'name': name})[0]

        self._contacts = []
        for org in self._orgs:
            if org['kind'] != 'customer':
                continue
            label = (org['group'][0] if org['group'] else org['tenants'][0]['name'])
            c = Contact.objects.create(
                name=f"{random.choice(self.FIRST_NAMES)} {random.choice(self.LAST_NAMES)}",
                title=f"IT Manager, {label}", phone='+49-69-555-0%03d' % random.randint(100, 999),
                email=f"it.manager@{org['domain']}", web_url=f"https://{org['domain']}")
            self._contacts.append(c)
            # Assign as primary contact on each tenant of the customer
            for t in org['tenants']:
                tenant = self._tenants[t['slug']]
                ContactAssignment.objects.get_or_create(
                    contact=c, role=self._contact_roles['customer-primary'],
                    content_type=ContentType.objects.get_for_model(tenant), object_id=tenant.pk,
                    defaults={'priority': 'primary'})

        total_holders = sum(len(v) for v in self._tenant_holders.values())
        self.stdout.write(f'  {len(self._tgroups)} tenant groups, {len(self._tenants)} tenants, '
                          f'{len(self._sites)} sites, {len(self._locations)} locations, {total_holders} asset holders.')

    def _make_holders(self, tenant, domain, n):
        from organization.models import AssetHolder
        holders = []
        used = set()
        for _ in range(n):
            for _attempt in range(20):
                first = random.choice(self.FIRST_NAMES)
                last = random.choice(self.LAST_NAMES)
                upn = f"{first}.{last}@{domain}".lower()
                if upn not in used:
                    break
            if upn in used:
                upn = f"{first}.{last}{random.randint(1, 99)}@{domain}".lower()
            used.add(upn)
            holder = AssetHolder.objects.create(
                first_name=first, last_name=last, upn=upn, email=upn, tenant=tenant)
            holders.append(holder)
        return holders

    # ─────────────────────────────────────────────────────────────────
    # Access: users, per-tenant roles, cross-tenant memberships
    # ─────────────────────────────────────────────────────────────────

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

        total_memberships = TenantMembership.objects.count()
        self.stdout.write(f'  {len(self._roles)} tenant roles, {len(self._users)} login users '
                          f'({customer_admins} customer admins), {total_memberships} memberships.')

    # ─────────────────────────────────────────────────────────────────
    # Assets: per-tenant devices, assignments, custody, installs
    # ─────────────────────────────────────────────────────────────────

    PRICES = {
        'dell-latitude-5550': 1899, 'hp-elitebook-860-g11': 2099, 'thinkpad-x1-carbon-g12': 2199,
        'macbook-pro-16-2024': 3599, 'macbook-air-15-2024': 1499, 'dell-precision-5680': 4299,
        'dell-optiplex-7010': 1299, 'mac-studio-2024': 6999, 'dell-precision-7960-tower': 7499,
        'dell-poweredge-r760': 18500, 'hpe-proliant-dl380-g11': 12500, 'synology-ds1823xs': 4200,
        'iphone-15-pro': 1299, 'galaxy-s24-ultra': 1249, 'ipad-pro-129-2024': 1099, 'surface-pro-10': 1799,
        'cisco-catalyst-9300': 8500, 'unifi-switch-pro-48': 1099, 'meraki-mr46': 1200,
        'unifi-dream-machine-pro': 379, 'dell-p2723de-monitor': 499, 'dell-p2422he-monitor': 379,
        'logitech-rally-bar': 2799,
    }
    HW_SUPPLIERS = ['dell-direct', 'apple-business', 'cdw-deutschland', 'bechtle-ag', 'insight-enterprises']

    def _os_for(self, atype_slug):
        if 'macbook' in atype_slug or 'mac-studio' in atype_slug:
            return random.choice(['macOS Sequoia 15.1', 'macOS Sonoma 14.6'])
        if 'iphone' in atype_slug or 'ipad' in atype_slug:
            return random.choice(['iOS 17.6', 'iPadOS 17.6'])
        if 'galaxy' in atype_slug:
            return 'Android 14'
        if atype_slug in ('dell-poweredge-r760', 'hpe-proliant-dl380-g11'):
            return random.choice(['VMware ESXi 8.0u2', 'Ubuntu Server 24.04 LTS', 'Windows Server 2022'])
        if 'thinkpad' in atype_slug or 'precision' in atype_slug:
            return random.choice(['Windows 11 23H2', 'Ubuntu 24.04 LTS'])
        return 'Windows 11 23H2'

    def _seed_assets(self):
        from assets.models import Asset, AssetAssignment
        from software.models import InstalledSoftware
        from inventory.models import ComponentAllocation
        from compliance.models import CustodyTemplate, CustodyReceipt
        self.stdout.write('--- Assets ---')

        # Global custody templates (category-matched).
        self._custody_templates = {}
        for name, slug, cat_slug, eula in [
            ('Standard Workstation & Laptop Agreement', 'laptop-agreement', 'laptops',
             'I acknowledge receipt of the issued laptop/workstation and agree to the acceptable-use and '
             'disk-encryption policy. The equipment remains company property and must be returned on demand.'),
            ('Mobile Device Agreement', 'mobile-agreement', 'mobile-phones',
             'I acknowledge receipt of the mobile device and SIM. I will keep it secured with a passcode/biometrics '
             'and will not remove the mobile device management (MDM) profile.'),
            ('Desktop Workstation Agreement', 'desktop-agreement', 'desktops',
             'I acknowledge custody of the desktop workstation and agree not to modify its hardware or connect it to '
             'unauthorized networks without IT approval.'),
        ]:
            self._custody_templates[cat_slug] = CustodyTemplate.objects.get_or_create(name=name, defaults={
                'category': self._categories[cat_slug], 'eula_text': eula,
                'disclaimer': 'This equipment remains the property of the organization.',
                'qms_reference': f'NMS-IT-{slug.upper()}', 'is_active': True, 'require_acceptance': True,
                'email_signature_request': True, 'signature_provider': 'local'})[0]

        self._asset_seq = {}
        self._assets = []
        self._assets_by_tenant = {}
        self._laptops_by_tenant = {}
        self._servers = []

        def next_tag(code):
            self._asset_seq[code] = self._asset_seq.get(code, 0) + 1
            return f"{code}-{self._asset_seq[code]:04d}"

        def shared_location(tenant_slug):
            locs = self._tenant_locations[tenant_slug]
            for kw in ('srv', 'rack', 'dc', 'server', 'network', 'closet', 'cabinet', 'farm'):
                for loc in locs:
                    if kw in loc.slug:
                        return loc
            return locs[0] if locs else None

        def make_asset(tenant, code, atype_slug, status_slug, holder, location, dept, tags=None):
            atype = self._asset_types[atype_slug]
            role = atype.asset_role
            tag = next_tag(code)
            base_cost = self.PRICES.get(atype_slug, 1000)
            cost = round(base_cost * random.uniform(0.95, 1.05), 2)
            years_old = random.choice([0, 1, 1, 2, 2, 3])
            p_date = days_ago(years_old * 365 + random.randint(0, 300))
            warranty = p_date + datetime.timedelta(days=(atype.eol_months or 36) * 30)
            fs_name = atype.custom_fieldset.name if atype.custom_fieldset else ''
            cv = {}
            host = f"{code.lower()}-{tag.split('-')[-1]}"
            if fs_name == 'Laptop / Workstation Specs':
                cv = {'hostname': host, 'os_version': self._os_for(atype_slug), 'encrypted': True,
                      'department': dept}
            elif fs_name == 'Mobile Device Specs':
                cv = {'os_version': self._os_for(atype_slug),
                      'sim_number': f"+49-170-{random.randint(1000000, 9999999)}",
                      'imei': f"35{random.randint(100000000000, 999999999999)}"}
            elif fs_name == 'Server Specs':
                cv = {'hostname': f"{host}.svc.local", 'os_version': self._os_for(atype_slug)}
            elif fs_name == 'Network Device Specs':
                cv = {'hostname': f"{host}.net.local",
                      'ip_address': f"10.{random.randint(10, 250)}.{random.randint(0, 254)}.{random.randint(1, 254)}",
                      'firmware_version': f"v{random.randint(7, 17)}.{random.randint(0, 12)}.{random.randint(0, 9)}"}
            elif fs_name == 'AV & Conference Specs':
                cv = {'mounted_state': random.choice(['Wall-Mounted', 'Table-Top'])}

            base_location = None if (holder and status_slug == 'in-use') else location
            asset = Asset.objects.create(
                name=f"{atype.model} ({tag})", asset_tag=tag, asset_type=atype, asset_role=role,
                status=self._status_labels[status_slug], location=base_location, tenant=tenant,
                serial_number=f"{code}{random.randint(100000, 999999)}", purchase_cost=cost,
                salvage_value=round(cost * 0.1, 2), purchase_date=p_date, warranty_expiration=warranty,
                supplier=self._suppliers[random.choice(self.HW_SUPPLIERS)],
                order_number=f"PO-{p_date.year}-{random.randint(1000, 9999)}", custom_field_data=cv)
            if tags:
                asset.tags.add(*[self._tags[t] for t in tags if t in self._tags])
            if status_slug == 'in-use' and holder:
                AssetAssignment.objects.create(asset=asset, assigned_user=holder,
                                               checked_out_by=self._provisioner, is_active=True,
                                               notes='Provisioned by Northwind service desk.')
            elif status_slug == 'in-use' and location:
                AssetAssignment.objects.create(asset=asset, assigned_location=location,
                                               checked_out_by=self._provisioner, is_active=True,
                                               notes='Deployed to site infrastructure.')
            self._assets.append(asset)
            return asset

        for slug, tenant in self._tenants.items():
            meta = self._tenant_meta[slug]
            profile = self.PROFILES[meta['profile']]
            code = meta['code']
            holders = self._tenant_holders[slug]
            locs = self._tenant_locations[slug]
            self._assets_by_tenant[slug] = []
            self._laptops_by_tenant[slug] = []
            regulated = meta['industry'] in ('Pharmaceuticals', 'Banking', 'Asset Management')

            # Per-employee primary device + optional mobile + monitors
            for holder in holders:
                dept = random.choice(profile['depts'])
                laptop_slug = random.choice(profile['laptops'])
                lt = make_asset(tenant, code, laptop_slug, 'in-use', holder, None, dept,
                                tags=['encrypted'] + (['gxp-validated'] if regulated and 'pharma' in meta['profile'] else []))
                self._laptops_by_tenant[slug].append(lt)
                self._assets_by_tenant[slug].append(lt)
                if random.random() < profile['mobile']:
                    self._assets_by_tenant[slug].append(
                        make_asset(tenant, code, random.choice(['iphone-15-pro', 'galaxy-s24-ultra']),
                                   'in-use', holder, None, dept, tags=['mdm-enrolled']))
                n_mon = int(profile['monitors']) + (1 if random.random() < (profile['monitors'] % 1) else 0)
                for _ in range(n_mon):
                    self._assets_by_tenant[slug].append(
                        make_asset(tenant, code, random.choice(['dell-p2723de-monitor', 'dell-p2422he-monitor']),
                                   'in-use', holder, None, dept))

            # A few loaner/spare and repair units
            for _ in range(max(1, len(holders) // 12)):
                self._assets_by_tenant[slug].append(
                    make_asset(tenant, code, random.choice(profile['laptops']), 'available', None,
                               locs[0] if locs else None, random.choice(profile['depts']), tags=['loaner']))
            if len(holders) > 8:
                self._assets_by_tenant[slug].append(
                    make_asset(tenant, code, random.choice(profile['laptops']), 'pending-repair', None,
                               locs[0] if locs else None, random.choice(profile['depts'])))

            # Shared / infrastructure assets
            infra_loc = shared_location(slug)
            for atype_slug, count in profile['shared']:
                for _ in range(count):
                    a = make_asset(tenant, code, atype_slug, 'in-use', None, infra_loc,
                                   'Operations', tags=['production'] if 'poweredge' in atype_slug else None)
                    self._assets_by_tenant[slug].append(a)
                    if a.asset_role and a.asset_role.slug in (
                            'virtualization-host-server', 'database-server', 'application-server', 'backup-server'):
                        self._servers.append(a)

        # Installed software on a sample of laptops + all servers
        sw_count = 0
        for slug in self._tenants:
            meta = self._tenant_meta[slug]
            for lt in self._laptops_by_tenant[slug]:
                installs = [(lt.custom_field_data.get('os_version', 'Windows 11 23H2'), 'Intune'),
                            ('Microsoft 365 E5', 'Intune'), ('CrowdStrike Falcon', 'Intune'),
                            ('1Password Business', 'Intune')]
                if meta['industry'] == 'Architecture & Design':
                    installs.append(('Autodesk AutoCAD', 'Intune'))
                if meta['industry'] == 'Asset Management':
                    installs.append(('Bloomberg Terminal', 'Lansweeper'))
                for sw_name, agent in random.sample(installs, k=min(len(installs), random.randint(2, 4))):
                    sw = self._software.get(sw_name if sw_name in self._software else 'Windows 11 Enterprise')
                    if not sw:
                        continue
                    _, created = InstalledSoftware.objects.get_or_create(
                        asset=lt, software=sw, version_detected='',
                        defaults={'discovered_by_agent': agent, 'install_date': lt.purchase_date,
                                  'last_seen_date': timezone.now() - datetime.timedelta(days=random.randint(0, 14))})
                    sw_count += created
        for srv in self._servers:
            for sw_name in random.sample(['VMware vSphere 8 Enterprise Plus', 'Ubuntu Pro 24.04',
                                          'Veeam Backup & Replication'], 2):
                _, created = InstalledSoftware.objects.get_or_create(
                    asset=srv, software=self._software[sw_name], version_detected='',
                    defaults={'discovered_by_agent': 'Lansweeper', 'install_date': srv.purchase_date,
                              'last_seen_date': timezone.now() - datetime.timedelta(days=random.randint(0, 7))})
                sw_count += created

        # Component allocations into servers and CAD towers
        alloc = 0
        cad = [a for a in self._assets if a.asset_type and a.asset_type.slug in
               ('dell-precision-7960-tower', 'mac-studio-2024', 'dell-precision-5680')]
        for srv in self._servers:
            for comp_slug, qty in [('samsung-32gb-ddr5', 4), ('samsung-2tb-nvme', 2), ('wd-red-8tb', 4),
                                   ('intel-x710-nic', 1), ('dell-perc-h755', 1)]:
                if random.random() < 0.7:
                    ComponentAllocation.objects.get_or_create(
                        component=self._components[comp_slug], assigned_asset=srv, defaults={'qty': qty})
                    alloc += 1
        for ws in cad[:30]:
            for comp_slug, qty in [('crucial-16gb-ddr5', 2), ('nvidia-rtx-6000', 1), ('samsung-2tb-nvme', 1)]:
                if random.random() < 0.6:
                    ComponentAllocation.objects.get_or_create(
                        component=self._components[comp_slug], assigned_asset=ws, defaults={'qty': qty})
                    alloc += 1

        # Custody receipts for regulated-industry laptops/mobiles (signed)
        receipts = 0
        for slug, tenant in self._tenants.items():
            if self._tenant_meta[slug]['industry'] not in ('Pharmaceuticals', 'Banking'):
                continue
            for asset in self._assets_by_tenant[slug]:
                cat = asset.asset_type.category.slug if asset.asset_type and asset.asset_type.category else None
                tmpl = self._custody_templates.get(cat)
                active = asset.assignments.filter(is_active=True, assigned_user__isnull=False).first()
                if not (tmpl and active and random.random() < 0.5):
                    continue
                holder = active.assigned_user
                h = hashlib.sha256(f"{asset.asset_tag}-{holder.pk}-{timezone.now()}".encode()).hexdigest()[:64]
                CustodyReceipt.objects.create(
                    asset=asset, holder=holder, custody_template=tmpl, verification_hash=h,
                    signature_canvas=f'data:image/png;base64,SIGNED_{asset.asset_tag}', eula_version='1.0',
                    accepted=True, acceptance_status='accepted',
                    signed_at=timezone.now() - datetime.timedelta(days=random.randint(5, 200)))
                receipts += 1

        self.stdout.write(f'  {len(self._assets)} assets, {sw_count} software installs, '
                          f'{alloc} component allocations, {receipts} custody receipts.')

    # ─────────────────────────────────────────────────────────────────
    # Inventory: accessory/consumable catalog, stock, assignments, kits
    # ─────────────────────────────────────────────────────────────────

    def _seed_inventory_stock(self):
        from inventory.models import (Accessory, Consumable, AccessoryStock, ConsumableStock,
                                       AccessoryAssignment, ConsumableAssignment, Kit, KitItem)
        self.stdout.write('--- Inventory: stock & kits ---')

        catalog_tenant = self._tenants['northwind-internal-it']
        self._accessories = {}
        for name, slug, mfr, cat, part, min_qty in self._accessory_defs:
            self._accessories[slug] = Accessory.objects.get_or_create(slug=slug, defaults={
                'name': name, 'manufacturer': self._manufacturers[mfr], 'category': self._categories[cat],
                'part_number': part, 'min_qty': min_qty, 'tenant': catalog_tenant})[0]
        self._consumables = {}
        for name, slug, mfr, cat, part, min_qty in self._consumable_defs:
            self._consumables[slug] = Consumable.objects.get_or_create(slug=slug, defaults={
                'name': name, 'manufacturer': self._manufacturers[mfr], 'category': self._categories[cat],
                'part_number': part, 'min_qty': min_qty, 'tenant': catalog_tenant})[0]

        # Stock at the MSP and at each tenant's first location.
        stock_count = 0
        for slug in self._tenants:
            locs = self._tenant_locations[slug]
            if not locs:
                continue
            loc = locs[0]
            for acc_slug in random.sample(list(self._accessories), k=4):
                # Deliberately leave a couple below min_qty to trigger low-stock alerts.
                qty = random.choice([0, 2, 3, 8, 12, 20])
                AccessoryStock.objects.get_or_create(accessory=self._accessories[acc_slug], location=loc,
                                                      defaults={'qty': qty})
                stock_count += 1
            for con_slug in random.sample(list(self._consumables), k=2):
                ConsumableStock.objects.get_or_create(consumable=self._consumables[con_slug], location=loc,
                                                       defaults={'qty': random.choice([1, 4, 10, 25])})
                stock_count += 1

        # Accessory assignments to a sample of holders
        assign_count = 0
        all_holders = [h for hs in self._tenant_holders.values() for h in hs]
        for holder in random.sample(all_holders, k=min(60, len(all_holders))):
            for acc_slug in random.sample(list(self._accessories), k=random.randint(1, 3)):
                AccessoryAssignment.objects.create(accessory=self._accessories[acc_slug],
                                                   assigned_holder=holder, qty=1)
                assign_count += 1
        for holder in random.sample(all_holders, k=min(10, len(all_holders))):
            ConsumableAssignment.objects.create(consumable=self._consumables['aa-batteries-24'],
                                                assigned_holder=holder, qty=1)

        # Kits
        kits = [
            ('Developer Onboarding Kit', 'northwind-internal-it',
             [('thinkpad-x1-carbon-g12', 1)], [('mx-master-3s', 1), ('mx-keys', 1), ('tb4-dock', 1)]),
            ('Executive Onboarding Kit', 'northwind-corporate',
             [('macbook-pro-16-2024', 1), ('iphone-15-pro', 1)], [('usb-c-charger-65w', 2), ('zone-wireless-2', 1)]),
            ('Trading Desk Setup', 'meridian-investment',
             [('macbook-pro-16-2024', 1)], [('dell-p2723de', 2), ('mx-master-3s', 1), ('tb4-dock', 1)]),
            ('Field Technician Kit', 'vantage-logistics',
             [('surface-pro-10', 1)], [('usb-c-charger-65w', 1), ('usb-c-hdmi-adapter', 1)]),
        ]
        for name, tenant_slug, at_items, acc_items in kits:
            kit = Kit.objects.create(name=name, description=f'Standard provisioning bundle: {name}.',
                                     tenant=self._tenants[tenant_slug])
            for at_slug, qty in at_items:
                KitItem.objects.create(kit=kit, asset_type=self._asset_types[at_slug], qty=qty)
            for acc_slug, qty in acc_items:
                KitItem.objects.create(kit=kit, accessory=self._accessories[acc_slug], qty=qty)

        self.stdout.write(f'  {len(self._accessories)} accessories, {len(self._consumables)} consumables, '
                          f'{stock_count} stock rows, {assign_count} accessory assignments, {len(kits)} kits.')

    # ─────────────────────────────────────────────────────────────────
    # Licensing
    # ─────────────────────────────────────────────────────────────────

    def _seed_licensing(self):
        from licenses.models import License, LicenseSeatAssignment
        self.stdout.write('--- Licensing ---')
        self._licenses = []
        seat_assigns = 0
        for slug, tenant in self._tenants.items():
            meta = self._tenant_meta[slug]
            holders = self._tenant_holders[slug]
            hc = max(len(holders), 5)
            code = meta['code']
            plan = [
                ('Microsoft 365 E5', 'subscription_seat', round(hc * 1.2) + 5, 57 * (round(hc * 1.2) + 5), True),
                ('CrowdStrike Falcon', 'subscription_seat', round(hc * 1.2) + 5, 60 * (round(hc * 1.2) + 5), True),
                ('1Password Business', 'subscription_seat', round(hc * 1.1) + 5, 8 * (round(hc * 1.1) + 5), True),
                ('Windows 11 Enterprise', 'perpetual_seat', hc + 10, None, False),
            ]
            if meta['industry'] == 'Pharmaceuticals':
                plan.append(('SAS Analytics Pro', 'subscription_seat', 15, 45000, True))
            if meta['industry'] == 'Architecture & Design':
                plan.append(('Autodesk AutoCAD', 'subscription_seat', 12, 24000, True))
            if meta['industry'] in ('Banking', 'Asset Management'):
                plan.append(('Bloomberg Terminal', 'subscription_seat', 8, 192000, True))
            for sw_name, ltype, seats, cost, has_expiry in plan:
                expiry = days_ahead(random.choice([18, 25, 40, 90, 180, 365])) if has_expiry else None
                lic = License.objects.create(
                    name=f"{code} {sw_name}", software=self._software[sw_name], license_type=ltype,
                    product_key=('' if ltype != 'perpetual_seat' else f"{code}-XXXXX-YYYYY-ZZZZZ"),
                    seats=seats, purchase_cost=cost, purchase_date=days_ago(random.randint(60, 600)),
                    order_number=f"PO-SW-{random.randint(1000, 9999)}", tenant=tenant, expiration_date=expiry)
                self._licenses.append(lic)
                # Assign seats to a sample of holders for the seat-based subscriptions
                if ltype == 'subscription_seat' and holders and sw_name in ('Microsoft 365 E5', 'CrowdStrike Falcon'):
                    for h in random.sample(holders, k=min(len(holders), max(3, len(holders) // 2))):
                        try:
                            LicenseSeatAssignment.objects.create(license=lic, assigned_holder=h)
                            seat_assigns += 1
                        except Exception:
                            pass
        self.stdout.write(f'  {len(self._licenses)} licenses, {seat_assigns} seat assignments.')

    # ─────────────────────────────────────────────────────────────────
    # Subscriptions
    # ─────────────────────────────────────────────────────────────────

    def _seed_subscriptions(self):
        from subscriptions.models import Subscription, SubscriptionAssignment
        from assets.models import Asset
        self.stdout.write('--- Subscriptions ---')
        self._subscriptions = []
        ct_asset = ContentType.objects.get_for_model(Asset)
        # One cloud footprint per organization, owned by its primary tenant.
        for org in self._orgs:
            primary_slug = org['tenants'][0]['slug']
            tenant = self._tenants[primary_slug]
            label = org['group'][0] if org['group'] else org['tenants'][0]['name']
            plan = [('Amazon Web Services', random.randint(30000, 150000)),
                    ('Microsoft Azure', random.randint(40000, 200000))]
            if org['kind'] == 'msp' or random.random() < 0.5:
                plan.append(('GitHub Enterprise', random.randint(4000, 40000)))
            if random.random() < 0.5:
                plan.append(('Datadog', random.randint(8000, 36000)))
            for prov_name, cost in plan:
                start = days_ago(random.randint(60, 700))
                renewal = days_ahead(random.choice([20, 35, 60, 120, 300]))
                sub = Subscription.objects.create(
                    name=f"{label} — {prov_name}", provider=self._providers[prov_name], type='saas',
                    start_date=start, renewal_date=renewal, renewal_cost=cost,
                    term_months=12, description=f"{prov_name} cloud subscription for {label}.", tenant=tenant)
                self._subscriptions.append(sub)
            # Link AWS workloads to a couple of the tenant's servers
            servers = [a for a in self._assets_by_tenant.get(primary_slug, [])
                       if a.asset_role and 'server' in a.asset_role.slug]
            if servers and self._subscriptions:
                for srv in servers[:3]:
                    SubscriptionAssignment.objects.get_or_create(
                        subscription=self._subscriptions[-len(plan)], content_type=ct_asset,
                        object_id=srv.pk, defaults={'notes': 'Hybrid workload node'})
        self.stdout.write(f'  {len(self._subscriptions)} subscriptions across {len(self._orgs)} organizations.')

    # ─────────────────────────────────────────────────────────────────
    # Maintenance
    # ─────────────────────────────────────────────────────────────────

    def _seed_maintenance(self):
        from compliance.models import AssetMaintenance
        self.stdout.write('--- Maintenance ---')
        sample = random.sample(self._assets, k=min(40, len(self._assets)))
        kinds = [('repair', 'Keyboard replacement under warranty', 0),
                 ('repair', 'Display hinge repair', 220),
                 ('upgrade', 'RAM upgrade to 64GB', 480),
                 ('hardware_support', 'Redundant PSU replacement', 1200),
                 ('software_support', 'Firmware / BIOS update', 0),
                 ('calibration', 'Annual RAID battery replacement', 450)]
        count = 0
        for asset in sample:
            mtype, note, cost = random.choice(kinds)
            start = asset.purchase_date + datetime.timedelta(days=random.randint(60, 500))
            if start > TODAY:
                start = days_ago(random.randint(10, 120))
            done = start + datetime.timedelta(days=random.randint(1, 5)) if random.random() < 0.7 else None
            AssetMaintenance.objects.create(
                asset=asset, title=f"{mtype.replace('_', ' ').title()} — {asset.name}",
                maintenance_type=mtype, supplier=self._suppliers[random.choice(self.HW_SUPPLIERS)],
                cost=cost, start_date=start, completion_date=done, notes=note)
            count += 1
        self.stdout.write(f'  {count} maintenance records.')

    # ─────────────────────────────────────────────────────────────────
    # Procurement
    # ─────────────────────────────────────────────────────────────────

    def _seed_procurement(self):
        from procurement.models import PurchaseOrder, PurchaseOrderLine
        self.stdout.write('--- Procurement ---')
        po_count = 0
        line_count = 0
        statuses = ['ordered', 'partial', 'received', 'draft', 'approved']
        target_slugs = ['northwind-internal-it', 'helix-rnd', 'meridian-retail', 'meridian-investment',
                        'sterling-portfolio', 'brightwell-legal', 'aurora-architects', 'vantage-logistics']
        for i, slug in enumerate(target_slugs):
            tenant = self._tenants.get(slug)
            locs = self._tenant_locations.get(slug)
            if not (tenant and locs):
                continue
            meta = self._tenant_meta[slug]
            status = statuses[i % len(statuses)]
            order_date = days_ago(random.randint(5, 90))
            po = PurchaseOrder.objects.create(
                tenant=tenant, order_number=f"{meta['code']}-PO-{order_date.year}-{1000 + i}",
                supplier=self._suppliers[random.choice(self.HW_SUPPLIERS)], status=status,
                order_date=order_date, expected_delivery_date=order_date + datetime.timedelta(days=21),
                destination_location=locs[0], created_by=self._provisioner,
                notes='Quarterly hardware refresh order.')
            po_count += 1
            laptop = random.choice(self.PROFILES[meta['profile']]['laptops'])
            lines = [('asset_type', laptop, random.randint(3, 10)),
                     ('accessory', 'tb4-dock', random.randint(3, 10)),
                     ('asset_type', random.choice(['dell-p2723de-monitor', 'dell-p2422he-monitor']), random.randint(4, 12))]
            for kind, key, qty in lines:
                received = qty if status == 'received' else (qty // 2 if status == 'partial' else 0)
                kwargs = dict(purchase_order=po, tenant=tenant, qty_ordered=qty, qty_received=received,
                              unit_price=round(self.PRICES.get(key, 100) if kind == 'asset_type' else 250, 2))
                if kind == 'asset_type':
                    kwargs['asset_type'] = self._asset_types[key]
                else:
                    kwargs['accessory'] = self._accessories[key]
                PurchaseOrderLine.objects.create(**kwargs)
                line_count += 1
        self.stdout.write(f'  {po_count} purchase orders, {line_count} order lines.')

    # ─────────────────────────────────────────────────────────────────
    # Operations: alerts, reports, event rules, config, dashboards, audit
    # ─────────────────────────────────────────────────────────────────

    def _seed_operations(self):
        from core.models import (NotificationChannel, AlertRule, AlertLog, ReportTemplate, ScheduledReport)
        from extras.models import EventRule, WebhookEndpoint, LabelTemplate, JournalEntry
        from extras.models import ConfigContext, Dashboard
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
        warranty_assets = [a for a in self._assets if a.warranty_expiration and a.warranty_expiration <= days_ahead(60)]
        for a in random.sample(warranty_assets, k=min(15, len(warranty_assets))):
            AlertLog.objects.create(
                rule=rules['warranty_expiry'], content_type=ct_asset, object_id=a.pk,
                subject=f"Warranty for {a.asset_tag} expires {a.warranty_expiration:%Y-%m-%d}",
                message=f"{a.name} ({a.asset_tag}) warranty ends {a.warranty_expiration:%Y-%m-%d}.",
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

        # Config contexts
        ctx_sec = ConfigContext.objects.create(
            name='Baseline Endpoint Security', weight=100,
            description='Security baseline applied to all managed endpoints.',
            data={'disk_encryption': 'required', 'edr_agent': 'CrowdStrike Falcon',
                  'password_manager': '1Password', 'mdm': 'Microsoft Intune', 'screen_lock_minutes': 10})
        ctx_sec.tenants.add(*self._tenants.values())
        pharma_tenants = [t for s, t in self._tenants.items()
                          if self._tenant_meta[s]['industry'] == 'Pharmaceuticals']
        if pharma_tenants:
            ctx_gxp = ConfigContext.objects.create(
                name='GxP Lab Controls', weight=50,
                description='Additional controls for GxP-validated lab and production systems.',
                data={'audit_logging': 'enabled', 'usb_storage': 'blocked',
                      'software_whitelisting': True, 'change_control': 'QMS-required'})
            ctx_gxp.tenants.add(*pharma_tenants)

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
        LabelTemplate.objects.get_or_create(name='Standard QR Asset Label', defaults={
            'description': '2x1 inch QR label', 'barcode_format': 'qr',
            'template_code': ('<table style="width:100%"><tr>'
                              '<td style="width:55%"><div style="font-weight:bold">{{ asset.name }}</div>'
                              '<div style="font-family:monospace">{{ asset.asset_tag }}</div></td>'
                              '<td style="width:45%;text-align:right">{{ barcode_img }}</td></tr></table>')})

        self.stdout.write(f'  {len(rules)} alert rules, {log_count} alert logs, 4 report templates, '
                          f'2 schedules, 2 event rules, config contexts, {audited} audited assets, '
                          f'{req_count} asset requests.')
