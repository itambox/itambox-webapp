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
        parser.add_argument('--force', action='store_true', default=False,
                            help='Required to clear data or run with DEBUG disabled.')

    def handle(self, *args, **options):
        from django.core.management.base import CommandError

        random.seed(42)  # reproducible dataset

        will_clear = not options['skip_drop']

        # This command is destructive: unless --skip-drop is given it TRUNCATEs every
        # domain table via the unfiltered manager. Refuse to do that against a non-DEBUG
        # (production) database unless the operator explicitly passes --force.
        if will_clear and not settings.DEBUG and not options['force']:
            raise CommandError(
                'Refusing to clear data with DEBUG disabled. This deletes ALL domain '
                'records. Re-run with --skip-drop to only add data, or --force if you '
                'really intend to wipe a production database.'
            )

        if will_clear:
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
            ('extras', 'AlertLog'), ('extras', 'AlertRule'),
            ('extras', 'ScheduledReport'), ('extras', 'ReportTemplate'),
            ('extras', 'NotificationChannel'), ('extras', 'EventRule'),
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
            ('extras', 'CustomFieldset'), ('extras', 'CustomField'), ('assets', 'Depreciation'),
            ('extras', 'Tag'),
        ]
        # Delete all rows via the unfiltered manager (bypasses tenant scoping and
        # soft-delete filters). Retry across passes to resolve FK ordering.
        def _unfiltered_qs(model):
            # Prefer all_objects (AllObjectsManager) which skips every filter;
            # fall back to _default_manager which may still scope by tenant/soft-delete.
            mgr = getattr(model, 'all_objects', None) or model._default_manager
            return mgr.all()

        pending = list(models_to_clear)
        last_errors = {}
        for _attempt in range(5):
            failed = []
            for app_label, model_name in pending:
                try:
                    model = apps.get_model(app_label, model_name)
                    count, _ = _unfiltered_qs(model).delete()
                    if count:
                        self.stdout.write(f'  Deleted {count} {model_name}(s)')
                    last_errors.pop(model_name, None)
                except Exception as exc:
                    failed.append((app_label, model_name))
                    last_errors[model_name] = str(exc)
            if not failed:
                break
            pending = failed
        for app_label, model_name in pending:
            self.stdout.write(self.style.WARNING(
                f'  Could not fully clear {model_name}: {last_errors.get(model_name, "unknown")}'))

        User.objects.filter(is_superuser=False).delete()
        ContentType.objects.clear_cache()
        self.stdout.write('  Kept superuser accounts, deleted regular users.')

    # ─────────────────────────────────────────────────────────────────
    # Minimal Seed (--production)
    # ─────────────────────────────────────────────────────────────────

    def _seed_minimal(self):
        import os
        import secrets as _secrets
        from assets.models import StatusLabel
        if not User.objects.filter(is_superuser=True).exists():
            # Never ship a hardcoded credential. Use DJANGO_SUPERUSER_PASSWORD when
            # provided (CI/automation), otherwise generate a strong random password
            # and print it once so the operator can capture it.
            password = os.environ.get('DJANGO_SUPERUSER_PASSWORD')
            generated = password is None
            if generated:
                password = _secrets.token_urlsafe(18)
            username = os.environ.get('DJANGO_SUPERUSER_USERNAME', 'admin')
            email = os.environ.get('DJANGO_SUPERUSER_EMAIL', 'admin@itambox.local')
            User.objects.create_superuser(username=username, email=email, password=password)
            if generated:
                self.stdout.write(self.style.WARNING(
                    f'  Created superuser "{username}" with a generated password: {password}\n'
                    f'  Store it now and change it after first login.'))
            else:
                self.stdout.write(f'  Created superuser "{username}" (password from DJANGO_SUPERUSER_PASSWORD).')
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
            self._seed_changelog()

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

        # Asset roles — (name, slug, color, desc, allows_components)
        self._asset_roles = {}
        for name, slug, color, desc, allows_comp in [
            ('Standard Workstation', 'standard-workstation', '007bff', 'Laptop/desktop for general office staff', True),
            ('Developer Workstation', 'developer-workstation', '6f42c1', 'High-performance workstation for engineers', True),
            ('Executive Workstation', 'executive-workstation', 'e83e8c', 'Premium device for executives', True),
            ('CAD/Design Workstation', 'cad-design-workstation', 'fd7e14', 'GPU workstation for CAD/3D', True),
            ('Lab / Cleanroom Terminal', 'lab-terminal', 'adb5bd', 'Restricted terminal for lab or production-floor use', False),
            ('Field Tablet', 'field-tablet', '20c997', 'Ruggedized tablet for field/warehouse work', False),
            ('Corporate Smartphone', 'corporate-smartphone', 'fd7e14', 'Company smartphone for voice/chat/MFA', False),
            ('Virtualization Host', 'virtualization-host-server', 'dc3545', 'Hypervisor host (ESXi/Proxmox/Hyper-V)', True),
            ('Database Server', 'database-server', '17a2b8', 'Production database host', True),
            ('Application Server', 'application-server', '20c997', 'Line-of-business application host', True),
            ('Backup / Storage', 'backup-server', 'fd7e14', 'Backup target or NAS', True),
            ('Core Router / Firewall', 'core-router-firewall', 'dc3545', 'Edge security gateway', False),
            ('Access / Distribution Switch', 'access-switch', '0d6efd', 'Network switch', False),
            ('Wireless Access Point', 'wireless-ap', '20c997', 'Enterprise WiFi access point', False),
            ('Conference Room AV', 'conference-av', 'e83e8c', 'Meeting-room camera/audio hub', False),
            ('Desktop Monitor', 'desktop-monitor', '6f42c1', 'External display', False),
        ]:
            obj, _ = AssetRole.objects.get_or_create(
                slug=slug,
                defaults={'name': name, 'color': color, 'description': desc, 'allows_components': allows_comp},
            )
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
        from organization.models import Contact, ContactRole, ContactAssignment
        from django.contrib.contenttypes.models import ContentType as CT
        supplier_ct = CT.objects.get_for_model(Supplier)
        primary_role, _ = ContactRole.objects.get_or_create(
            slug='primary-contact',
            defaults={'name': 'Primary Contact', 'description': 'Primary Contact'},
        )
        self._suppliers = {}
        for name, slug, email, phone, website in [
            ('Northwind Procurement', 'northwind-procurement', 'buy@northwind-it.com', '+49-30-555-0100', 'https://northwind-it.com'),
            ('Dell Direct', 'dell-direct', 'enterprise@dell.com', '+1-800-555-0199', 'https://dell.com'),
            ('Apple Business', 'apple-business', 'business@apple.com', '+1-800-555-0200', 'https://apple.com/business'),
            ('CDW Deutschland', 'cdw-deutschland', 'de.sales@cdw.com', '+49-211-555-0500', 'https://cdw.de'),
            ('Bechtle AG', 'bechtle-ag', 'b2b@bechtle.com', '+49-7132-555-0700', 'https://bechtle.com'),
            ('Insight Enterprises', 'insight-enterprises', 'eu@insight.com', '+44-20-555-0800', 'https://insight.com'),
        ]:
            obj, created = Supplier.objects.get_or_create(slug=slug, defaults={
                'name': name, 'website': website})
            if created and not obj.contacts.filter(priority='primary').exists():
                contact = Contact.objects.create(
                    name=f"{name} Contact",
                    phone=phone,
                    email=email,
                )
                ContactAssignment.objects.create(
                    contact=contact,
                    role=primary_role,
                    content_type=supplier_ct,
                    object_id=obj.pk,
                    priority='primary',
                )
            self._suppliers[slug] = obj

        # Depreciation schedules — generic named first (used by asset types)
        self._depreciations = {}
        for name, months in [('3-Year Straight-Line', 36), ('4-Year Straight-Line', 48),
                             ('5-Year Straight-Line', 60), ('7-Year Straight-Line', 84)]:
            obj, _ = Depreciation.objects.get_or_create(name=name, defaults={'months': months})
            self._depreciations[name] = obj

        # German AfA / GWG example policies for the demo tenant (opt-in via seed_data;
        # migration 0043 also seeds these on every install — see FIX-05 note).
        _afa_policies = [
            {
                'name': 'IT-Hardware 36 Monate (AfA)',
                'months': 36,
                'method': 'straight_line',
                'convention': 'include_purchase_month',
                'description': 'AfA-Tabelle 2021 — Computer, Notebooks, Tablets: 3 Jahre',
            },
            {
                'name': 'Server 60 Monate (AfA)',
                'months': 60,
                'method': 'straight_line',
                'convention': 'include_purchase_month',
                'description': 'AfA-Tabelle 2021 — Server / Workstations: 5 Jahre',
            },
            {
                'name': 'Sofortabschreibung GWG (≤ 800 €)',
                'months': 1,
                'method': 'straight_line',
                'convention': 'include_purchase_month',
                'immediate_expense_threshold': '800.00',
                'description': 'Geringwertige Wirtschaftsgüter §6 Abs. 2 EStG — Sofortabschreibung bis 800 €',
            },
        ]
        self._demo_depreciation_afa = None
        for p in _afa_policies:
            obj, _ = Depreciation.objects.get_or_create(
                name=p['name'],
                defaults={k: v for k, v in p.items() if k != 'name'},
            )
            if self._demo_depreciation_afa is None:
                self._demo_depreciation_afa = obj  # first entry = tenant default showcase

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
                'choices': choices or ''})
            # model_level=True described the hardware type (a spec); otherwise
            # the field is a per-device detail on the asset.
            obj.object_types.add(assettype_ct if model_level else asset_ct)
            self._custom_fields[name] = obj

        def fieldset(name, *field_names):
            fs, _ = CustomFieldset.objects.get_or_create(name=name)
            fs.fields.set([self._custom_fields[f] for f in field_names])
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
        """Compact specification of the MSP and its customers.

        Each multi-tenant 'group' is a corporate family of distinct **legal
        entities** (different legal forms, registered seats and — where the
        country differs — currencies), not a single company split by function.
        Tenant slugs/codes/profiles/sites stay stable; only the legal identity
        (name suffix, currency, registered seat + commercial-register number)
        is added so downstream slug-based wiring keeps working.
        """
        return [
            # kind, group(name,slug,description), domain, [tenants...]
            dict(kind='msp',
                 group=('Northwind Managed Services Group', 'northwind-msp',
                        'Berlin-based managed-service provider: the operating company plus its corporate holding.'),
                 domain='northwind-it.com',
                 tenants=[
                     dict(name='Northwind Managed Services GmbH', slug='northwind-internal-it', code='NW-IT',
                          profile='msp_internal', headcount=12, currency='EUR',
                          legal_seat='Berlin, Germany', reg_no='HRB 121544 B (Amtsgericht Berlin-Charlottenburg)',
                          site=('Northwind Berlin HQ', 'nw-berlin-hq', 'Berlin', 'Friedrichstrasse 88\n10117 Berlin\nGermany',
                                'dach', 'corporate-offices', '52.5200', '13.4050'),
                          extra_sites=[('Northwind Frankfurt DC', 'nw-frankfurt-dc', 'Frankfurt',
                                        'Hanauer Landstrasse 200\n60314 Frankfurt\nGermany', 'dach', 'datacenters', '50.1109', '8.6821')],
                          locations=[('Engineering Floor', 'nw-eng-floor'), ('Service Desk', 'nw-service-desk'),
                                     ('DC Rack Row 1', 'nw-dc-rack-1'), ('DC Rack Row 2', 'nw-dc-rack-2')]),
                     dict(name='Northwind Services Holding GmbH', slug='northwind-corporate', code='NW-CORP',
                          profile='msp_corp', headcount=16, currency='EUR',
                          legal_seat='Berlin, Germany', reg_no='HRB 121310 B (Amtsgericht Berlin-Charlottenburg)',
                          site=('Northwind Berlin HQ', 'nw-berlin-hq', 'Berlin', '', 'dach', 'corporate-offices', None, None),
                          locations=[('Finance & HR', 'nw-finance-hr'), ('Sales Floor', 'nw-sales-floor')]),
                 ]),
            dict(kind='customer', industry='Pharmaceuticals',
                 group=('Helix Biopharma Group', 'helix-biopharma',
                        'Swiss life-sciences group: research (Basel), production (Visp) and commercial (Zürich) entities.'),
                 domain='helixbio.com',
                 tenants=[
                     dict(name='Helix Biopharma AG', slug='helix-rnd', code='HLX-RD', profile='pharma_rnd', headcount=22,
                          currency='CHF', legal_seat='Basel, Switzerland', reg_no='CHE-114.227.911 (Handelsregister Basel-Stadt)',
                          site=('Helix Basel Research Campus', 'helix-basel', 'Basel',
                                'Hochbergerstrasse 60\n4057 Basel\nSwitzerland', 'western-europe', 'labs-plants', '47.5596', '7.5886'),
                          locations=[('Lab Block A', 'helix-lab-a'), ('Lab Block B', 'helix-lab-b'),
                                     ('R&D Offices', 'helix-rnd-offices'), ('Server Room', 'helix-basel-srv')]),
                     dict(name='Helix Biopharma Production GmbH', slug='helix-mfg', code='HLX-MF', profile='pharma_mfg', headcount=16,
                          currency='CHF', legal_seat='Visp, Switzerland', reg_no='CHE-217.034.882 (Handelsregister Wallis)',
                          site=('Helix Visp Plant', 'helix-visp', 'Visp',
                                'Schachenstrasse 12\n3930 Visp\nSwitzerland', 'western-europe', 'labs-plants', '46.2940', '7.8810'),
                          locations=[('Production Line 1', 'helix-line-1'), ('Production Line 2', 'helix-line-2'),
                                     ('QA Lab', 'helix-qa-lab'), ('Plant IT Room', 'helix-visp-srv')]),
                     dict(name='Helix Biopharma Commercial AG', slug='helix-commercial', code='HLX-CO', profile='pharma_commercial', headcount=14,
                          currency='CHF', legal_seat='Zürich, Switzerland', reg_no='CHE-330.519.704 (Handelsregister Zürich)',
                          site=('Helix Zurich Office', 'helix-zurich', 'Zurich',
                                'Bahnhofstrasse 45\n8001 Zurich\nSwitzerland', 'western-europe', 'corporate-offices', '47.3769', '8.5417'),
                          locations=[('Commercial Floor', 'helix-commercial-floor'), ('Meeting Suites', 'helix-meeting-suites')]),
                 ]),
            dict(kind='customer', industry='Banking',
                 group=('Meridian Capital Group', 'meridian-bank',
                        'Cross-border banking group: German retail bank (AG), UK capital-markets arm (plc) and a shared risk-services entity.'),
                 domain='meridianbank.com',
                 tenants=[
                     dict(name='Meridian Bank AG', slug='meridian-retail', code='MER-RT', profile='bank_retail', headcount=26,
                          currency='EUR', legal_seat='Frankfurt am Main, Germany', reg_no='HRB 88245 (Amtsgericht Frankfurt am Main)',
                          site=('Meridian Frankfurt Tower', 'meridian-frankfurt', 'Frankfurt',
                                'Taunusanlage 12\n60325 Frankfurt\nGermany', 'dach', 'corporate-offices', '50.1109', '8.6700'),
                          locations=[('Retail Floor 2', 'mer-retail-f2'), ('Retail Floor 3', 'mer-retail-f3'),
                                     ('Branch Ops', 'mer-branch-ops'), ('Data Center', 'mer-frankfurt-dc')]),
                     dict(name='Meridian Capital Markets plc', slug='meridian-investment', code='MER-IB', profile='bank_invest', headcount=16,
                          currency='GBP', legal_seat='London, United Kingdom', reg_no='Companies House 09183477',
                          site=('Meridian London Office', 'meridian-london', 'London',
                                '30 St Mary Axe\nLondon EC3A 8BF\nUnited Kingdom', 'western-europe', 'corporate-offices', '51.5145', '-0.0803'),
                          locations=[('Trading Floor', 'mer-trading-floor'), ('Deal Rooms', 'mer-deal-rooms')]),
                     dict(name='Meridian Risk Services GmbH', slug='meridian-risk', code='MER-RC', profile='bank_risk', headcount=10,
                          currency='EUR', legal_seat='Frankfurt am Main, Germany', reg_no='HRB 90112 (Amtsgericht Frankfurt am Main)',
                          site=('Meridian Frankfurt Tower', 'meridian-frankfurt', 'Frankfurt', '', 'dach', 'corporate-offices', None, None),
                          locations=[('Risk Analytics', 'mer-risk-analytics')]),
                 ]),
            dict(kind='customer', industry='Asset Management',
                 group=('Sterling Asset Management Group', 'sterling-am',
                        'Munich fund manager (KVG) with a dedicated fund-services entity.'),
                 domain='sterling-am.com',
                 tenants=[
                     dict(name='Sterling Asset Management GmbH', slug='sterling-portfolio', code='STG-PM', profile='fund_portfolio', headcount=14,
                          currency='EUR', legal_seat='Munich, Germany', reg_no='HRB 204417 (Amtsgericht München)',
                          site=('Sterling Munich Office', 'sterling-munich', 'Munich',
                                'Maximilianstrasse 35\n80539 Munich\nGermany', 'dach', 'corporate-offices', '48.1391', '11.5802'),
                          locations=[('Portfolio Desk', 'stg-portfolio-desk'), ('Partner Suites', 'stg-partner-suites'),
                                     ('Server Closet', 'stg-server-closet')]),
                     dict(name='Sterling Fund Services GmbH', slug='sterling-ops', code='STG-OP', profile='fund_ops', headcount=8,
                          currency='EUR', legal_seat='Munich, Germany', reg_no='HRB 204902 (Amtsgericht München)',
                          site=('Sterling Munich Office', 'sterling-munich', 'Munich', '', 'dach', 'corporate-offices', None, None),
                          locations=[('Fund Operations', 'stg-fund-ops')]),
                 ]),
            dict(kind='customer', industry='Legal Services', group=None, domain='brightwell-legal.com',
                 tenants=[
                     dict(name='Brightwell Legal PartG mbB', slug='brightwell-legal', code='BWL', profile='legal', headcount=16,
                          currency='EUR', legal_seat='Berlin, Germany', reg_no='PR 1284 B (Partnerschaftsregister Berlin)',
                          site=('Brightwell Berlin Chambers', 'brightwell-berlin', 'Berlin',
                                'Kurfurstendamm 21\n10719 Berlin\nGermany', 'dach', 'corporate-offices', '52.5030', '13.3270'),
                          locations=[('Partner Offices', 'bwl-partner-offices'), ('Associate Bullpen', 'bwl-associates'),
                                     ('Records Room', 'bwl-records')]),
                 ]),
            dict(kind='customer', industry='Architecture & Design', group=None, domain='aurora-arch.com',
                 tenants=[
                     dict(name='Aurora Architekten GmbH', slug='aurora-architects', code='AUR', profile='architecture', headcount=11,
                          currency='EUR', legal_seat='Hamburg, Germany', reg_no='HRB 167733 (Amtsgericht Hamburg)',
                          site=('Aurora Hamburg Studio', 'aurora-hamburg', 'Hamburg',
                                'Am Kaiserkai 10\n20457 Hamburg\nGermany', 'dach', 'corporate-offices', '53.5413', '9.9920'),
                          locations=[('Design Studio', 'aur-design-studio'), ('Render Farm', 'aur-render-farm')]),
                 ]),
            dict(kind='customer', industry='Logistics', group=None, domain='vantage-logistics.com',
                 tenants=[
                     dict(name='Vantage Logistics GmbH & Co. KG', slug='vantage-logistics', code='VAN', profile='logistics', headcount=18,
                          currency='EUR', legal_seat='Frankfurt am Main, Germany', reg_no='HRA 49882 (Amtsgericht Frankfurt am Main)',
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
                gname, gslug, gdesc = org['group']
                group_obj, _ = TenantGroup.objects.get_or_create(
                    slug=gslug, defaults={'name': gname, 'description': gdesc})
                self._tgroups[gslug] = group_obj

            for t in org['tenants']:
                # Per-entity depreciation default: German (EUR) entities default to the German
                # AfA 36-month policy so the "tenant-level default" resolution rung is visible;
                # foreign-currency entities fall back to a generic straight-line schedule.
                currency = t.get('currency', 'EUR')
                default_dep = (self._demo_depreciation_afa if currency == 'EUR'
                               else self._depreciations.get('3-Year Straight-Line'))
                industry = org.get('industry', 'Managed Service Provider')
                description = (
                    f"{t['name']} — {industry} entity of "
                    f"{(org['group'][0] if org['group'] else t['name'])}.\n"
                    f"Registered seat: {t['legal_seat']} · {t['reg_no']} · functional currency {currency}.\n"
                    f"IT managed by Northwind Managed Services GmbH."
                )
                tenant, _ = Tenant.objects.get_or_create(slug=t['slug'], defaults={
                    'name': t['name'], 'group': group_obj, 'currency': currency,
                    'default_depreciation': default_dep, 'description': description})
                self._tenants[t['slug']] = tenant
                self._tenant_meta[t['slug']] = dict(profile=t['profile'], domain=org['domain'], code=t['code'],
                                                    group_slug=org['group'][1] if org['group'] else None,
                                                    industry=org.get('industry'), kind=org['kind'],
                                                    currency=currency, legal_seat=t['legal_seat'])
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

        # Vendor-support reps attached to the hardware manufacturers (exercises
        # Manufacturer.contacts + the vendor-support role).
        vendor_contacts = {
            'dell-technologies': ('Dell ProSupport Plus Desk', 'support.de@dell.com', '+49-69-9792-0000'),
            'apple-inc': ('Apple Business Care', 'business.eu@apple.com', '+49-800-2000-136'),
            'hp-inc': ('HP Care Pack Support', 'enterprise.support@hp.com', '+49-7031-986-0'),
            'lenovo-group': ('Lenovo Premier Support', 'premier.eu@lenovo.com', '+49-711-6517-0'),
            'cisco-systems': ('Cisco TAC EMEA', 'tac@cisco.com', '+31-20-357-1000'),
            'samsung-electronics': ('Samsung B2B Support', 'b2b.support@samsung.com', '+49-6196-77-55555'),
            'microsoft-corporation': ('Microsoft Premier Support', 'premier@microsoft.com', '+49-89-31760-0'),
            'logitech-international': ('Logitech B2B Care', 'b2bsupport@logitech.com', '+41-21-863-5111'),
            'brother-industries': ('Brother Business Support', 'support@brother.de', '+49-6172-863-0'),
            'synology-inc': ('Synology Technical Support', 'support@synology.com', '+49-89-3838-2700'),
            'ubiquiti-inc': ('Ubiquiti Enterprise Support', 'support@ui.com', '+1-844-674-4357'),
        }
        mfr_ct = ContentType.objects.get_for_model(next(iter(self._manufacturers.values())))
        for mfr_slug, (cname, cemail, cphone) in vendor_contacts.items():
            mfr = self._manufacturers.get(mfr_slug)
            if not mfr:
                continue
            vc = Contact.objects.create(name=cname, title='Vendor Support', email=cemail, phone=cphone)
            self._contacts.append(vc)
            ContactAssignment.objects.get_or_create(
                contact=vc, role=self._contact_roles['vendor-support'],
                content_type=mfr_ct, object_id=mfr.pk, defaults={'priority': 'primary'})

        # MSP account managers as contacts on the MSP operating entity.
        msp_tenant = self._tenants['northwind-internal-it']
        msp_ct = ContentType.objects.get_for_model(msp_tenant)
        for am_name, am_email in [('Nadia Haas', 'nadia.haas@northwind-it.com'),
                                  ('Peter Voss', 'peter.voss@northwind-it.com')]:
            ac = Contact.objects.create(name=am_name, title='Account Manager, Northwind Managed Services',
                                        email=am_email, phone='+49-30-555-0%03d' % random.randint(100, 999))
            self._contacts.append(ac)
            ContactAssignment.objects.get_or_create(
                contact=ac, role=self._contact_roles['account-manager'],
                content_type=msp_ct, object_id=msp_tenant.pk, defaults={'priority': 'secondary'})

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
        from assets.models import Asset, AssetAssignment, AssetTagSequence
        from software.models import InstalledSoftware
        from inventory.models import ComponentAllocation
        from compliance.models import CustodyTemplate, CustodyReceipt
        self.stdout.write('--- Assets ---')

        # Global custody templates (category-matched), fully configured with QMS
        # references, disclaimers and tags.
        self._custody_templates = {}
        for name, slug, cat_slug, eula, tag_slugs in [
            ('Standard Workstation & Laptop Agreement', 'laptop-agreement', 'laptops',
             'I acknowledge receipt of the issued laptop/workstation and agree to the acceptable-use and '
             'disk-encryption policy. The equipment remains company property and must be returned on demand.',
             ['production', 'encrypted']),
            ('Mobile Device Agreement', 'mobile-agreement', 'mobile-phones',
             'I acknowledge receipt of the mobile device and SIM. I will keep it secured with a passcode/biometrics '
             'and will not remove the mobile device management (MDM) profile.',
             ['mdm-enrolled']),
            ('Desktop Workstation Agreement', 'desktop-agreement', 'desktops',
             'I acknowledge custody of the desktop workstation and agree not to modify its hardware or connect it to '
             'unauthorized networks without IT approval.',
             ['production']),
            ('Field Tablet Agreement', 'tablet-agreement', 'tablets',
             'I acknowledge receipt of the ruggedized field tablet and agree to return it undamaged at the end of '
             'my assignment. Lost or damaged devices must be reported to the service desk within 24 hours.',
             ['field', 'mdm-enrolled']),
        ]:
            tmpl = CustodyTemplate.objects.get_or_create(name=name, defaults={
                'category': self._categories[cat_slug], 'eula_text': eula,
                'disclaimer': 'This equipment remains the property of the organization.',
                'qms_reference': f'NMS-IT-{slug.upper()}', 'is_active': True, 'require_acceptance': True,
                'email_signature_request': True, 'signature_provider': 'local'})[0]
            tmpl.tags.set([self._tags[t] for t in tag_slugs if t in self._tags])
            self._custody_templates[cat_slug] = tmpl

        # Tenant-scoped, regulator-specific agreements (PCI / GxP) — show per-tenant
        # custody configuration layered on top of the global defaults.
        for tname, tslug, cat_slug, qms, eula, tags in [
            ('Meridian — PCI-DSS Laptop Custody Agreement', 'meridian-retail', 'laptops', 'MER-PCI-IT-007',
             'I acknowledge that this device operates within PCI-DSS scope. I will not store cardholder data locally, '
             'will keep the endpoint agent active and will comply with quarterly access reviews.', ['pci-scope', 'critical']),
            ('Brightwell — Confidential Records Custody Agreement', 'brightwell-legal', 'laptops', 'BWL-LEG-IT-003',
             'I acknowledge custody of a device with access to privileged client matter files and agree to full-disk '
             'encryption and the firm’s information-barrier policy.', ['encrypted', 'vip']),
        ]:
            tenant = self._tenants.get(tslug)
            if not tenant:
                continue
            t = CustodyTemplate.objects.get_or_create(name=tname, defaults={
                'tenant': tenant, 'category': self._categories[cat_slug], 'eula_text': eula,
                'disclaimer': 'Regulated equipment — handle per the referenced policy.',
                'qms_reference': qms, 'is_active': True, 'require_acceptance': True,
                'email_signature_request': True, 'signature_provider': 'local'})[0]
            t.tags.set([self._tags[x] for x in tags if x in self._tags])
            self._custody_templates.setdefault(f'{tslug}:{cat_slug}', t)

        # A group-scoped (tenant_group) GxP laptop agreement shared across all Helix
        # Biopharma entities — exercises CustodyTemplate.tenant_group / group-wide sharing.
        helix_group = self._tgroups.get('helix-biopharma')
        self._gxp_custody_template = None
        if helix_group:
            self._gxp_custody_template = CustodyTemplate.objects.get_or_create(
                name='Helix Biopharma — GxP Laptop & Workstation Agreement', defaults={
                    'tenant_group': helix_group, 'category': self._categories['laptops'],
                    'eula_text': ('I acknowledge receipt of a GxP-validated workstation. I will not install '
                                  'unvalidated software, will keep audit logging enabled and will follow the '
                                  'Helix Biopharma QMS change-control procedure for any modification.'),
                    'disclaimer': 'GxP-validated equipment — subject to QMS change control.',
                    'qms_reference': 'HLX-QMS-IT-014', 'is_active': True, 'require_acceptance': True,
                    'email_signature_request': True, 'signature_provider': 'local'})[0]

        self._assets = []
        self._assets_by_tenant = {}
        self._laptops_by_tenant = {}
        self._primary_laptop_by_holder = {}   # holder.pk -> current laptop (for license seats)
        self._retired_assets = []
        self._servers = []

        # Per-tenant asset-tag sequence (prefix = tenant code). Assets are created with a
        # blank asset_tag so Asset.save() draws the next value from AssetTagSequence — the
        # product's real numbering mechanism — instead of hand-formatted tags.
        for slug, tenant in self._tenants.items():
            AssetTagSequence.objects.get_or_create(
                tenant=tenant, category=None,
                defaults={'prefix': f"{self._tenant_meta[slug]['code']}-",
                          'zero_padding': 4, 'next_value': 1, 'is_active': True})

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
            base_cost = self.PRICES.get(atype_slug, 1000)
            cost = round(base_cost * random.uniform(0.95, 1.05), 2)
            years_old = random.choice([0, 1, 1, 2, 2, 3])
            p_date = days_ago(years_old * 365 + random.randint(0, 300))
            warranty = p_date + datetime.timedelta(days=(atype.eol_months or 36) * 30)
            fs_name = atype.custom_fieldset.name if atype.custom_fieldset else ''

            base_location = None if (holder and status_slug == 'in-use') else location
            salvage = round(cost * 0.1, 2)
            # In service a few days after purchase; book value straight-lined to salvage over EoL.
            in_service = p_date + datetime.timedelta(days=random.randint(2, 14))
            eol_months = atype.eol_months or 36
            age_months = max(0, (TODAY - p_date).days / 30.0)
            fraction = min(age_months / eol_months, 1.0)
            book_value = round(cost - (cost - salvage) * fraction, 2)
            # Blank asset_tag → Asset.save() draws the next value from the tenant's
            # AssetTagSequence. Tag-derived fields (hostname, display name) are filled
            # in the second save, once the generated tag is known.
            asset = Asset(
                name=f"{atype.model} (provisioning)", asset_tag='', asset_type=atype, asset_role=role,
                status=self._status_labels[status_slug], location=base_location, tenant=tenant,
                serial_number=f"{code}{random.randint(100000, 999999)}", purchase_cost=cost,
                salvage_value=salvage, purchase_date=p_date, in_service_date=in_service,
                current_book_value=book_value, depreciation_updated_at=timezone.now(),
                supplier=self._suppliers[random.choice(self.HW_SUPPLIERS)],
                order_number=f"PO-{p_date.year}-{random.randint(1000, 9999)}", custom_field_data={})
            asset.save()
            from assets.models import Warranty, WarrantyTypeChoices
            Warranty.objects.create(
                asset=asset, warranty_type=WarrantyTypeChoices.HARDWARE,
                provider=asset.supplier.name if asset.supplier else '',
                start_date=p_date, end_date=warranty,
            )
            tag = asset.asset_tag
            host = f"{code.lower()}-{tag.split('-')[-1]}"
            cv = {}
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
            asset.name = f"{atype.model} ({tag})"
            asset.custom_field_data = cv
            asset.save(update_fields=['name', 'custom_field_data'])
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
                # ~25% of laptops carry re-issue history: a prior, closed assignment to a
                # different employee who returned the device (lifecycle realism).
                if len(holders) > 3 and random.random() < 0.25:
                    prev = random.choice([h for h in holders if h.pk != holder.pk])
                    co = timezone.now() - datetime.timedelta(days=random.randint(220, 700))
                    ci = co + datetime.timedelta(days=random.randint(60, 200))
                    AssetAssignment.objects.create(
                        asset=lt, assigned_user=prev, checked_out_by=self._provisioner,
                        checked_out_at=co, is_active=False, checked_in_at=ci,
                        checked_in_by=self._provisioner,
                        notes='Previously issued; returned to the service desk on a role change.')
                self._laptops_by_tenant[slug].append(lt)
                self._assets_by_tenant[slug].append(lt)
                self._primary_laptop_by_holder[holder.pk] = lt
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

            # A couple of retired/replaced devices per larger tenant: the old unit was
            # superseded by a current in-use laptop (lifecycle + disposal realism).
            if len(holders) > 6:
                for _ in range(random.randint(1, 2)):
                    replacement = random.choice(self._laptops_by_tenant[slug]) if self._laptops_by_tenant[slug] else None
                    old = make_asset(tenant, code, random.choice(profile['laptops']), 'retired', None,
                                     locs[0] if locs else None, random.choice(profile['depts']),
                                     tags=['legacy'])
                    disposed = timezone.now() - datetime.timedelta(days=random.randint(20, 180))
                    old.disposed_at = disposed
                    old.disposal_value = round(float(old.purchase_cost) * random.uniform(0.03, 0.12), 2)
                    old.current_book_value = 0
                    if replacement:
                        old.notes = f"Decommissioned and superseded by {replacement.asset_tag} ({replacement.name})."
                    old.save(update_fields=['disposed_at', 'disposal_value', 'current_book_value', 'notes'])
                    self._assets_by_tenant[slug].append(old)
                    self._retired_assets.append(old)

        # Installed software — every managed endpoint carries a security/productivity
        # baseline (discovered by the MDM/inventory agent), plus role- and industry-
        # specific titles. Versions are populated so the software inventory looks real.
        sw_versions = {
            'Microsoft 365 E5': ['16.83.2', '16.84.1', '16.85.0'],
            'CrowdStrike Falcon': ['7.16.18019', '7.17.18101', '7.18.18206'],
            '1Password Business': ['8.10.40', '8.10.42', '8.10.44'],
            'Zoom Workplace Enterprise': ['6.0.10', '6.1.5', '6.2.0'],
            'Microsoft Office LTSC 2024': ['16.0.17328', '16.0.17425'],
            'Adobe Creative Cloud': ['6.4.0.345', '6.5.0.348'],
            'JetBrains All Products Pack': ['2024.1.4', '2024.2.1'],
            'Autodesk AutoCAD': ['2024.1.3', '2025.0.1'],
            'Bloomberg Terminal': ['DAPI-4.18', 'DAPI-4.19'],
            'SAS Analytics Pro': ['9.4M8', '9.4M9'],
        }

        def install(asset, sw_name, agent, install_date):
            sw = self._software.get(sw_name)
            if not sw:
                return 0
            ver = random.choice(sw_versions.get(sw_name, ['']))
            _, created = InstalledSoftware.objects.get_or_create(
                asset=asset, software=sw, version_detected=ver,
                defaults={'discovered_by_agent': agent, 'install_date': install_date,
                          'last_seen_date': timezone.now() - datetime.timedelta(days=random.randint(0, 14))})
            return int(created)

        sw_count = 0
        for slug in self._tenants:
            meta = self._tenant_meta[slug]
            for lt in self._laptops_by_tenant[slug]:
                agent = 'Lansweeper' if meta['industry'] == 'Asset Management' else 'Intune'
                # OS title (resolve the detected OS string back to a catalogue product).
                os_str = lt.custom_field_data.get('os_version', 'Windows 11 23H2')
                os_sw = ('macOS Sequoia' if 'macOS' in os_str
                         else 'Ubuntu Pro 24.04' if 'Ubuntu' in os_str
                         else 'Windows 11 Enterprise')
                os_obj = self._software.get(os_sw)
                if os_obj:
                    _, c = InstalledSoftware.objects.get_or_create(
                        asset=lt, software=os_obj, version_detected=os_str,
                        defaults={'discovered_by_agent': agent, 'install_date': lt.purchase_date,
                                  'last_seen_date': timezone.now() - datetime.timedelta(days=random.randint(0, 7))})
                    sw_count += int(c)
                # Security + productivity baseline on every laptop.
                for sw_name in ('Microsoft 365 E5', 'CrowdStrike Falcon', '1Password Business',
                                'Zoom Workplace Enterprise'):
                    sw_count += install(lt, sw_name, agent, lt.purchase_date)
                # Role / industry specific titles.
                role_slug = lt.asset_role.slug if lt.asset_role else ''
                if role_slug == 'developer-workstation':
                    sw_count += install(lt, 'JetBrains All Products Pack', agent, lt.purchase_date)
                if meta['industry'] == 'Architecture & Design':
                    sw_count += install(lt, 'Autodesk AutoCAD', agent, lt.purchase_date)
                    sw_count += install(lt, 'Adobe Creative Cloud', agent, lt.purchase_date)
                if meta['industry'] in ('Banking', 'Asset Management') and random.random() < 0.4:
                    sw_count += install(lt, 'Bloomberg Terminal', agent, lt.purchase_date)
                if meta['industry'] == 'Pharmaceuticals' and random.random() < 0.3:
                    sw_count += install(lt, 'SAS Analytics Pro', agent, lt.purchase_date)
                if meta['kind'] == 'msp' and random.random() < 0.5:
                    sw_count += install(lt, 'Microsoft Office LTSC 2024', agent, lt.purchase_date)
            # Mobile devices report MDM-managed apps too.
            for a in self._assets_by_tenant[slug]:
                if a.asset_type and a.asset_type.category and a.asset_type.category.slug == 'mobile-phones':
                    for sw_name in ('Microsoft 365 E5', '1Password Business'):
                        sw_count += install(a, sw_name, 'Intune', a.purchase_date)
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
            # Sign receipts for regulated industries plus any tenant that has its own
            # tenant-scoped custody template (e.g. Brightwell Legal).
            has_scoped = any(k.startswith(f'{slug}:') for k in self._custody_templates)
            if self._tenant_meta[slug]['industry'] not in ('Pharmaceuticals', 'Banking') and not has_scoped:
                continue
            is_helix = self._tenant_meta[slug]['group_slug'] == 'helix-biopharma'
            for asset in self._assets_by_tenant[slug]:
                cat = asset.asset_type.category.slug if asset.asset_type and asset.asset_type.category else None
                # Preference: tenant-scoped template > group GxP (Helix laptops) > global default.
                if f'{slug}:{cat}' in self._custody_templates:
                    tmpl = self._custody_templates[f'{slug}:{cat}']
                elif is_helix and cat == 'laptops' and self._gxp_custody_template:
                    tmpl = self._gxp_custody_template
                else:
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
                                       ComponentStock, AccessoryAssignment, ConsumableAssignment, Kit, KitItem)
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

        # Spare-parts (component) stock held at server rooms / DC racks. The MSP holds
        # the deepest spares pool; tenants with their own server location keep a few.
        comp_count = 0

        def _infra_loc(tslug):
            for kw in ('srv', 'rack', 'dc', 'server', 'network', 'closet', 'cabinet', 'farm'):
                for lo in self._tenant_locations.get(tslug, []):
                    if kw in lo.slug:
                        return lo
            return None

        # Deep central spares pool at the MSP Frankfurt DC (or its first infra location).
        msp_loc = _infra_loc('northwind-internal-it') or self._tenant_locations['northwind-internal-it'][0]
        for comp_slug, comp in self._components.items():
            ComponentStock.objects.get_or_create(
                component=comp, location=msp_loc,
                defaults={'qty': random.choice([2, 4, 5, 8, 12, 0])})  # one 0 → low-stock signal
            comp_count += 1
        # A shallower spares pool at customer server rooms.
        for tslug in self._tenants:
            loc = _infra_loc(tslug)
            if not loc or tslug == 'northwind-internal-it':
                continue
            for comp_slug in random.sample(list(self._components), k=random.randint(2, 4)):
                ComponentStock.objects.get_or_create(
                    component=self._components[comp_slug], location=loc,
                    defaults={'qty': random.choice([1, 2, 3, 4])})
                comp_count += 1

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
                          f'{stock_count} accessory/consumable stock rows, {comp_count} component stock rows, '
                          f'{assign_count} accessory assignments, {len(kits)} kits.')

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
                # Assign seats to a sample of holders for the seat-based subscriptions.
                # Per-user products (e.g. Microsoft 365 E5) are user-bound — the seat
                # targets the holder. Per-device products (e.g. CrowdStrike Falcon, an
                # endpoint agent) are device-bound — the seat targets the holder's
                # primary laptop when known. The model enforces asset XOR holder.
                DEVICE_BOUND_SOFTWARE = {'CrowdStrike Falcon'}
                if ltype == 'subscription_seat' and holders and sw_name in ('Microsoft 365 E5', 'CrowdStrike Falcon'):
                    device_bound = sw_name in DEVICE_BOUND_SOFTWARE
                    for h in random.sample(holders, k=min(len(holders), max(3, len(holders) // 2))):
                        try:
                            laptop = self._primary_laptop_by_holder.get(h.pk)
                            if device_bound and laptop is not None:
                                LicenseSeatAssignment.objects.create(license=lic, asset=laptop)
                            else:
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
        # One cloud footprint per organization, contracted centrally by the parent
        # entity (the group's primary tenant) and consumed across its sibling entities.
        x_entity = 0
        for org in self._orgs:
            primary_slug = org['tenants'][0]['slug']
            tenant = self._tenants[primary_slug]
            currency = self._tenant_meta[primary_slug]['currency']
            label = org['group'][0] if org['group'] else org['tenants'][0]['name']
            plan = [('Amazon Web Services', random.randint(30000, 150000)),
                    ('Microsoft Azure', random.randint(40000, 200000))]
            if org['kind'] == 'msp' or random.random() < 0.5:
                plan.append(('GitHub Enterprise', random.randint(4000, 40000)))
            if random.random() < 0.5:
                plan.append(('Datadog', random.randint(8000, 36000)))
            aws_sub = None
            for prov_name, cost in plan:
                start = days_ago(random.randint(60, 700))
                renewal = days_ahead(random.choice([20, 35, 60, 120, 300]))
                sub = Subscription.objects.create(
                    name=f"{label} — {prov_name}", provider=self._providers[prov_name], type='saas',
                    start_date=start, renewal_date=renewal, renewal_cost=cost, currency=currency,
                    billing_cycle='annual', term_months=12, auto_renewal=True,
                    contract_reference=f"MSA-{prov_name.split()[0].upper()}-{start.year}",
                    owner=self._provisioner,
                    description=f"{prov_name} cloud subscription — group contract held by {tenant.name}.",
                    tenant=tenant)
                self._subscriptions.append(sub)
                if prov_name == 'Amazon Web Services':
                    aws_sub = sub
            # Cross-entity consumption: assign the group AWS contract to servers in EVERY
            # entity of the group, not just the contracting one (shared-service realism).
            if aws_sub:
                for t in org['tenants']:
                    servers = [a for a in self._assets_by_tenant.get(t['slug'], [])
                               if a.asset_role and 'server' in a.asset_role.slug]
                    sibling = t['slug'] != primary_slug
                    for srv in servers[:3]:
                        _, made = SubscriptionAssignment.objects.get_or_create(
                            subscription=aws_sub, content_type=ct_asset, object_id=srv.pk,
                            defaults={'assigned_by': self._provisioner,
                                      'notes': ('Hybrid workload node (intercompany — billed to group contract)'
                                                if sibling else 'Hybrid workload node')})
                        if made and sibling:
                            x_entity += 1
        self.stdout.write(f'  {len(self._subscriptions)} subscriptions across {len(self._orgs)} organizations, '
                          f'{x_entity} cross-entity workload assignments.')

    # ─────────────────────────────────────────────────────────────────
    # Maintenance
    # ─────────────────────────────────────────────────────────────────

    def _seed_maintenance(self):
        from assets.models import AssetMaintenance
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
        from assets.models import Asset
        self.stdout.write('--- Procurement ---')
        po_count = 0
        line_count = 0
        fulfilled = 0
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
                line = PurchaseOrderLine.objects.create(**kwargs)
                line_count += 1
                # Received asset-type lines materialise into real Assets that point back
                # to the originating PO line (Asset.purchase_order_line) — closes the
                # order → inventory loop instead of leaving received qty abstract.
                if kind == 'asset_type' and received:
                    atype = self._asset_types[key]
                    for n in range(min(received, 3)):
                        cost = round(float(line.unit_price or 0) * random.uniform(0.97, 1.03), 2)
                        a = Asset(
                            name=f"{atype.model} (receiving)", asset_tag='', asset_type=atype,
                            asset_role=atype.asset_role, status=self._status_labels['available'],
                            location=locs[0], tenant=tenant, purchase_order_line=line,
                            serial_number=f"{meta['code']}{random.randint(100000, 999999)}",
                            purchase_cost=cost, salvage_value=round(cost * 0.1, 2),
                            purchase_date=order_date, in_service_date=order_date,
                            order_number=po.order_number,
                            supplier=po.supplier, notes='Received against purchase order; awaiting deployment.')
                        a.save()  # asset_tag drawn from the tenant's AssetTagSequence
                        a.name = f"{atype.model} ({a.asset_tag})"
                        a.save(update_fields=['name'])
                        fulfilled += 1
        self.stdout.write(f'  {po_count} purchase orders, {line_count} order lines, '
                          f'{fulfilled} assets received against PO lines.')

    # ─────────────────────────────────────────────────────────────────
    # Operations: alerts, reports, event rules, config, dashboards, audit
    # ─────────────────────────────────────────────────────────────────

    def _seed_operations(self):
        from extras.models import NotificationChannel, AlertRule, AlertLog
        from extras.models import EventRule, WebhookEndpoint, LabelTemplate, JournalEntry, ReportTemplate, ScheduledReport
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

    # ─────────────────────────────────────────────────────────────────
    # Change history (ObjectChange)
    # ─────────────────────────────────────────────────────────────────

    def _seed_changelog(self):
        """Backfill a believable audit trail.

        Seeding runs in a management command with no request context, so
        ChangeLoggingMixin records nothing on its own. The changelog is a product
        strength, so we synthesise ObjectChange rows directly — provisioning,
        re-assignment, status changes and audits — attributed to the right MSP
        engineers and back-dated so history reads as naturally grown rather than
        all-at-once.
        """
        import uuid
        from core.models import ObjectChange
        self.stdout.write('--- Change history ---')

        actors = self._engineer_users or [self._provisioner]
        helpdesk = [u for name, u in self._users.items()
                    if name in ('ravi.anand', 'mia.koch')] or actors
        rows = []  # ObjectChange instances whose .time we backdate via bulk_update

        def aware_days_ago(n):
            return timezone.now() - datetime.timedelta(days=max(0, n), hours=random.randint(0, 18))

        def add(obj, action, post, pre=None, actor=None, when=None):
            actor = actor or random.choice(actors)
            ct = ContentType.objects.get_for_model(type(obj))
            oc = ObjectChange.objects.create(
                user=actor, user_name=actor.get_username(), request_id=uuid.uuid4(),
                action=action, changed_object_type=ct, changed_object_id=obj.pk,
                object_repr=str(obj)[:200], object_type_repr=f"{ct.app_label} | {ct.model}",
                prechange_data=pre, postchange_data=post)
            oc.time = when or timezone.now()
            rows.append(oc)

        # Asset lifecycle: provisioning at purchase, then later edits / checkouts / audits.
        for slug in self._tenants:
            assets = self._assets_by_tenant.get(slug, [])
            for asset in random.sample(assets, k=min(18, len(assets))):
                born = (TODAY - asset.purchase_date).days if asset.purchase_date else 120
                add(asset, 'create',
                    {'asset_tag': asset.asset_tag, 'status': asset.status.name if asset.status else None,
                     'tenant': asset.tenant.name if asset.tenant else None,
                     'purchase_cost': str(asset.purchase_cost)},
                    when=aware_days_ago(born))
                active = asset.assignments.filter(is_active=True, assigned_user__isnull=False).first()
                if active and random.random() < 0.7:
                    add(asset, 'checkout',
                        {'assigned_user': str(active.assigned_user), 'status': 'In Use'},
                        pre={'status': 'Available'},
                        when=aware_days_ago(max(1, born - random.randint(2, 20))))
                if random.random() < 0.35:
                    new_note = random.choice([
                        'Re-imaged and re-enrolled in MDM.', 'BIOS/firmware updated to latest.',
                        'Warranty extended by 12 months.', 'Relocated during office move.'])
                    add(asset, 'update', {'notes': new_note}, pre={'notes': ''},
                        actor=random.choice(helpdesk),
                        when=aware_days_ago(random.randint(5, max(6, born // 2))))
                if random.random() < 0.25:
                    add(asset, 'audit',
                        {'last_audited': str(TODAY), 'status': asset.status.name if asset.status else None},
                        when=aware_days_ago(random.randint(2, 90)))

        # Retired assets: a clear decommission update.
        for asset in self._retired_assets:
            add(asset, 'update', {'status': 'Retired', 'disposed_at': str(asset.disposed_at)},
                pre={'status': 'In Use'}, when=aware_days_ago(random.randint(10, 120)))

        # License & subscription edits (seat-count bumps, renewals).
        for lic in random.sample(self._licenses, k=min(20, len(self._licenses))):
            add(lic, 'create', {'name': lic.name, 'seats': lic.seats}, when=aware_days_ago(random.randint(60, 600)))
            if random.random() < 0.4:
                add(lic, 'update', {'seats': lic.seats}, pre={'seats': max(1, lic.seats - 5)},
                    when=aware_days_ago(random.randint(10, 120)))
        for sub in random.sample(self._subscriptions, k=min(12, len(self._subscriptions))):
            add(sub, 'update', {'renewal_date': str(sub.renewal_date), 'renewal_cost': str(sub.renewal_cost)},
                pre={'renewal_cost': str(round(float(sub.renewal_cost) * 0.9, 2))},
                when=aware_days_ago(random.randint(5, 90)))

        # Back-date the auto_now_add timestamps in one pass.
        ObjectChange.objects.bulk_update(rows, ['time'])
        self.stdout.write(f'  {len(rows)} change-history entries across assets, licenses and subscriptions.')
