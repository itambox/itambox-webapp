"""
Management command to clear all data and seed the database with comprehensive sample data.

Usage:
    python manage.py seed_data                  # Seed with defaults (no --no-input needed for drop)
    python manage.py seed_data --skip-drop      # Add data without clearing existing
    python manage.py seed_data --production     # Only create minimal essential data (admin, status labels)
"""

import datetime
import hashlib
import random

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

User = get_user_model()


class Command(BaseCommand):
    help = "Clear all data and reseed the database with comprehensive sample data for testing."

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-drop',
            action='store_true',
            default=False,
            help='Add data without clearing existing records.',
        )
        parser.add_argument(
            '--production',
            action='store_true',
            default=False,
            help='Only create minimal essential data (admin user, default status labels).',
        )

    def handle(self, *args, **options):
        skip_drop = options['skip_drop']
        production = options['production']

        if not skip_drop:
            self._clear_all_data()

        if production:
            self._seed_minimal()
        else:
            self._seed_all()

        self.stdout.write(self.style.SUCCESS('\nDatabase seeding complete.'))

    # ─────────────────────────────────────────────────────────────────
    # Clear
    # ─────────────────────────────────────────────────────────────────

    def _clear_all_data(self):
        """Delete all records in dependency order to avoid FK violations."""
        self.stdout.write('Clearing all existing data...')

        # Delete in reverse-dependency order
        models_to_clear = [
            # Leaf models first
            ('components', 'ComponentAllocation'),
            ('components', 'ComponentStock'),
            ('assets', 'ActivityLog'),
            ('compliance', 'CustodyReceipt'),
            ('core', 'ObjectChange'),
            ('core', 'Notification'),
            ('users', 'UserPreference'),
            ('organization', 'ContactAssignment'),
            ('organization', 'AssetHolderAssignment'),
            ('licenses', 'LicenseSeatAssignment'),
            ('subscriptions', 'SubscriptionAssignment'),
            ('inventory', 'AccessoryAssignment'),
            ('inventory', 'ConsumableAssignment'),
            ('assets', 'InstalledSoftware'),
            ('compliance', 'AssetMaintenance'),
            ('inventory', 'KitItem'),
            ('inventory', 'Kit'),
            ('assets', 'Asset'),
            ('assets', 'AssetType'),
            ('components', 'Component'),
            ('inventory', 'Accessory'),
            ('inventory', 'Consumable'),
            ('licenses', 'License'),
            ('software', 'Software'),
            ('subscriptions', 'Subscription'),
            ('subscriptions', 'Provider'),
            ('organization', 'Location'),
            ('organization', 'Site'),
            ('organization', 'AssetHolder'),
            ('organization', 'Contact'),
            ('organization', 'ContactRole'),
            ('organization', 'Tenant'),
            ('organization', 'TenantGroup'),
            ('organization', 'Region'),
            ('organization', 'SiteGroup'),
            ('assets', 'AssetRole'),
            ('assets', 'StatusLabel'),
            ('assets', 'Manufacturer'),
            ('assets', 'Category'),
            ('assets', 'CustomFieldset'),
            ('assets', 'CustomField'),
            ('assets', 'Depreciation'),
            ('extras', 'Tag'),
        ]
        for app_label, model_name in models_to_clear:
            try:
                from django.apps import apps
                model = apps.get_model(app_label, model_name)
                count, _ = model.objects.all().delete()
                if count:
                    self.stdout.write(f'  Deleted {count} {model_name}(s)')
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'  Could not clear {model_name}: {e}'))

        # Keep superuser accounts, delete regular users
        User.objects.filter(is_superuser=False).delete()
        # Clear ContentType cache after bulk deletions
        ContentType.objects.clear_cache()
        self.stdout.write('  Kept superuser accounts, deleted regular users.')

    # ─────────────────────────────────────────────────────────────────
    # Minimal Seed (--production)
    # ─────────────────────────────────────────────────────────────────

    def _seed_minimal(self):
        """Only essential records: admin user and default status labels."""
        from assets.models import StatusLabel

        # Ensure admin user exists
        if not User.objects.filter(is_superuser=True).exists():
            User.objects.create_superuser(
                username='admin',
                email='admin@assetbox.local',
                password='admin123'
            )
            self.stdout.write('  Created admin user (admin / admin123)')

        # Default status labels
        defaults = [
            ('Available', 'available', 'deployable', '28a745'),
            ('In Use', 'in-use', 'deployable', '007bff'),
            ('Pending Repair', 'pending-repair', 'pending', 'ffc107'),
            ('Retired', 'retired', 'archived', 'dc3545'),
            ('In Transit', 'in-transit', 'pending', '6f42c1'),
            ('Decommissioned', 'decommissioned', 'undeployable', '6c757d'),
        ]
        for name, slug, stype, color in defaults:
            StatusLabel.objects.get_or_create(
                slug=slug,
                defaults={'name': name, 'type': stype, 'color': color}
            )
        self.stdout.write(f'  Seeded {len(defaults)} StatusLabels.')

    # ─────────────────────────────────────────────────────────────────
    # Full Seed
    # ─────────────────────────────────────────────────────────────────

    def _seed_all(self):
        self.stdout.write('\nSeeding comprehensive sample data...\n')

        with transaction.atomic():
            # Phase 0: Foundation
            self._seed_phase0()

            # Phase 1: Organization Hierarchy
            self._seed_phase1()

            # Phase 2: Asset Infrastructure
            self._seed_phase2()

            # Phase 3: Hardware Assets
            self._seed_phase3()

            # Phase 4: Software & Licenses
            self._seed_phase4()

            # Phase 5: Subscriptions
            self._seed_phase5()

            # Phase 6: Kits, Maintenance, Activity
            self._seed_phase6()

    # ─────────────────────────────────────────
    # Phase 0: Users, Tags, StatusLabels
    # ─────────────────────────────────────────

    def _seed_phase0(self):
        from assets.models import StatusLabel, AssetRole, Manufacturer, Depreciation, Supplier
        from extras.models import Tag, CustomField, CustomFieldset
        from users.models import UserPreference

        self.stdout.write('--- Phase 0: Foundation ---')

        # Users
        users_data = [
            ('rene.rettig', 'rene.rettig@helheim.cloud', True),
            ('sarah.chen', 'sarah.chen@helheim.cloud', False),
            ('marcus.johnson', 'marcus.johnson@helheim.cloud', False),
            ('elena.rodriguez', 'elena.rodriguez@helheim.cloud', False),
            ('thomas.weber', 'thomas.weber@helheim.cloud', False),
            ('oliver.smith', 'oliver.smith@helheim.cloud', False),
            ('sophia.martinez', 'sophia.martinez@helheim.cloud', False),
            ('lucas.muller', 'lucas.muller@helheim.cloud', False),
            ('emma.dupont', 'emma.dupont@helheim.cloud', False),
            ('liam.o-connor', 'liam.oconnor@helheim.cloud', False),
            ('chloe.lefevre', 'chloe.lefevre@helheim.cloud', False),
            ('noah.schmidt', 'noah.schmidt@helheim.cloud', False),
            ('mia.petrov', 'mia.petrov@helheim.cloud', False),
            ('alexander.gruber', 'alexander.gruber@helheim.cloud', False),
            ('yasmine.al-sayed', 'yasmine.alsayed@helheim.cloud', False),
            ('david.kim', 'david.kim@helheim.cloud', False),
            ('wei.zhang', 'wei.zhang@helheim.cloud', False),
        ]
        self._users = []
        for username, email, is_superuser in users_data:
            user, created = User.objects.get_or_create(username=username, defaults={
                'email': email, 'is_superuser': is_superuser, 'is_staff': is_superuser
            })
            if created:
                user.set_password('assetbox2026')
                user.save()
                UserPreference.objects.create(user=user, data={
                    'color_theme': random.choice(['light', 'dark']),
                    'pagination': {'per_page': random.choice([25, 50, 100])}
                })
            self._users.append(user)

        # Default status labels (idempotent)
        defaults = [
            ('Available', 'available', 'deployable', '28a745'),
            ('In Use', 'in-use', 'deployable', '007bff'),
            ('Pending Repair', 'pending-repair', 'pending', 'ffc107'),
            ('Retired', 'retired', 'archived', 'dc3545'),
            ('In Transit', 'in-transit', 'pending', '6f42c1'),
            ('Decommissioned', 'decommissioned', 'undeployable', '6c757d'),
            ('Quarantined', 'quarantined', 'pending', 'fd7e14'),
        ]
        self._status_labels = {}
        for name, slug, stype, color in defaults:
            obj, _ = StatusLabel.objects.get_or_create(
                slug=slug, defaults={'name': name, 'type': stype, 'color': color}
            )
            self._status_labels[slug] = obj

        # Tags
        tag_data = [
            ('Production', 'production', '28a745'),
            ('Development', 'development', '007bff'),
            ('Staging', 'staging', 'ffc107'),
            ('VIP', 'vip', 'dc3545'),
            ('Windows', 'windows', '17a2b8'),
            ('macOS', 'macos', '6f42c1'),
            ('Linux', 'linux', 'fd7e14'),
            ('Cloud', 'cloud', '20c997'),
            ('On-Prem', 'on-prem', 'adb5bd'),
            ('Finance', 'finance', '198754'),
            ('Engineering', 'engineering', '0d6efd'),
            ('HR', 'hr', 'd63384'),
            ('Marketing', 'marketing', 'ff6b6b'),
            ('Sales', 'sales', 'ffd43b'),
            ('Executive', 'executive', 'e83e8c'),
            ('Security', 'security', '0b5ed7'),
            ('Audited', 'audited', '20c997'),
            ('Under Review', 'under-review', 'ffc107'),
            ('Critical', 'critical', 'dc3545'),
            ('Legacy', 'legacy', '6c757d'),
        ]
        self._tags = {}
        for name, slug, color in tag_data:
            obj, _ = Tag.objects.get_or_create(slug=slug, defaults={'name': name, 'color': color})
            self._tags[slug] = obj

        # AssetRoles
        role_data = [
            ('Laptop', 'laptop', '007bff', 'Portable computing device for end users'),
            ('Desktop', 'desktop', '28a745', 'Fixed workstation for office use'),
            ('Server', 'server', 'dc3545', 'Datacenter or rack-mounted server'),
            ('Monitor', 'monitor', '6f42c1', 'Display device'),
            ('Mobile Phone', 'mobile-phone', 'fd7e14', 'Smartphone or feature phone'),
            ('Tablet', 'tablet', '20c997', 'Tablet device'),
            ('Printer', 'printer', 'adb5bd', 'Network or local printer'),
            ('Network Device', 'network-device', '0d6efd', 'Switch, router, firewall, or access point'),
            ('Peripheral', 'peripheral', 'ff6b6b', 'Keyboard, mouse, webcam, headset'),
            ('Storage Device', 'storage-device', 'e83e8c', 'NAS, external drive, or SAN component'),
        ]
        self._asset_roles = {}
        for name, slug, color, desc in role_data:
            obj, _ = AssetRole.objects.get_or_create(
                slug=slug, defaults={'name': name, 'color': color, 'description': desc}
            )
            self._asset_roles[slug] = obj

        # Manufacturers
        mfr_data = [
            ('Dell Technologies', 'dell-technologies', 'American multinational computer technology company'),
            ('Apple Inc.', 'apple-inc', 'Consumer electronics and software company'),
            ('HP Inc.', 'hp-inc', 'Information technology company'),
            ('Lenovo Group', 'lenovo-group', 'Chinese multinational technology company'),
            ('Cisco Systems', 'cisco-systems', 'Networking hardware and telecommunications'),
            ('Samsung Electronics', 'samsung-electronics', 'South Korean multinational electronics corporation'),
            ('Microsoft Corporation', 'microsoft-corporation', 'Software, consumer electronics, and personal computers'),
            ('Logitech International', 'logitech-international', 'Computer peripherals and software'),
            ('Brother Industries', 'brother-industries', 'Printers and multifunction devices'),
            ('Synology Inc.', 'synology-inc', 'Network-attached storage appliances'),
            ('Ubiquiti Inc.', 'ubiquiti-inc', 'Networking technology company'),
        ]
        self._manufacturers = {}
        for name, slug, desc in mfr_data:
            obj, _ = Manufacturer.objects.get_or_create(
                slug=slug, defaults={'name': name, 'description': desc}
            )
            self._manufacturers[slug] = obj

        # CustomFields
        cf_data = [
            ('sim_number', 'SIM Number', 'text', False, None),
            ('imei', 'IMEI', 'text', False, None),
            ('screen_size', 'Screen Size (inches)', 'number', False, None),
            ('vehicle_vin', 'VIN Number', 'text', False, None),
            ('hostname', 'Hostname', 'text', True, None),
            ('os_version', 'OS Version', 'text', False, None),
            ('department', 'Department', 'select', False, 'Engineering\nFinance\nHR\nMarketing\nSales\nOperations'),
            ('floor', 'Floor', 'number', False, None),
            ('asset_lifecycle', 'Lifecycle Stage', 'select', False, 'Procurement\nDeployment\nActive\nMaintenance\nRetirement'),
            ('encrypted', 'Disk Encrypted', 'boolean', False, None),
            ('cpu_architecture', 'CPU Architecture', 'select', False, 'x86_64\nARM64'),
            ('ram_slot_count', 'RAM Slots Count', 'number', False, None),
            ('ip_address', 'IP Address', 'text', False, None),
            ('port_count', 'Port Count', 'number', False, None),
            ('poe_budget_w', 'PoE Budget (Watts)', 'number', False, None),
            ('firmware_version', 'Firmware Version', 'text', False, None),
            ('sim_carrier', 'SIM Carrier', 'select', False, 'Deutsche Telekom\nVodafone\nO2\nSwisscom\nOrange'),
            ('apn_profile', 'APN Profile', 'text', False, None),
            ('puk_code', 'PUK Code', 'text', False, None),
            ('screen_resolution', 'Screen Resolution', 'text', False, None),
            ('input_ports', 'Input Ports', 'text', False, None),
            ('mounted_state', 'Mounted State', 'select', False, 'Wall-Mounted\nCeiling-Mounted\nTable-Top\nMobile-Stand'),
        ]
        self._custom_fields = {}
        for name, label, ftype, required, choices in cf_data:
            obj, _ = CustomField.objects.get_or_create(
                name=name,
                defaults={'label': label, 'field_type': ftype, 'required': required, 'choices': choices}
            )
            self._custom_fields[name] = obj

        # CustomFieldsets
        self._laptop_fieldset = CustomFieldset.objects.create(name='Laptop Specs')
        self._laptop_fieldset.fields.add(
            self._custom_fields['hostname'],
            self._custom_fields['os_version'],
            self._custom_fields['encrypted'],
            self._custom_fields['department'],
            self._custom_fields['cpu_architecture'],
            self._custom_fields['ram_slot_count'],
        )

        self._mobile_fieldset = CustomFieldset.objects.create(name='Mobile Device Specs')
        self._mobile_fieldset.fields.add(
            self._custom_fields['sim_number'],
            self._custom_fields['imei'],
            self._custom_fields['screen_size'],
            self._custom_fields['os_version'],
        )

        self._server_fieldset = CustomFieldset.objects.create(name='Server Specs')
        self._server_fieldset.fields.add(
            self._custom_fields['hostname'],
            self._custom_fields['os_version'],
            self._custom_fields['department'],
            self._custom_fields['floor'],
        )

        self._switch_fieldset = CustomFieldset.objects.create(name='Network Switch Specs')
        self._switch_fieldset.fields.add(
            self._custom_fields['hostname'],
            self._custom_fields['ip_address'],
            self._custom_fields['port_count'],
            self._custom_fields['poe_budget_w'],
            self._custom_fields['firmware_version'],
        )

        self._sim_fieldset = CustomFieldset.objects.create(name='Mobile SIM Specs')
        self._sim_fieldset.fields.add(
            self._custom_fields['sim_number'],
            self._custom_fields['sim_carrier'],
            self._custom_fields['apn_profile'],
            self._custom_fields['puk_code'],
        )

        self._av_fieldset = CustomFieldset.objects.create(name='AV & Conference Specs')
        self._av_fieldset.fields.add(
            self._custom_fields['screen_resolution'],
            self._custom_fields['input_ports'],
            self._custom_fields['mounted_state'],
        )

        # Depreciation schedules
        dep_data = [
            ('3-Year Straight-Line', 36),
            ('4-Year Straight-Line', 48),
            ('5-Year Straight-Line', 60),
            ('7-Year Straight-Line', 84),
            ('10-Year Straight-Line', 120),
        ]
        self._depreciations = {}
        for name, months in dep_data:
            obj, _ = Depreciation.objects.get_or_create(name=name, defaults={'months': months})
            self._depreciations[name] = obj

        # Suppliers
        supplier_data = [
            ('ITZ Solutions GmbH', 'itz-solutions', 'info@itz-solutions.de', '+49-30-555-0100', 'https://itz-solutions.de'),
            ('Dell Direct', 'dell-direct', 'enterprise@dell.com', '+1-800-555-0199', 'https://dell.com'),
            ('Apple Business', 'apple-business', 'business@apple.com', '+1-800-555-0200', 'https://apple.com/business'),
            ('HP Enterprise Store', 'hp-enterprise-store', 'hpe-sales@hp.com', '+1-800-555-0300', 'https://hpe.com'),
            ('Lenovo Pro', 'lenovo-pro', 'pro-sales@lenovo.com', '+1-800-555-0400', 'https://lenovo.com/pro'),
            ('CDW Deutschland', 'cdw-deutschland', 'de.sales@cdw.com', '+49-211-555-0500', 'https://cdw.de'),
            ('Amazon Business', 'amazon-business', 'business@amazon.de', '+49-800-555-0600', 'https://business.amazon.de'),
            ('Notebooksbilliger AG', 'notebooksbilliger-ag', 'b2b@notebooksbilliger.de', '+49-5921-555-0700', 'https://notebooksbilliger.de/b2b'),
        ]
        self._suppliers = {}
        for name, slug, email, phone, website in supplier_data:
            obj, _ = Supplier.objects.get_or_create(
                slug=slug,
                defaults={'name': name, 'contact_email': email, 'contact_phone': phone, 'website': website}
            )
            self._suppliers[slug] = obj

        self.stdout.write(f'  {len(self._users)} users, {len(self._tags)} tags, '
                          f'{len(self._asset_roles)} asset roles, {len(self._manufacturers)} manufacturers, '
                          f'{len(self._custom_fields)} custom fields, {len(self._depreciations)} depreciation schedules.')

    # ─────────────────────────────────────────
    # Phase 1: Organization Hierarchy
    # ─────────────────────────────────────────

    def _seed_phase1(self):
        from organization.models import (
            Region, SiteGroup, TenantGroup, Tenant, Site, Location,
            AssetHolder, ContactRole, Contact, ContactAssignment,
        )

        self.stdout.write('--- Phase 1: Organization ---')

        # Regions
        regions = {}
        for name, slug in [('North America', 'north-america'), ('Europe', 'europe'), ('Asia-Pacific', 'asia-pacific')]:
            obj, _ = Region.objects.get_or_create(slug=slug, defaults={'name': name})
            regions[slug] = obj

        # Sub-regions
        sub_regions = {}
        for name, slug, parent_slug in [
            ('US East', 'us-east', 'north-america'),
            ('US West', 'us-west', 'north-america'),
            ('Canada', 'canada', 'north-america'),
            ('Western Europe', 'western-europe', 'europe'),
            ('Northern Europe', 'northern-europe', 'europe'),
            ('Southeast Asia', 'southeast-asia', 'asia-pacific'),
            ('Australia', 'australia', 'asia-pacific'),
        ]:
            obj, _ = Region.objects.get_or_create(
                slug=slug,
                defaults={'name': name, 'parent': regions[parent_slug]}
            )
            sub_regions[slug] = obj

        # SiteGroups
        site_groups = {}
        for name, slug in [
            ('Corporate Offices', 'corporate-offices'),
            ('Datacenters', 'datacenters'),
            ('Remote Sites', 'remote-sites'),
        ]:
            obj, _ = SiteGroup.objects.get_or_create(slug=slug, defaults={'name': name})
            site_groups[slug] = obj

        # TenantGroups & Tenants
        tg_helheim, _ = TenantGroup.objects.get_or_create(
            slug='helheim-group', defaults={'name': 'Helheim Group'}
        )
        tg_customers, _ = TenantGroup.objects.get_or_create(
            slug='customer-tenants', defaults={'name': 'Customer Tenants'}
        )

        tenants = {}
        tenant_data = [
            # Internal (Helheim Group)
            ('Helheim Cloud GmbH', 'helheim-cloud-gmbh', tg_helheim),
            ('Helheim Security AG', 'helheim-security-ag', tg_helheim),
            ('Helheim Labs Inc.', 'helheim-labs-inc', tg_helheim),
            ('Helheim Aviation SE', 'helheim-aviation-se', tg_helheim),
            # Customer tenants
            ('Finova Capital AG', 'finova-capital-ag', tg_customers),
            ('Finova Asset Management Ltd.', 'finova-asset-management-ltd', tg_customers),
            ('MediCare Health GmbH', 'medicare-health-gmbh', tg_customers),
            ('MediCare Research Lab', 'medicare-research-lab', tg_customers),
            ('TechStartup.io GmbH', 'techstartup-io-gmbh', tg_customers),
            ('GreenEnergy Solutions SE', 'greenenergy-solutions-se', tg_customers),
            ('Apex Logistics Solutions', 'apex-logistics-solutions', tg_customers),
            ('Quantum Robotics Corp', 'quantum-robotics-corp', tg_customers),
        ]
        for name, slug, tgroup in tenant_data:
            obj, _ = Tenant.objects.get_or_create(slug=slug, defaults={'name': name, 'group': tgroup})
            tenants[slug] = obj
        self._tenants = tenants

        # Sites
        sites = {}
        site_data = [
            ('Berlin HQ', 'berlin-hq', site_groups['corporate-offices'], tenants['helheim-cloud-gmbh'],
             sub_regions['western-europe'], 'Central Office', '52.5200', '13.4050',
             'Mauerstrasse 42\n10117 Berlin\nGermany'),
            ('Munich Office', 'munich-office', site_groups['corporate-offices'], tenants['helheim-security-ag'],
             sub_regions['western-europe'], 'Satellite Office', '48.1351', '11.5820',
             'Leopoldstrasse 88\n80802 Munich\nGermany'),
            ('Amsterdam DC', 'amsterdam-dc', site_groups['datacenters'], tenants['helheim-cloud-gmbh'],
             sub_regions['western-europe'], 'Primary Datacenter', '52.3105', '4.7683',
             'Science Park 400\n1098 XH Amsterdam\nNetherlands'),
            ('New York Office', 'new-york-office', site_groups['corporate-offices'], tenants['helheim-labs-inc'],
             sub_regions['us-east'], 'US Headquarters', '40.7128', '-74.0060',
             '350 Fifth Avenue\nNew York, NY 10118\nUSA'),
            ('San Francisco Office', 'san-francisco-office', site_groups['corporate-offices'], tenants['helheim-labs-inc'],
             sub_regions['us-west'], 'West Coast Office', '37.7749', '-122.4194',
             '1 Market Street\nSan Francisco, CA 94105\nUSA'),
            ('Singapore Office', 'singapore-office', site_groups['remote-sites'], tenants['helheim-cloud-gmbh'],
             sub_regions['southeast-asia'], 'APAC Hub', '1.3521', '103.8198',
             '8 Marina Boulevard\nSingapore 018981'),
            # Customer Sites
            ('Finova Frankfurt HQ', 'finova-frankfurt-hq', site_groups['corporate-offices'], tenants['finova-capital-ag'],
             sub_regions['western-europe'], 'Customer HQ', '50.1109', '8.6821',
             'Taunusanlage 12\n60325 Frankfurt\nGermany'),
            ('Finova Frankfurt Office 2', 'frankfurt-hub', site_groups['corporate-offices'], tenants['finova-asset-management-ltd'],
             sub_regions['western-europe'], 'Asset Management Center', '50.1120', '8.6800',
             'Kaiserstrasse 15\n60311 Frankfurt\nGermany'),
            ('MediCare Munich Office', 'medicare-munich-office', site_groups['corporate-offices'], tenants['medicare-health-gmbh'],
             sub_regions['western-europe'], 'Customer Office', '48.1374', '11.5755',
             'Rosenheimer Strasse 145\n81671 Munich\nGermany'),
            ('MediCare Research Lab Munich', 'munich-lab', site_groups['datacenters'], tenants['medicare-research-lab'],
             sub_regions['western-europe'], 'Research Laboratory', '48.1400', '11.5800',
             'Rosenheimer Strasse 150\n81671 Munich\nGermany'),
            ('TechStartup Berlin Hub', 'techstartup-berlin-hub', site_groups['remote-sites'], tenants['techstartup-io-gmbh'],
             sub_regions['western-europe'], 'Co-Working Hub', '52.5067', '13.3911',
             'Axel-Springer-Strasse 54\n10117 Berlin\nGermany'),
            ('GreenEnergy Hamburg', 'greenenergy-hamburg', site_groups['corporate-offices'], tenants['greenenergy-solutions-se'],
             sub_regions['northern-europe'], 'Customer HQ', '53.5511', '9.9937',
             'Am Kaiserkai 1\n20457 Hamburg\nGermany'),
            ('Apex Frankfurt Depot', 'apex-frankfurt-depot', site_groups['remote-sites'], tenants['apex-logistics-solutions'],
             sub_regions['western-europe'], 'Warehouse Depot', '50.0494', '8.5707',
             'CargoCity Sued\n60549 Frankfurt Airport\nGermany'),
            ('Boston Robotics Center', 'boston-robotics-center', site_groups['corporate-offices'], tenants['quantum-robotics-corp'],
             sub_regions['us-east'], 'R&D Center', '42.3601', '-71.0589',
             '250 Summer Street\nBoston, MA 02210\nUSA'),
        ]
        for name, slug, group, tenant, region, facility, lat, lon, addr in site_data:
            obj, _ = Site.objects.get_or_create(
                slug=slug,
                defaults={
                    'name': name, 'group': group, 'tenant': tenant, 'region': region,
                    'facility': facility, 'latitude': lat, 'longitude': lon,
                    'physical_address': addr, 'time_zone': 'UTC',
                }
            )
            sites[slug] = obj

        # Locations (rooms/floors within sites)
        locations = {}
        location_data = [
            ('Floor 1 - Engineering', 'berlin-floor-1-eng', sites['berlin-hq'], tenants['helheim-cloud-gmbh']),
            ('Floor 2 - Finance & HR', 'berlin-floor-2-admin', sites['berlin-hq'], tenants['helheim-cloud-gmbh']),
            ('Floor 3 - Executive', 'berlin-floor-3-exec', sites['berlin-hq'], tenants['helheim-cloud-gmbh']),
            ('Server Room A', 'berlin-server-room-a', sites['berlin-hq'], tenants['helheim-cloud-gmbh']),
            ('Berlin Staging Row 1', 'berlin-staging-row-1', sites['berlin-hq'], tenants['helheim-cloud-gmbh']),
            ('Rack Row 1 - Compute', 'ams-rack-row-1', sites['amsterdam-dc'], tenants['helheim-cloud-gmbh']),
            ('Rack Row 2 - Storage', 'ams-rack-row-2', sites['amsterdam-dc'], tenants['helheim-cloud-gmbh']),
            ('Rack Row 3 - Network', 'ams-rack-row-3', sites['amsterdam-dc'], tenants['helheim-cloud-gmbh']),
            ('Floor 5 - Engineering', 'ny-floor-5-eng', sites['new-york-office'], tenants['helheim-labs-inc']),
            ('Floor 6 - Sales', 'ny-floor-6-sales', sites['new-york-office'], tenants['helheim-labs-inc']),
            ('Office 12A', 'munich-office-12a', sites['munich-office'], tenants['helheim-security-ag']),
            ('Munich Staging Area', 'munich-staging-area', sites['munich-office'], tenants['helheim-security-ag']),
            # Customer Locations
            ('Floor 2 - Trading Desk', 'finova-floor-2-trading', sites['finova-frankfurt-hq'], tenants['finova-capital-ag']),
            ('Floor 3 - Risk Management', 'finova-floor-3-risk', sites['finova-frankfurt-hq'], tenants['finova-capital-ag']),
            ('Frankfurt Hub Floor 1', 'frankfurt-hub-floor-1', sites['frankfurt-hub'], tenants['finova-asset-management-ltd']),
            ('Ward Administration', 'medicare-ward-admin', sites['medicare-munich-office'], tenants['medicare-health-gmbh']),
            ('Lab Research Suite', 'medicare-lab-research', sites['medicare-munich-office'], tenants['medicare-health-gmbh']),
            ('Lab A Testing Bench', 'lab-a-testing-bench', sites['munich-lab'], tenants['medicare-research-lab']),
            ('Open Space Area', 'techstartup-open-space', sites['techstartup-berlin-hub'], tenants['techstartup-io-gmbh']),
            ('Engineering Lab', 'techstartup-eng-lab', sites['techstartup-berlin-hub'], tenants['techstartup-io-gmbh']),
            ('Wind Analytics Office', 'greenenergy-wind-analytics', sites['greenenergy-hamburg'], tenants['greenenergy-solutions-se']),
            ('Solar Operations Center', 'greenenergy-solar-ops', sites['greenenergy-hamburg'], tenants['greenenergy-solutions-se']),
            ('Frankfurt Depot Shelf B', 'frankfurt-depot-shelf-b', sites['apex-frankfurt-depot'], tenants['apex-logistics-solutions']),
            ('Boston Assembly Floor', 'boston-assembly-floor', sites['boston-robotics-center'], tenants['quantum-robotics-corp']),
        ]
        for name, slug, site, tenant in location_data:
            obj, _ = Location.objects.get_or_create(
                slug=slug,
                defaults={'name': name, 'site': site, 'tenant': tenant}
            )
            locations[slug] = obj
        self._locations = locations

        # AssetHolders (employees)
        holder_data = [
            ('Rene', 'Rettig', 'rene.rettig', 'rene.rettig@helheim.cloud', tenants['helheim-cloud-gmbh']),
            ('Sarah', 'Chen', 'sarah.chen', 'sarah.chen@helheim.cloud', tenants['helheim-cloud-gmbh']),
            ('Marcus', 'Johnson', 'marcus.johnson', 'marcus.johnson@helheim.cloud', tenants['helheim-labs-inc']),
            ('Elena', 'Rodriguez', 'elena.rodriguez', 'elena.rodriguez@helheim.cloud', tenants['helheim-security-ag']),
            ('Thomas', 'Weber', 'thomas.weber', 'thomas.weber@helheim.cloud', tenants['helheim-cloud-gmbh']),
            ('Anna', 'Schmidt', 'anna.schmidt', 'anna.schmidt@helheim.cloud', tenants['helheim-cloud-gmbh']),
            ('James', 'Wilson', 'james.wilson', 'james.wilson@helheim.cloud', tenants['helheim-labs-inc']),
            ('Yuki', 'Tanaka', 'yuki.tanaka', 'yuki.tanaka@helheim.cloud', tenants['helheim-cloud-gmbh']),
            ('Omar', 'Hassan', 'omar.hassan', 'omar.hassan@helheim.cloud', tenants['helheim-security-ag']),
            ('Lisa', 'Andersson', 'lisa.andersson', 'lisa.andersson@helheim.cloud', tenants['helheim-cloud-gmbh']),
            ('Carlos', 'Mendez', 'carlos.mendez', 'carlos.mendez@helheim.cloud', tenants['helheim-labs-inc']),
            ('Priya', 'Patel', 'priya.patel', 'priya.patel@helheim.cloud', tenants['helheim-cloud-gmbh']),
            ('Oliver', 'Smith', 'oliver.smith', 'oliver.smith@helheim.cloud', tenants['helheim-aviation-se']),
            ('Sophia', 'Martinez', 'sophia.martinez', 'sophia.martinez@helheim.cloud', tenants['helheim-aviation-se']),
            # Customer holders
            ('Klaus', 'Fischer', 'klaus.fischer', 'klaus.fischer@finova-capital.de', tenants['finova-capital-ag']),
            ('Nina', 'Bergmann', 'nina.bergmann', 'nina.bergmann@finova-capital.de', tenants['finova-capital-ag']),
            ('Lucas', 'Müller', 'lucas.muller', 'lucas.muller@finova-asset.de', tenants['finova-asset-management-ltd']),
            ('Emma', 'Dupont', 'emma.dupont', 'emma.dupont@finova-asset.de', tenants['finova-asset-management-ltd']),
            ('Dr. Markus', 'Wagner', 'markus.wagner', 'dr.wagner@medicare-health.de', tenants['medicare-health-gmbh']),
            ('Sophie', 'Klein', 'sophie.klein', 'sophie.klein@medicare-health.de', tenants['medicare-health-gmbh']),
            ('Liam', 'O\'Connor', 'liam.o-connor', 'liam.oconnor@medicare-lab.de', tenants['medicare-research-lab']),
            ('Chloe', 'Lefevre', 'chloe.lefevre', 'chloe.lefevre@medicare-lab.de', tenants['medicare-research-lab']),
            ('Felix', 'Bauer', 'felix.bauer', 'felix@techstartup.io', tenants['techstartup-io-gmbh']),
            ('Lena', 'Schulz', 'lena.schulz', 'lena@techstartup.io', tenants['techstartup-io-gmbh']),
            ('Jonas', 'Hoffmann', 'jonas.hoffmann', 'jonas.hoffmann@greenenergy-se.de', tenants['greenenergy-solutions-se']),
            ('Katja', 'Vogel', 'katja.vogel', 'katja.vogel@greenenergy-se.de', tenants['greenenergy-solutions-se']),
            ('Noah', 'Schmidt', 'noah.schmidt', 'noah.schmidt@apex-logistics.de', tenants['apex-logistics-solutions']),
            ('Mia', 'Petrov', 'mia.petrov', 'mia.petrov@apex-logistics.de', tenants['apex-logistics-solutions']),
            ('Alexander', 'Gruber', 'alexander.gruber', 'alex.gruber@quantum-robotics.com', tenants['quantum-robotics-corp']),
            ('Yasmine', 'Al-Sayed', 'yasmine.al-sayed', 'yasmine.alsayed@quantum-robotics.com', tenants['quantum-robotics-corp']),
            ('David', 'Kim', 'david.kim', 'david.kim@quantum-robotics.com', tenants['quantum-robotics-corp']),
            ('Wei', 'Zhang', 'wei.zhang', 'wei.zhang@quantum-robotics.com', tenants['quantum-robotics-corp']),
        ]
        self._holders = {}
        for first, last, upn, email, tenant in holder_data:
            obj, _ = AssetHolder.objects.get_or_create(
                upn=upn,
                defaults={
                    'first_name': first, 'last_name': last, 'email': email, 'tenant': tenant
                }
            )
            self._holders[upn] = obj

        # ContactRoles
        role_data = [
            ('Account Manager', 'account-manager'),
            ('Support Contact', 'support-contact'),
            ('Technical Lead', 'technical-lead'),
            ('Billing Contact', 'billing-contact'),
            ('Escalation Point', 'escalation-point'),
        ]
        self._contact_roles = {}
        for name, slug in role_data:
            obj, _ = ContactRole.objects.get_or_create(slug=slug, defaults={'name': name})
            self._contact_roles[slug] = obj

        # Contacts (vendor/manufacturer contacts)
        contact_data = [
            ('John Vendor', 'Dell Account Manager', '+1-512-555-0100', 'john.vendor@dell.com', 'https://dell.com'),
            ('Lisa Support', 'Apple Enterprise Support', '+1-408-555-0200', 'lisa.support@apple.com', 'https://apple.com'),
            ('Mike Tech', 'Cisco TAC Lead', '+1-408-555-0300', 'mike.tech@cisco.com', 'https://cisco.com'),
            ('Sandra Sales', 'HP Renewals', '+1-650-555-0400', 'sandra.sales@hp.com', 'https://hp.com'),
            ('Alex Distributor', 'Lenovo Channel Manager', '+49-30-555-0500', 'alex.dist@lenovo.com', 'https://lenovo.com'),
        ]
        self._contacts = []
        for name, title, phone, email, web in contact_data:
            obj = Contact.objects.create(name=name, title=title, phone=phone, email=email, web_url=web)
            self._contacts.append(obj)

        # ContactAssignments for manufacturers
        mfr_contact_map = [
            ('dell-technologies', 0, 'account-manager'),
            ('apple-inc', 1, 'support-contact'),
            ('cisco-systems', 2, 'technical-lead'),
            ('hp-inc', 3, 'account-manager'),
            ('lenovo-group', 4, 'account-manager'),
        ]
        for mfr_slug, contact_idx, role_slug in mfr_contact_map:
            ct = ContentType.objects.get_for_model(self._manufacturers[mfr_slug])
            ContactAssignment.objects.get_or_create(
                contact=self._contacts[contact_idx],
                role=self._contact_roles[role_slug],
                content_type=ct,
                object_id=self._manufacturers[mfr_slug].pk,
            )

        self.stdout.write(f'  {len(regions)} regions, {len(site_groups)} site groups, '
                          f'{len(tenants)} tenants, {len(sites)} sites, {len(locations)} locations, '
                          f'{len(self._holders)} asset holders, {len(self._contact_roles)} contact roles, '
                          f'{len(self._contacts)} contacts.')

    # ─────────────────────────────────────────
    # Phase 2: Asset Infrastructure (AssetTypes, Components, Accessories, Consumables)
    # ─────────────────────────────────────────

    def _seed_phase2(self):
        from assets.models import AssetType, Category
        from components.models import Component
        from inventory.models import Accessory, Consumable

        self.stdout.write('--- Phase 2: Asset Infrastructure ---')

        # --- Asset Types ---
        at_data = [
            # (model, slug, manufacturer_slug, part_number, cpu, ram_gb, storage_gb, storage_type, gpu, eol_months, custom_fieldset, depreciation)
            ('Latitude 5550', 'dell-latitude-5550', 'dell-technologies', 'LAT5550-2025',
             'Intel Core i7-1365U', 16, 512, 'NVMe', 'Intel Iris Xe', 36, self._laptop_fieldset, self._depreciations['3-Year Straight-Line']),
            ('Precision 5680', 'dell-precision-5680', 'dell-technologies', 'PREC5680-WS',
             'Intel Core i9-13900H', 32, 1024, 'NVMe', 'NVIDIA RTX 2000 Ada', 36, self._laptop_fieldset, self._depreciations['4-Year Straight-Line']),
            ('OptiPlex 7010', 'dell-optiplex-7010', 'dell-technologies', 'OPT7010-SFF',
             'Intel Core i5-13500', 16, 256, 'NVMe', 'Intel UHD 770', 48, None, self._depreciations['4-Year Straight-Line']),
            ('MacBook Pro 16"', 'macbook-pro-16-2024', 'apple-inc', 'MBP16-M4',
             'Apple M4 Pro', 36, 512, 'NVMe', 'Integrated 20-core GPU', 36, self._laptop_fieldset, self._depreciations['3-Year Straight-Line']),
            ('MacBook Air 15"', 'macbook-air-15-2024', 'apple-inc', 'MBA15-M3',
             'Apple M3', 16, 256, 'NVMe', 'Integrated 10-core GPU', 36, self._laptop_fieldset, self._depreciations['3-Year Straight-Line']),
            ('Mac Studio', 'mac-studio-2024', 'apple-inc', 'MSTUDIO-M2U',
             'Apple M2 Ultra', 64, 1024, 'NVMe', 'Integrated 76-core GPU', 48, None, self._depreciations['5-Year Straight-Line']),
            ('EliteBook 860 G11', 'hp-elitebook-860-g11', 'hp-inc', '866S7EA',
             'Intel Core i7-1370P', 32, 1024, 'NVMe', 'Intel Iris Xe', 36, self._laptop_fieldset, self._depreciations['3-Year Straight-Line']),
            ('ThinkPad X1 Carbon Gen 12', 'thinkpad-x1-carbon-g12', 'lenovo-group', '21KC004PGE',
             'Intel Core i7-1365U', 16, 512, 'NVMe', 'Intel Iris Xe', 36, self._laptop_fieldset, self._depreciations['3-Year Straight-Line']),
            ('ThinkCentre M90q Gen 5', 'thinkcentre-m90q-gen5', 'lenovo-group', '12JNS00E00',
             'Intel Core i5-13500T', 16, 256, 'NVMe', 'Intel UHD 770', 48, None, self._depreciations['4-Year Straight-Line']),
            ('PowerEdge R760', 'dell-poweredge-r760', 'dell-technologies', 'R760-XEON',
             '2x Intel Xeon Gold 6430', 256, 8000, 'SSD RAID', None, 60, self._server_fieldset, self._depreciations['5-Year Straight-Line']),
            ('ProLiant DL380 Gen11', 'hpe-proliant-dl380-g11', 'hp-inc', 'P52534-B21',
             '2x Intel Xeon Silver 4416+', 128, 4000, 'SSD RAID', None, 60, self._server_fieldset, self._depreciations['5-Year Straight-Line']),
            ('iPhone 15 Pro', 'iphone-15-pro', 'apple-inc', 'A2847',
             'Apple A17 Pro', 8, 256, 'NVMe', None, 24, self._mobile_fieldset, self._depreciations['3-Year Straight-Line']),
            ('Galaxy S24 Ultra', 'galaxy-s24-ultra', 'samsung-electronics', 'SM-S928B',
             'Snapdragon 8 Gen 3', 12, 256, 'UFS 4.0', None, 24, self._mobile_fieldset, self._depreciations['3-Year Straight-Line']),
            ('iPad Pro 12.9"', 'ipad-pro-129-2024', 'apple-inc', 'A2436',
             'Apple M4', 8, 256, 'NVMe', None, 36, None, self._depreciations['3-Year Straight-Line']),
            ('Surface Pro 10', 'surface-pro-10', 'microsoft-corporation', 'SURFPRO10-I7',
             'Intel Core i7-1365U', 16, 512, 'NVMe', 'Intel Iris Xe', 36, self._laptop_fieldset, self._depreciations['3-Year Straight-Line']),
            ('Catalyst 9300', 'cisco-catalyst-9300', 'cisco-systems', 'C9300-48P',
             None, None, None, None, None, 60, self._switch_fieldset, self._depreciations['7-Year Straight-Line']),
            ('Meraki MR46', 'meraki-mr46', 'cisco-systems', 'MR46-HW',
             None, None, None, None, None, 60, None, self._depreciations['5-Year Straight-Line']),
            ('UniFi Dream Machine Pro', 'unifi-dream-machine-pro', 'ubiquiti-inc', 'UDM-Pro',
             None, None, None, None, None, 48, self._switch_fieldset, self._depreciations['5-Year Straight-Line']),
            ('UniFi Switch Pro 48 PoE', 'unifi-switch-pro-48', 'ubiquiti-inc', 'USW-PRO-48-POE',
             None, None, None, None, None, 48, self._switch_fieldset, self._depreciations['5-Year Straight-Line']),
            ('DiskStation DS1823xs+', 'synology-ds1823xs', 'synology-inc', 'DS1823XS+',
             'AMD Ryzen V1780B', 32, 8000, 'HDD', None, 60, None, self._depreciations['5-Year Straight-Line']),
            ('Dell P2723DE 27" Monitor', 'dell-p2723de-monitor', 'dell-technologies', 'P2723DE',
             '', None, None, '', '', 60, None, self._depreciations['5-Year Straight-Line']),
            ('Dell P2422HE 24" Monitor', 'dell-p2422he-monitor', 'dell-technologies', 'P2422HE',
             '', None, None, '', '', 60, None, self._depreciations['5-Year Straight-Line']),
            ('Logitech MeetUp AV System', 'logitech-meetup', 'logitech-international', '960-001101',
             None, None, None, None, None, 36, self._av_fieldset, self._depreciations['5-Year Straight-Line']),
            ('Precision 7960 Tower', 'dell-precision-7960-tower', 'dell-technologies', 'PREC7960-TWR',
             None, None, None, None, None, 48, self._laptop_fieldset, self._depreciations['4-Year Straight-Line']),
        ]
        self._asset_types = {}
        for data in at_data:
            model_name, slug, mfr_slug = data[0], data[1], data[2]
            obj, _ = AssetType.objects.get_or_create(
                slug=slug,
                defaults={
                    'model': model_name, 'manufacturer': self._manufacturers[mfr_slug],
                    'part_number': data[3] or '', 'eol_months': data[9],
                    'custom_fieldset': data[10], 'depreciation': data[11],
                }
            )
            self._asset_types[slug] = obj

        # --- Categories ---
        cat_names = [
            'charger', 'adaptor', 'mouse', 'keyboard', 'webcam', 'headset', 'cable',
            'display', 'toner', 'ink', 'batteries', 'thermal-paste', 'other',
            'ram-memory', 'ssd-nvme', 'hdd', 'nic', 'gpu', 'cpu', 'dock',
        ]
        self._categories = {}
        for cat_slug in cat_names:
            applies = {'asset': True, 'accessory': True, 'consumable': True, 'component': True}
            obj, _ = Category.objects.get_or_create(
                slug=cat_slug,
                defaults={'name': cat_slug.replace('-', ' ').title(), 'applies_to': applies}
            )
            self._categories[cat_slug] = obj

        # --- Components ---
        comp_data = [
            ('Samsung 32GB DDR5-4800', 'samsung-32gb-ddr5', 'samsung-electronics', 'ram-memory', 'M324R4GA3BB0', {'capacity_gb': 32, 'type': 'DDR5', 'speed_mhz': 4800}),
            ('Crucial 16GB DDR4-3200', 'crucial-16gb-ddr4', 'dell-technologies', 'ram-memory', 'CT16G4SFD832A', {'capacity_gb': 16, 'type': 'DDR4', 'speed_mhz': 3200}),
            ('Corsair Vengeance 64GB DDR5-5600', 'corsair-64gb-ddr5', 'logitech-international', 'ram-memory', 'CMK64GX5M2B5600C40', {'capacity_gb': 64, 'type': 'DDR5', 'speed_mhz': 5600}),
            ('Kingston ValueRAM 8GB DDR4-2666', 'kingston-8gb-ddr4', 'samsung-electronics', 'ram-memory', 'KVR26N19S8/8', {'capacity_gb': 8, 'type': 'DDR4', 'speed_mhz': 2666}),
            ('Samsung 1TB 990 Pro NVMe', 'samsung-1tb-nvme', 'samsung-electronics', 'ssd-nvme', 'MZ-V9P1T0B', {'capacity_gb': 1000, 'type': 'NVMe', 'interface': 'PCIe 4.0'}),
            ('Samsung 2TB 990 Pro NVMe', 'samsung-2tb-nvme', 'samsung-electronics', 'ssd-nvme', 'MZ-V9P2T0B', {'capacity_gb': 2000, 'type': 'NVMe', 'interface': 'PCIe 4.0'}),
            ('Kingston NV2 500GB NVMe', 'kingston-500gb-nvme', 'samsung-electronics', 'ssd-nvme', 'SNV2S/500G', {'capacity_gb': 500, 'type': 'NVMe', 'interface': 'PCIe 4.0'}),
            ('Crucial MX500 4TB SATA SSD', 'crucial-4tb-sata', 'dell-technologies', 'ssd-nvme', 'CT4000MX500SSD1', {'capacity_gb': 4000, 'type': 'SATA', 'interface': 'SATA III'}),
            ('WD Red Pro 8TB HDD', 'wd-red-8tb', 'dell-technologies', 'hdd', 'WD8003FFBX', {'capacity_gb': 8000, 'type': 'HDD', 'interface': 'SATA'}),
            ('Seagate IronWolf Pro 12TB HDD', 'seagate-ironwolf-12tb', 'dell-technologies', 'hdd', 'ST12000NE0008', {'capacity_gb': 12000, 'type': 'HDD', 'interface': 'SATA'}),
            ('Intel X710 10GbE NIC', 'intel-x710-nic', 'dell-technologies', 'nic', 'X710DA2', {'type': 'SFP+', 'speed': '10GbE'}),
            ('Mellanox ConnectX-6 100GbE NIC', 'mellanox-connectx6', 'cisco-systems', 'nic', 'MCX623106AN-CDAT', {'type': 'QSFP56', 'speed': '100GbE'}),
            ('NVIDIA A100 80GB', 'nvidia-a100', 'dell-technologies', 'gpu', 'A100-80GB', {'type': 'A100', 'vram_gb': 80}),
            ('NVIDIA GeForce RTX 4090 24GB', 'nvidia-rtx-4090', 'samsung-electronics', 'gpu', 'RTX4090-24G', {'type': 'RTX 4090', 'vram_gb': 24}),
            ('NVIDIA RTX 6000 Ada 48GB', 'nvidia-rtx-6000', 'dell-technologies', 'gpu', 'RTX6000-ADA', {'type': 'RTX 6000 Ada', 'vram_gb': 48}),
            ('Intel Xeon Gold 6430', 'xeon-gold-6430', 'dell-technologies', 'cpu', 'SRMZS', {'type': 'Xeon Gold', 'cores': 32}),
            ('AMD EPYC 9654 Processor', 'amd-epyc-9654', 'dell-technologies', 'cpu', '100-000000789', {'type': 'EPYC 96-Core', 'cores': 96}),
            ('Noctua NH-D15 CPU Cooler', 'noctua-nh-d15', 'logitech-international', 'other', 'NH-D15-CH-BK', {'type': 'Air Cooler', 'fan_size': '140mm'}),
            ('Corsair RM1000x 1000W PSU', 'corsair-rm1000x', 'logitech-international', 'other', 'CP-9020201-EU', {'type': 'Modular PSU', 'wattage': 1000}),
            ('Dell PERC H755 RAID Controller', 'dell-perc-h755', 'dell-technologies', 'other', 'PERC-H755-FRONT', {'type': 'RAID Controller', 'interface': 'SAS 12Gb/s'}),
        ]
        self._components = {}
        for name, slug, mfr_slug, cat_slug, part_number, specs in comp_data:
            category = self._categories.get(cat_slug)
            obj, _ = Component.objects.get_or_create(
                slug=slug,
                defaults={
                    'name': name, 'manufacturer': self._manufacturers[mfr_slug],
                    'category': category, 'part_number': part_number, 'specs': specs,
                }
            )
            self._components[slug] = obj

        # --- Accessories ---
        acc_data = [
            ('USB-C Charger 65W', 'usb-c-charger-65w', 'dell-technologies', 'charger', '450-AFGM', 5),
            ('USB-C to HDMI Adapter', 'usb-c-hdmi-adapter', 'dell-technologies', 'adaptor', '470-AEGM', 3),
            ('Wireless Mouse MX Master 3S', 'mx-master-3s', 'logitech-international', 'mouse', '910-006556', 5),
            ('Wireless Keyboard MX Keys', 'mx-keys', 'logitech-international', 'keyboard', '920-009413', 3),
            ('Webcam C920s Pro', 'webcam-c920s', 'logitech-international', 'webcam', '960-001257', 2),
            ('Headset Zone Wireless 2', 'zone-wireless-2', 'logitech-international', 'headset', '981-000886', 3),
            ('Thunderbolt 4 Cable 2m', 'thunderbolt-4-cable', 'apple-inc', 'cable', 'MWP02ZM/A', 5),
            ('Magic Mouse', 'magic-mouse', 'apple-inc', 'mouse', 'MK2E3ZM/A', 2),
            ('Dell 27" Monitor P2723DE', 'dell-p2723de', 'dell-technologies', 'display', 'DELL-P2723DE', 2),
            ('Dell 24" Monitor P2422HE', 'dell-p2422he', 'dell-technologies', 'display', 'DELL-P2422HE', 2),
            ('Ergonomic Laptop Stand', 'ergo-laptop-stand', 'logitech-international', 'other', '939-001790', 5),
        ]
        self._accessories = {}
        for name, slug, mfr_slug, cat_slug, part_number, min_qty in acc_data:
            obj, _ = Accessory.objects.get_or_create(
                slug=slug,
                defaults={
                    'name': name, 'manufacturer': self._manufacturers[mfr_slug],
                    'category': self._categories.get(cat_slug), 'part_number': part_number,
                    'min_qty': min_qty,
                    'tenant': self._tenants.get('helheim-cloud-gmbh'),
                }
            )
            self._accessories[slug] = obj

        # --- Consumables ---
        cons_data = [
            ('HP 26X Laser Toner - Black', 'hp-26x-toner-black', 'hp-inc', 'toner', 'CF226X', 3),
            ('HP 26A Laser Toner - Cyan', 'hp-26a-toner-cyan', 'hp-inc', 'toner', 'CF221A', 2),
            ('Canon Ink Cartridge PGI-280XL', 'canon-pgi-280xl', 'brother-industries', 'ink', 'PGI-280XL', 3),
            ('Brother DR-241CL Drum Unit', 'brother-dr-241cl', 'brother-industries', 'toner', 'DR-241CL', 2),
            ('Arctic Silver 5 Thermal Paste', 'arctic-silver-5', 'dell-technologies', 'thermal-paste', 'AS5-3.5G', 5),
            ('AA Batteries Pack 24', 'aa-batteries-24', 'logitech-international', 'batteries', 'AA-24PK', 10),
            ('Whiteboard Markers Box 12', 'whiteboard-markers-12', 'logitech-international', 'other', 'WB-MRK-12', 5),
        ]
        self._consumables = {}
        for name, slug, mfr_slug, cat_slug, part_number, min_qty in cons_data:
            obj, _ = Consumable.objects.get_or_create(
                slug=slug,
                defaults={
                    'name': name, 'manufacturer': self._manufacturers[mfr_slug],
                    'category': self._categories.get(cat_slug), 'part_number': part_number,
                    'min_qty': min_qty,
                    'tenant': self._tenants.get('helheim-cloud-gmbh'),
                }
            )
            self._consumables[slug] = obj

        self.stdout.write(f'  {len(self._categories)} categories, {len(self._asset_types)} asset types, '
                          f'{len(self._components)} components, '                                    
                          f'{len(self._accessories)} accessories, {len(self._consumables)} consumables.')

    # ─────────────────────────────────────────
    # Phase 3: Hardware Assets & Components
    # ─────────────────────────────────────────

    def _seed_phase3(self):
        from assets.models import (
            Asset, InstalledSoftware,
        )
        from inventory.models import AccessoryAssignment, ConsumableAssignment
        from compliance.models import CustodyReceipt
        from components.models import ComponentStock, ComponentAllocation

        self.stdout.write('--- Phase 3: Hardware Assets ---')

        # Create 60+ assets across all types
        asset_data = [
            # (name, asset_tag, asset_type_slug, asset_role_slug, status_slug, holder_upn, location_slug, serial, purchase_cost, salvage_value, purchase_date)
            ('MBP16 Rene Rettig', 'ABX-001', 'macbook-pro-16-2024', 'laptop', 'in-use', 'rene.rettig', 'berlin-floor-1-eng', 'C02ZV1R9MD6T', 3599.00, 500.00, datetime.date(2024, 3, 15)),
            ('MBP16 Sarah Chen', 'ABX-002', 'macbook-pro-16-2024', 'laptop', 'in-use', 'sarah.chen', 'berlin-floor-1-eng', 'C02ZV2T8MD7U', 3599.00, 500.00, datetime.date(2024, 4, 10)),
            ('Latitude 5550 Marcus', 'ABX-003', 'dell-latitude-5550', 'laptop', 'in-use', 'marcus.johnson', 'ny-floor-5-eng', 'DL5550-001', 1899.00, 300.00, datetime.date(2024, 6, 1)),
            ('Latitude 5550 Elena', 'ABX-004', 'dell-latitude-5550', 'laptop', 'in-use', 'elena.rodriguez', 'munich-office-12a', 'DL5550-002', 1899.00, 300.00, datetime.date(2024, 7, 20)),
            ('ThinkPad X1 Thomas', 'ABX-005', 'thinkpad-x1-carbon-g12', 'laptop', 'in-use', 'thomas.weber', 'berlin-floor-1-eng', 'TPX1-001', 2199.00, 350.00, datetime.date(2024, 5, 12)),
            ('ThinkPad X1 Anna', 'ABX-006', 'thinkpad-x1-carbon-g12', 'laptop', 'in-use', 'anna.schmidt', 'berlin-floor-2-admin', 'TPX1-002', 2199.00, 350.00, datetime.date(2024, 5, 12)),
            ('MBA15 James Wilson', 'ABX-007', 'macbook-air-15-2024', 'laptop', 'in-use', 'james.wilson', 'ny-floor-5-eng', 'C02XK3P9N6QW', 1499.00, 200.00, datetime.date(2024, 8, 5)),
            ('EliteBook Yuki', 'ABX-008', 'hp-elitebook-860-g11', 'laptop', 'in-use', 'yuki.tanaka', 'berlin-floor-2-admin', 'HPEB-001', 2099.00, 300.00, datetime.date(2024, 4, 22)),
            ('MBP16 Omar Hassan', 'ABX-009', 'macbook-pro-16-2024', 'laptop', 'in-use', 'omar.hassan', 'munich-office-12a', 'C02ZV5R0PD8VX', 3599.00, 500.00, datetime.date(2024, 9, 1)),
            ('M2 Ultra Dev Server', 'ABX-010', 'mac-studio-2024', 'laptop', 'in-use', None, 'berlin-server-room-a', 'C07XM8S2DT6P', 6999.00, 1000.00, datetime.date(2024, 2, 10)),
            ('EliteBook Lisa', 'ABX-011', 'hp-elitebook-860-g11', 'laptop', 'in-use', 'lisa.andersson', 'berlin-floor-1-eng', 'HPEB-002', 2099.00, 300.00, datetime.date(2024, 11, 8)),
            ('Latitude 5550 Carlos', 'ABX-012', 'dell-latitude-5550', 'laptop', 'in-use', 'carlos.mendez', 'ny-floor-6-sales', 'DL5550-003', 1899.00, 300.00, datetime.date(2024, 10, 15)),
            ('ThinkPad X1 Priya', 'ABX-013', 'thinkpad-x1-carbon-g12', 'laptop', 'in-use', 'priya.patel', 'berlin-floor-1-eng', 'TPX1-003', 2199.00, 350.00, datetime.date(2024, 6, 18)),
            ('Precision 5680 WS-1', 'ABX-014', 'dell-precision-5680', 'laptop', 'in-use', None, 'berlin-floor-1-eng', 'PREC-001', 4299.00, 600.00, datetime.date(2024, 1, 25)),
            ('Precision 5680 WS-2', 'ABX-015', 'dell-precision-5680', 'laptop', 'available', None, 'berlin-floor-1-eng', 'PREC-002', 4299.00, 600.00, datetime.date(2024, 1, 25)),
            ('Mac Studio Design', 'ABX-016', 'mac-studio-2024', 'desktop', 'in-use', None, 'berlin-floor-1-eng', 'C07XM9G4DT8Q', 6999.00, 1000.00, datetime.date(2024, 3, 1)),
            ('OptiPlex 7010 Finance-1', 'ABX-017', 'dell-optiplex-7010', 'desktop', 'in-use', None, 'berlin-floor-2-admin', 'OPT-001', 1299.00, 200.00, datetime.date(2024, 4, 5)),
            ('OptiPlex 7010 Finance-2', 'ABX-018', 'dell-optiplex-7010', 'desktop', 'in-use', None, 'berlin-floor-2-admin', 'OPT-002', 1299.00, 200.00, datetime.date(2024, 4, 5)),
            ('OptiPlex 7010 HR-1', 'ABX-019', 'dell-optiplex-7010', 'desktop', 'in-use', None, 'berlin-floor-2-admin', 'OPT-003', 1299.00, 200.00, datetime.date(2024, 4, 12)),
            ('OptiPlex 7010 Exec-1', 'ABX-020', 'dell-optiplex-7010', 'desktop', 'in-use', None, 'berlin-floor-3-exec', 'OPT-004', 1299.00, 200.00, datetime.date(2024, 2, 28)),
            ('ThinkCentre M90q Backup', 'ABX-021', 'thinkcentre-m90q-gen5', 'desktop', 'available', None, 'berlin-server-room-a', 'TCM-001', 899.00, 100.00, datetime.date(2024, 8, 15)),
            ('PowerEdge R760 Prod-1', 'ABX-022', 'dell-poweredge-r760', 'server', 'in-use', None, 'ams-rack-row-1', 'SRV-PROD-01', 18500.00, 2000.00, datetime.date(2024, 1, 10)),
            ('PowerEdge R760 Prod-2', 'ABX-023', 'dell-poweredge-r760', 'server', 'in-use', None, 'ams-rack-row-1', 'SRV-PROD-02', 18500.00, 2000.00, datetime.date(2024, 1, 10)),
            ('ProLiant DL380 Dev-1', 'ABX-024', 'hpe-proliant-dl380-g11', 'server', 'in-use', None, 'ams-rack-row-2', 'SRV-DEV-01', 12500.00, 1500.00, datetime.date(2024, 3, 20)),
            ('PowerEdge R760 Backup', 'ABX-025', 'dell-poweredge-r760', 'server', 'available', None, 'ams-rack-row-2', 'SRV-BACKUP-01', 18500.00, 2000.00, datetime.date(2024, 6, 1)),
            ('iPhone 15 Pro Rene', 'ABX-026', 'iphone-15-pro', 'mobile-phone', 'in-use', 'rene.rettig', 'berlin-floor-1-eng', 'IP15P-001', 1299.00, 150.00, datetime.date(2024, 9, 22)),
            ('iPhone 15 Pro Sarah', 'ABX-027', 'iphone-15-pro', 'mobile-phone', 'in-use', 'sarah.chen', 'berlin-floor-1-eng', 'IP15P-002', 1299.00, 150.00, datetime.date(2024, 9, 22)),
            ('iPhone 15 Pro Marcus', 'ABX-028', 'iphone-15-pro', 'mobile-phone', 'in-use', 'marcus.johnson', 'ny-floor-5-eng', 'IP15P-003', 1299.00, 150.00, datetime.date(2024, 9, 25)),
            ('Galaxy S24 Elena', 'ABX-029', 'galaxy-s24-ultra', 'mobile-phone', 'in-use', 'elena.rodriguez', 'munich-office-12a', 'S24U-001', 1249.00, 150.00, datetime.date(2024, 10, 5)),
            ('iPad Pro James', 'ABX-030', 'ipad-pro-129-2024', 'tablet', 'in-use', 'james.wilson', 'ny-floor-5-eng', 'IPP-001', 1099.00, 100.00, datetime.date(2024, 7, 15)),
            ('Surface Pro Thomas', 'ABX-031', 'surface-pro-10', 'tablet', 'in-use', 'thomas.weber', 'berlin-floor-3-exec', 'SP10-001', 1799.00, 200.00, datetime.date(2024, 8, 20)),
            ('Catalyst 9300 Core-1', 'ABX-032', 'cisco-catalyst-9300', 'network-device', 'in-use', None, 'ams-rack-row-3', 'C9300-CORE-01', 8500.00, 500.00, datetime.date(2024, 2, 1)),
            ('Catalyst 9300 Core-2', 'ABX-033', 'cisco-catalyst-9300', 'network-device', 'available', None, 'berlin-server-room-a', 'C9300-CORE-02', 8500.00, 500.00, datetime.date(2024, 2, 1)),
            ('Meraki MR46 AP-1', 'ABX-034', 'meraki-mr46', 'network-device', 'in-use', None, 'berlin-floor-1-eng', 'MR46-AP-01', 1200.00, 100.00, datetime.date(2024, 3, 5)),
            ('Meraki MR46 AP-2', 'ABX-035', 'meraki-mr46', 'network-device', 'in-use', None, 'berlin-floor-2-admin', 'MR46-AP-02', 1200.00, 100.00, datetime.date(2024, 3, 5)),
            ('UDM Pro Gateway', 'ABX-036', 'unifi-dream-machine-pro', 'network-device', 'in-use', None, 'berlin-server-room-a', 'UDM-PRO-01', 379.00, 50.00, datetime.date(2024, 1, 15)),
            ('Synology NAS Primary', 'ABX-037', 'synology-ds1823xs', 'storage-device', 'in-use', None, 'ams-rack-row-2', 'NAS-PRIMARY-01', 4200.00, 400.00, datetime.date(2024, 5, 10)),
            ('Dell P2723DE Monitor-1', 'ABX-038', 'dell-p2723de-monitor', 'monitor', 'in-use', 'rene.rettig', 'berlin-floor-1-eng', 'MON-DELL-01', 499.00, 50.00, datetime.date(2024, 3, 15)),
            ('Dell P2723DE Monitor-2', 'ABX-039', 'dell-p2723de-monitor', 'monitor', 'in-use', 'sarah.chen', 'berlin-floor-1-eng', 'MON-DELL-02', 499.00, 50.00, datetime.date(2024, 4, 10)),
            ('Latitude 5550 Spare-1', 'ABX-040', 'dell-latitude-5550', 'laptop', 'available', None, 'berlin-floor-1-eng', 'DL5550-SP01', 1899.00, 300.00, datetime.date(2024, 10, 1)),
            ('Latitude 5550 Spare-2', 'ABX-041', 'dell-latitude-5550', 'laptop', 'available', None, 'ny-floor-5-eng', 'DL5550-SP02', 1899.00, 300.00, datetime.date(2024, 10, 1)),
            ('MBP16 Repair-1', 'ABX-042', 'macbook-pro-16-2024', 'laptop', 'pending-repair', None, 'berlin-floor-1-eng', 'C02ZV0R5LD1UY', 3599.00, 500.00, datetime.date(2024, 2, 20)),
            ('ThinkPad X1 Retired-1', 'ABX-043', 'thinkpad-x1-carbon-g12', 'laptop', 'retired', None, 'berlin-server-room-a', 'TPX1-RET-01', 1899.00, 100.00, datetime.date(2021, 3, 15)),
            ('iPhone 15 Pro Spare', 'ABX-044', 'iphone-15-pro', 'mobile-phone', 'available', None, 'berlin-floor-3-exec', 'IP15P-SP01', 1299.00, 150.00, datetime.date(2024, 11, 1)),
            ('iPad Pro Spare', 'ABX-045', 'ipad-pro-129-2024', 'tablet', 'available', None, 'ny-floor-6-sales', 'IPP-SP01', 1099.00, 100.00, datetime.date(2024, 11, 1)),
            ('OptiPlex 7010 Reception', 'ABX-046', 'dell-optiplex-7010', 'desktop', 'in-use', None, 'berlin-floor-3-exec', 'OPT-REC-01', 1299.00, 200.00, datetime.date(2024, 5, 20)),
            # New internal networking, AV & high-end desktop workstation assets
            ('UniFi Switch 48 Berlin HQ', 'ABX-050', 'unifi-switch-pro-48', 'network-device', 'in-use', None, 'berlin-server-room-a', 'USW-PRO-48-001', 1099.00, 100.00, datetime.date(2024, 4, 1)),
            ('UniFi Switch 48 AMS DC', 'ABX-051', 'unifi-switch-pro-48', 'network-device', 'in-use', None, 'ams-rack-row-3', 'USW-PRO-48-002', 1099.00, 100.00, datetime.date(2024, 4, 5)),
            ('Berlin Room 12 AV', 'ABX-052', 'logitech-meetup', 'peripheral', 'in-use', None, 'berlin-floor-1-eng', 'LOG-AV-001', 899.00, 50.00, datetime.date(2024, 6, 12)),
            ('Munich Room A AV', 'ABX-053', 'logitech-meetup', 'peripheral', 'in-use', None, 'munich-office-12a', 'LOG-AV-002', 899.00, 50.00, datetime.date(2024, 6, 15)),
            ('Precision 7960 Oliver', 'ABX-054', 'dell-precision-7960-tower', 'desktop', 'in-use', 'oliver.smith', 'berlin-floor-1-eng', 'PREC7960-001', 5499.00, 800.00, datetime.date(2024, 5, 1)),
            ('Precision 7960 Sophia', 'ABX-055', 'dell-precision-7960-tower', 'desktop', 'in-use', 'sophia.martinez', 'berlin-floor-1-eng', 'PREC7960-002', 5499.00, 800.00, datetime.date(2024, 5, 1)),
            # Customer Tenant Assets
            ('Latitude 5550 Klaus', 'FIN-001', 'dell-latitude-5550', 'laptop', 'in-use', 'klaus.fischer', 'finova-floor-2-trading', 'DL5550-FIN01', 1899.00, 300.00, datetime.date(2024, 3, 1)),
            ('Latitude 5550 Nina', 'FIN-002', 'dell-latitude-5550', 'laptop', 'in-use', 'nina.bergmann', 'finova-floor-3-risk', 'DL5550-FIN02', 1899.00, 300.00, datetime.date(2024, 3, 15)),
            ('EliteBook 860 Dr. Wagner', 'MED-001', 'hp-elitebook-860-g11', 'laptop', 'in-use', 'markus.wagner', 'medicare-ward-admin', 'HPEB-MED01', 2099.00, 300.00, datetime.date(2024, 4, 1)),
            ('EliteBook 860 Sophie', 'MED-002', 'hp-elitebook-860-g11', 'laptop', 'in-use', 'sophie.klein', 'medicare-lab-research', 'HPEB-MED02', 2099.00, 300.00, datetime.date(2024, 4, 15)),
            ('MB Air 15 Felix', 'TSI-001', 'macbook-air-15-2024', 'laptop', 'in-use', 'felix.bauer', 'techstartup-open-space', 'MBA-TSI01', 1499.00, 200.00, datetime.date(2024, 5, 1)),
            ('MB Air 15 Lena', 'TSI-002', 'macbook-air-15-2024', 'laptop', 'in-use', 'lena.schulz', 'techstartup-eng-lab', 'MBA-TSI02', 1499.00, 200.00, datetime.date(2024, 5, 10)),
            ('ThinkPad X1 Jonas', 'GRE-001', 'thinkpad-x1-carbon-g12', 'laptop', 'in-use', 'jonas.hoffmann', 'greenenergy-wind-analytics', 'TPX1-GRE01', 2199.00, 350.00, datetime.date(2024, 6, 1)),
            ('ThinkPad X1 Katja', 'GRE-002', 'thinkpad-x1-carbon-g12', 'laptop', 'in-use', 'katja.vogel', 'greenenergy-solar-ops', 'TPX1-GRE02', 2199.00, 350.00, datetime.date(2024, 6, 1)),
            ('OptiPlex 7010 Finova-1', 'FIN-003', 'dell-optiplex-7010', 'desktop', 'in-use', None, 'finova-floor-2-trading', 'OPT-FIN01', 1299.00, 200.00, datetime.date(2024, 2, 15)),
            ('OptiPlex 7010 Finova-2', 'FIN-004', 'dell-optiplex-7010', 'desktop', 'in-use', None, 'finova-floor-3-risk', 'OPT-FIN02', 1299.00, 200.00, datetime.date(2024, 2, 15)),
            ('Mac Studio TechStartup', 'TSI-003', 'mac-studio-2024', 'desktop', 'in-use', None, 'techstartup-eng-lab', 'MST-TSI01', 6999.00, 1000.00, datetime.date(2024, 6, 15)),
            ('iPhone 15 Pro Klaus', 'FIN-005', 'iphone-15-pro', 'mobile-phone', 'in-use', 'klaus.fischer', 'finova-floor-2-trading', 'IP15P-FIN01', 1299.00, 150.00, datetime.date(2024, 10, 1)),
            ('iPad Pro Medicare', 'MED-003', 'ipad-pro-129-2024', 'tablet', 'in-use', 'markus.wagner', 'medicare-ward-admin', 'IPP-MED01', 1099.00, 100.00, datetime.date(2024, 8, 1)),
            ('Surface Pro GreenEnergy', 'GRE-003', 'surface-pro-10', 'tablet', 'in-use', 'jonas.hoffmann', 'greenenergy-wind-analytics', 'SP10-GRE01', 1799.00, 200.00, datetime.date(2024, 7, 15)),
            ('Dell P2422HE Finova 1', 'FIN-006', 'dell-p2422he-monitor', 'monitor', 'in-use', 'klaus.fischer', 'finova-floor-2-trading', 'MON-FIN01', 379.00, 30.00, datetime.date(2024, 3, 1)),
            ('Dell P2422HE Finova 2', 'FIN-007', 'dell-p2422he-monitor', 'monitor', 'in-use', 'nina.bergmann', 'finova-floor-3-risk', 'MON-FIN02', 379.00, 30.00, datetime.date(2024, 3, 15)),
            ('Latitude 5550 GreenEnergy Spare', 'GRE-004', 'dell-latitude-5550', 'laptop', 'available', None, 'greenenergy-wind-analytics', 'DL5550-GRE01', 1899.00, 300.00, datetime.date(2024, 8, 1)),
            ('EliteBook 860 Finova Spare', 'FIN-008', 'hp-elitebook-860-g11', 'laptop', 'available', None, 'finova-floor-2-trading', 'HPEB-FIN01', 2099.00, 300.00, datetime.date(2024, 7, 1)),
            # New Customer Tenant Assets (Apex Logistics and Quantum Robotics)
            ('Latitude 5550 Noah', 'APX-001', 'dell-latitude-5550', 'laptop', 'in-use', 'noah.schmidt', 'frankfurt-depot-shelf-b', 'DL5550-APX01', 1899.00, 300.00, datetime.date(2024, 5, 1)),
            ('Latitude 5550 Mia', 'APX-002', 'dell-latitude-5550', 'laptop', 'in-use', 'mia.petrov', 'frankfurt-depot-shelf-b', 'DL5550-APX02', 1899.00, 300.00, datetime.date(2024, 5, 10)),
            ('ThinkPad X1 David', 'QNT-001', 'thinkpad-x1-carbon-g12', 'laptop', 'in-use', 'david.kim', 'boston-assembly-floor', 'TPX1-QNT01', 2199.00, 350.00, datetime.date(2024, 6, 1)),
            ('ThinkPad X1 Wei', 'QNT-002', 'thinkpad-x1-carbon-g12', 'laptop', 'in-use', 'wei.zhang', 'boston-assembly-floor', 'TPX1-QNT02', 2199.00, 350.00, datetime.date(2024, 6, 1)),
            ('Precision 7960 Alex', 'QNT-003', 'dell-precision-7960-tower', 'desktop', 'in-use', 'alexander.gruber', 'boston-assembly-floor', 'PREC7960-QNT01', 5499.00, 800.00, datetime.date(2024, 6, 10)),
        ]
        self._assets = {}
        for data in asset_data:
            name, tag, at_slug, role_slug, status_slug = data[0], data[1], data[2], data[3], data[4]
            holder = self._holders.get(data[5] or '') if data[5] else None
            location = self._locations.get(data[6] or '') if data[6] else None

            # If checked out to a holder, physical location is None in Asset table
            base_location = None if (holder and status_slug == 'in-use') else location

            # Construct dynamic custom values based on custom fieldsets
            c_values = {}
            asset_type = self._asset_types.get(at_slug)
            if asset_type and asset_type.custom_fieldset:
                fs_name = asset_type.custom_fieldset.name
                if fs_name == 'Laptop Specs':
                    c_values = {
                        'hostname': f"{role_slug}-{tag.lower()}",
                        'os_version': random.choice(['Windows 11 23H2', 'macOS Sequoia 15.1', 'Ubuntu 24.04 LTS']),
                        'encrypted': True,
                        'department': random.choice(['Engineering', 'Finance', 'HR', 'Marketing', 'Sales', 'Operations']),
                        'cpu_architecture': random.choice(['x86_64', 'ARM64']),
                        'ram_slot_count': random.choice([2, 4]),
                    }
                elif fs_name == 'Mobile Device Specs':
                    c_values = {
                        'sim_number': f"+49-170-{random.randint(1000000, 9999999)}",
                        'imei': f"35{random.randint(100000000000, 999999999999)}",
                        'screen_size': random.choice([6.1, 6.7, 11.0]),
                        'os_version': random.choice(['iOS 17.5', 'Android 14']),
                    }
                elif fs_name == 'Server Specs':
                    c_values = {
                        'hostname': f"srv-{tag.lower()}.local",
                        'os_version': random.choice(['RHEL 9.3', 'Ubuntu Server 22.04', 'Windows Server 2025']),
                        'department': 'Operations',
                        'floor': random.randint(1, 3),
                    }
                elif fs_name == 'Network Switch Specs':
                    c_values = {
                        'hostname': f"sw-{tag.lower()}.local",
                        'ip_address': f"10.100.{random.randint(1, 254)}.{random.randint(1, 254)}",
                        'port_count': random.choice([24, 48]),
                        'poe_budget_w': random.choice([370, 740]),
                        'firmware_version': 'v16.12.8',
                    }
                elif fs_name == 'AV & Conference Specs':
                    c_values = {
                        'screen_resolution': '3840x2160 (4K)',
                        'input_ports': 'HDMI, DisplayPort, USB-C',
                        'mounted_state': random.choice(['Wall-Mounted', 'Ceiling-Mounted']),
                    }

            obj, created = Asset.objects.get_or_create(
                asset_tag=tag,
                defaults={
                    'name': name, 'asset_type': asset_type,
                    'asset_role': self._asset_roles.get(role_slug),
                    'status': self._status_labels.get(status_slug),
                    'location': base_location,
                    'tenant': location.tenant if location and location.tenant else None,
                    'serial_number': data[7], 'purchase_cost': data[8],
                    'salvage_value': data[9], 'purchase_date': data[10],
                    'supplier': self._suppliers.get('itz-solutions'),
                    'order_number': f'PO-2024-{random.randint(1000, 9999)}',
                    'custom_values': c_values,
                }
            )
            self._assets[tag] = obj

            # Create proper checkout log entries for new or unassigned assets
            if created or not obj.assignments.exists():
                from assets.models import AssetAssignment
                from organization.models import AssetHolder, Location
                
                # Case A: Checked out to a Holder
                if holder and status_slug == 'in-use':
                    ct_holder = ContentType.objects.get_for_model(AssetHolder)
                    AssetAssignment.objects.get_or_create(
                        asset=obj,
                        assigned_to_content_type=ct_holder,
                        assigned_to_object_id=holder.pk,
                        defaults={
                            'checked_out_by': self._users[0],
                            'is_active': True,
                            'notes': 'Initial seed checkout to holder',
                        }
                    )
                    # Support legacy direct GFK queries
                    from organization.models import AssetHolderAssignment
                    ct_asset = ContentType.objects.get_for_model(Asset)
                    AssetHolderAssignment.objects.get_or_create(
                        asset_holder=holder, content_type=ct_asset, object_id=obj.pk,
                    )
                
                # Case B: Checked out to a Location
                elif not holder and status_slug == 'in-use' and location:
                    ct_loc = ContentType.objects.get_for_model(Location)
                    AssetAssignment.objects.get_or_create(
                        asset=obj,
                        assigned_to_content_type=ct_loc,
                        assigned_to_object_id=location.pk,
                        defaults={
                            'checked_out_by': self._users[0],
                            'is_active': True,
                            'notes': 'Initial seed checkout to location',
                        }
                    )

        # --- ComponentStock (NEW) ---
        stock_data = [
            ('samsung-32gb-ddr5', 'berlin-server-room-a', 50),
            ('samsung-32gb-ddr5', 'ams-rack-row-1', 40),
            ('crucial-16gb-ddr4', 'berlin-floor-1-eng', 30),
            ('crucial-16gb-ddr4', 'ny-floor-5-eng', 25),
            ('corsair-64gb-ddr5', 'berlin-floor-1-eng', 15),
            ('kingston-8gb-ddr4', 'munich-staging-area', 30),
            ('samsung-1tb-nvme', 'berlin-server-room-a', 40),
            ('samsung-2tb-nvme', 'ams-rack-row-2', 30),
            ('kingston-500gb-nvme', 'berlin-floor-2-admin', 20),
            ('crucial-4tb-sata', 'ams-rack-row-2', 15),
            ('wd-red-8tb', 'ams-rack-row-2', 50),
            ('seagate-ironwolf-12tb', 'berlin-server-room-a', 30),
            ('intel-x710-nic', 'berlin-server-room-a', 15),
            ('intel-x710-nic', 'ams-rack-row-3', 20),
            ('mellanox-connectx6', 'ams-rack-row-3', 10),
            ('nvidia-a100', 'ams-rack-row-1', 5),
            ('nvidia-rtx-4090', 'berlin-floor-1-eng', 8),
            ('nvidia-rtx-6000', 'berlin-floor-1-eng', 4),
            ('xeon-gold-6430', 'berlin-server-room-a', 10),
            ('amd-epyc-9654', 'ams-rack-row-1', 6),
            ('noctua-nh-d15', 'berlin-floor-1-eng', 25),
            ('corsair-rm1000x', 'berlin-floor-1-eng', 20),
            ('dell-perc-h755', 'berlin-server-room-a', 10),
            # Customer stock locations
            ('samsung-32gb-ddr5', 'frankfurt-depot-shelf-b', 15),
            ('samsung-1tb-nvme', 'frankfurt-depot-shelf-b', 20),
            ('crucial-16gb-ddr4', 'frankfurt-hub-floor-1', 10),
            ('nvidia-rtx-4090', 'boston-assembly-floor', 5),
            ('amd-epyc-9654', 'lab-a-testing-bench', 4),
        ]
        self._stocks = []
        for comp_slug, loc_slug, qty in stock_data:
            comp_obj = self._components.get(comp_slug)
            loc_obj = self._locations.get(loc_slug)
            if comp_obj and loc_obj:
                stock_obj, _ = ComponentStock.objects.get_or_create(
                    component=comp_obj,
                    location=loc_obj,
                    defaults={'qty': qty}
                )
                self._stocks.append(stock_obj)

        # --- ComponentAllocations ---
        alloc_data = [
            ('samsung-32gb-ddr5', 'ABX-022', 2),
            ('samsung-32gb-ddr5', 'ABX-023', 2),
            ('samsung-1tb-nvme', 'ABX-022', 1),
            ('samsung-2tb-nvme', 'ABX-023', 2),
            ('wd-red-8tb', 'ABX-022', 2),
            ('wd-red-8tb', 'ABX-023', 2),
            ('wd-red-8tb', 'ABX-025', 4),
            ('intel-x710-nic', 'ABX-022', 1),
            ('intel-x710-nic', 'ABX-023', 1),
            ('nvidia-a100', 'ABX-024', 1),
            ('xeon-gold-6430', 'ABX-024', 1),
            ('crucial-16gb-ddr4', 'ABX-003', 1),
            ('crucial-16gb-ddr4', 'ABX-004', 1),
            ('samsung-1tb-nvme', 'ABX-001', 1),
            ('samsung-1tb-nvme', 'ABX-002', 1),
            # New allocations
            ('corsair-64gb-ddr5', 'ABX-014', 1),
            ('corsair-64gb-ddr5', 'ABX-015', 1),
            ('nvidia-rtx-4090', 'ABX-014', 1),
            ('nvidia-rtx-4090', 'ABX-015', 1),
            ('noctua-nh-d15', 'ABX-014', 1),
            ('corsair-rm1000x', 'ABX-014', 1),
            ('seagate-ironwolf-12tb', 'ABX-037', 4), # Primary NAS loaded with HDDs!
            ('dell-perc-h755', 'ABX-022', 1),
            ('dell-perc-h755', 'ABX-023', 1),
            ('intel-x710-nic', 'ABX-032', 1),
            ('mellanox-connectx6', 'ABX-033', 1),
            # New Workstations allocations
            ('corsair-64gb-ddr5', 'ABX-054', 1),
            ('corsair-64gb-ddr5', 'ABX-055', 1),
            ('corsair-64gb-ddr5', 'QNT-003', 1),
            ('nvidia-rtx-6000', 'ABX-054', 1),
            ('nvidia-rtx-6000', 'ABX-055', 1),
            ('nvidia-rtx-6000', 'QNT-003', 1),
            # Customer allocations
            ('samsung-32gb-ddr5', 'FIN-001', 1),
            ('samsung-1tb-nvme', 'FIN-001', 1),
            ('samsung-32gb-ddr5', 'FIN-002', 1),
            ('samsung-1tb-nvme', 'FIN-002', 1),
            ('crucial-16gb-ddr4', 'MED-001', 1),
            ('crucial-16gb-ddr4', 'MED-002', 1),
            ('samsung-1tb-nvme', 'TSI-001', 1),
            ('samsung-1tb-nvme', 'TSI-002', 1),
            ('corsair-64gb-ddr5', 'TSI-003', 1),
            ('nvidia-rtx-4090', 'TSI-003', 1),
        ]
        for comp_slug, asset_tag, qty in alloc_data:
            ComponentAllocation.objects.get_or_create(
                component=self._components[comp_slug],
                asset=self._assets[asset_tag],
                defaults={'qty_allocated': qty},
            )

        # --- AccessoryAssignments ---
        for acc_slug, holder_upn, qty in [
            ('usb-c-charger-65w', 'rene.rettig', 1),
            ('usb-c-charger-65w', 'sarah.chen', 1),
            ('mx-master-3s', 'marcus.johnson', 1),
            ('mx-keys', 'elena.rodriguez', 1),
            ('webcam-c920s', 'thomas.weber', 1),
            ('usb-c-hdmi-adapter', 'anna.schmidt', 1),
            ('zone-wireless-2', 'james.wilson', 1),
            ('magic-mouse', 'yuki.tanaka', 1),
            ('usb-c-charger-65w', 'lisa.andersson', 1),
            ('mx-master-3s', 'carlos.mendez', 1),
            ('mx-master-3s', 'priya.patel', 1),
            # Customer accessory assignments
            ('mx-master-3s', 'klaus.fischer', 1),
            ('mx-keys', 'nina.bergmann', 1),
            ('zone-wireless-2', 'markus.wagner', 1),
            ('webcam-c920s', 'sophie.klein', 1),
            ('magic-mouse', 'felix.bauer', 1),
            ('usb-c-charger-65w', 'lena.schulz', 1),
            ('usb-c-hdmi-adapter', 'jonas.hoffmann', 1),
            ('mx-master-3s', 'katja.vogel', 1),
        ]:
            AccessoryAssignment.objects.create(
                accessory=self._accessories[acc_slug],
                assigned_holder=self._holders[holder_upn],
                qty=qty,
            )

        # --- ConsumableAssignments ---
        for cons_slug, holder_upn, qty in [
            ('hp-26x-toner-black', 'anna.schmidt', 2),
            ('arctic-silver-5', 'thomas.weber', 1),
            ('aa-batteries-24', 'lisa.andersson', 1),
            ('whiteboard-markers-12', 'elena.rodriguez', 1),
        ]:
            ConsumableAssignment.objects.create(
                consumable=self._consumables[cons_slug],
                assigned_holder=self._holders[holder_upn],
                qty=qty,
            )

        # --- CustodyReceipt ---
        for tag, holder_upn in [('ABX-001', 'rene.rettig'), ('ABX-002', 'sarah.chen'), ('ABX-003', 'marcus.johnson'),
                                 ('FIN-001', 'klaus.fischer'), ('MED-001', 'markus.wagner'), ('GRE-001', 'jonas.hoffmann')]:
            h = self._holders[holder_upn]
            hash_val = hashlib.sha256(f"{tag}-{h.pk}-{timezone.now()}".encode()).hexdigest()[:64]
            CustodyReceipt.objects.get_or_create(
                verification_hash=hash_val,
                defaults={
                    'asset': self._assets[tag],
                    'holder': h,
                    'signature_canvas': f'data:image/png;base64,MOCK_SIGNATURE_{tag}',
                    'eula_version': '1.0',
                }
            )
        self.stdout.write(f'  {len(self._assets)} assets, components, accessories, consumables, custody receipts.')

    # ─────────────────────────────────────────
    # Phase 4: Software & Licenses
    # ─────────────────────────────────────────

    def _seed_phase4(self):
        from software.models import Software
        from licenses.models import License, LicenseSeatAssignment

        self.stdout.write('--- Phase 4: Software & Licenses ---')

        # Software Products
        sw_data = [
            ('Windows 11 Enterprise', 'dell-technologies'),
            ('macOS Sonoma', 'apple-inc'),
            ('Microsoft 365 E5', 'microsoft-corporation'),
            ('Microsoft Office 2024 LTSC', 'microsoft-corporation'),
            ('Adobe Creative Cloud', 'microsoft-corporation'),
            ('JetBrains IntelliJ IDEA Ultimate', 'microsoft-corporation'),
            ('Docker Desktop Enterprise', 'microsoft-corporation'),
            ('VMware vSphere 8 Enterprise Plus', 'dell-technologies'),
            ('Slack Enterprise Grid', 'microsoft-corporation'),
            ('Zoom Enterprise', 'microsoft-corporation'),
            ('1Password Business', 'microsoft-corporation'),
            ('SentinelOne Singularity', 'microsoft-corporation'),
            ('CrowdStrike Falcon', 'microsoft-corporation'),
            ('Cisco AnyConnect VPN', 'cisco-systems'),
            ('Ubuntu Pro 22.04', 'dell-technologies'),
        ]
        self._software_products = {}
        for name, mfr_slug in sw_data:
            obj, _ = Software.objects.get_or_create(
                name=name,
                defaults={'manufacturer': self._manufacturers[mfr_slug]}
            )
            self._software_products[name] = obj

        # Licenses
        license_data = [
            ('Win11-Ent-Vol-001', 'Windows 11 Enterprise', 'perpetual_seat', 'W11ENT-XXXX-YYYY-ZZZZ-AAAA', 150, None, datetime.date(2024, 1, 1), 'helheim-cloud-gmbh'),
            ('M365-E5-Vol', 'Microsoft 365 E5', 'subscription_seat', None, 150, 22500.00, datetime.date(2024, 1, 1), 'helheim-cloud-gmbh'),
            ('Office2024-Vol', 'Microsoft Office 2024 LTSC', 'perpetual_seat', 'OFF2024-XXXX-YYYY-ZZZZ-BBBB', 100, None, datetime.date(2024, 3, 1), 'helheim-cloud-gmbh'),
            ('Adobe-CC-Team', 'Adobe Creative Cloud', 'subscription_seat', None, 25, 15000.00, datetime.date(2024, 2, 1), 'helheim-labs-inc'),
            ('IntelliJ-Ultimate-50', 'JetBrains IntelliJ IDEA Ultimate', 'subscription_seat', None, 50, 8500.00, datetime.date(2024, 4, 1), 'helheim-labs-inc'),
            ('Docker-Enterprise', 'Docker Desktop Enterprise', 'subscription_seat', None, 30, 4500.00, datetime.date(2024, 5, 1), 'helheim-labs-inc'),
            ('vSphere-Ent-8', 'VMware vSphere 8 Enterprise Plus', 'subscription_seat', None, 10, 35000.00, datetime.date(2024, 2, 15), 'helheim-labs-inc'),
            ('Slack-Ent', 'Slack Enterprise Grid', 'subscription_seat', None, 150, 18000.00, datetime.date(2024, 1, 1), 'helheim-labs-inc'),
            ('Zoom-Ent', 'Zoom Enterprise', 'subscription_seat', None, 150, 12000.00, datetime.date(2024, 1, 1), 'helheim-cloud-gmbh'),
            ('1Password-Biz', '1Password Business', 'subscription_seat', None, 150, 12000.00, datetime.date(2024, 1, 1), 'helheim-cloud-gmbh'),
            ('S1-Complete-1000', 'SentinelOne Singularity', 'subscription_seat', None, 1000, 45000.00, datetime.date(2024, 1, 1), 'helheim-security-ag'),
            ('CS-Falcon-1000', 'CrowdStrike Falcon', 'subscription_seat', None, 1000, 55000.00, datetime.date(2024, 1, 1), 'helheim-security-ag'),
            ('Cisco-AnyConnect', 'Cisco AnyConnect VPN', 'subscription_seat', None, 200, 8000.00, datetime.date(2024, 1, 1), 'helheim-cloud-gmbh'),
            # Customer Licenses
            ('Finova-M365-E3', 'Microsoft 365 E5', 'subscription_seat', None, 75, 11250.00, datetime.date(2024, 5, 1), 'finova-capital-ag'),
            ('MediCare-Office-50', 'Microsoft Office 2024 LTSC', 'perpetual_seat', 'MED-OFF2024-XXXX-YYYY', 50, None, datetime.date(2024, 6, 1), 'medicare-health-gmbh'),
            ('TechStartup-JetBrains', 'JetBrains IntelliJ IDEA Ultimate', 'subscription_seat', None, 10, 1700.00, datetime.date(2024, 8, 1), 'techstartup-io-gmbh'),
            ('GreenEnergy-Win11', 'Windows 11 Enterprise', 'perpetual_seat', 'GRE-W11ENT-XXXX-YYYY', 30, None, datetime.date(2024, 9, 1), 'greenenergy-solutions-se'),
        ]
        self._licenses = []
        for name, sw_name, ltype, key, seats, cost, purchase_date, tenant_slug in license_data:
            obj = License.objects.create(
                name=name,
                software=self._software_products[sw_name],
                license_type=ltype,
                product_key=key or '',
                seats=seats,
                purchase_cost=cost,
                purchase_date=purchase_date,
                order_number=f'PO-SW-{random.randint(1000, 9999)}',
                tenant=self._tenants.get(tenant_slug),
            )
            self._licenses.append(obj)

        # LicenseSeatAssignments
        holder_ups = list(self._holders.keys())
        for lic in self._licenses[:6]:
            num_assign = min(lic.seats, random.randint(3, 8))
            assigned_holders = random.sample(holder_ups, num_assign)
            for upn in assigned_holders:
                try:
                    LicenseSeatAssignment.objects.create(
                        license=lic,
                        assigned_holder=self._holders[upn],
                    )
                except Exception:
                    pass  # CheckConstraint may fail if asset also assigned

        self.stdout.write(f'  {len(self._software_products)} software products, {len(self._licenses)} licenses.')

        # --- Installed Software (creates links between assets and software) ---
        from assets.models import InstalledSoftware
        sw_installs = [
            ('ABX-001', 'Windows 11 Enterprise', '23H2', 'Intune', datetime.date(2024, 3, 15)),
            ('ABX-002', 'Windows 11 Enterprise', '23H2', 'Intune', datetime.date(2024, 4, 10)),
            ('ABX-003', 'Windows 11 Enterprise', '23H2', 'Intune', datetime.date(2024, 6, 1)),
            ('ABX-004', 'Windows 11 Enterprise', '23H2', 'Intune', datetime.date(2024, 7, 20)),
            ('ABX-005', 'Windows 11 Enterprise', '23H2', 'Intune', datetime.date(2024, 5, 12)),
            ('ABX-006', 'Windows 11 Enterprise', '23H2', 'Intune', datetime.date(2024, 5, 12)),
            ('ABX-007', 'macOS Sonoma', '14.5', 'Intune', datetime.date(2024, 8, 5)),
            ('ABX-008', 'Windows 11 Enterprise', '23H2', 'Intune', datetime.date(2024, 4, 22)),
            ('ABX-009', 'macOS Sonoma', '14.5', 'Intune', datetime.date(2024, 9, 1)),
            ('ABX-010', 'macOS Sonoma', '14.5', 'Intune', datetime.date(2024, 2, 10)),
            ('ABX-022', 'Ubuntu Pro 22.04', '22.04.4', 'Lansweeper', datetime.date(2024, 1, 10)),
            ('ABX-023', 'Ubuntu Pro 22.04', '22.04.4', 'Lansweeper', datetime.date(2024, 1, 10)),
            ('ABX-024', 'VMware vSphere 8 Enterprise Plus', '8.0.2', 'Lansweeper', datetime.date(2024, 3, 20)),
            ('ABX-001', 'Microsoft 365 E5', '', 'Intune', datetime.date(2024, 3, 15)),
            ('ABX-002', 'Microsoft 365 E5', '', 'Intune', datetime.date(2024, 4, 10)),
            ('ABX-003', 'CrowdStrike Falcon', '', 'Intune', datetime.date(2024, 6, 1)),
            ('ABX-004', 'SentinelOne Singularity', '', 'Intune', datetime.date(2024, 7, 20)),
            ('ABX-022', 'Docker Desktop Enterprise', '4.30.0', 'Lansweeper', datetime.date(2024, 1, 10)),
            ('ABX-023', 'Docker Desktop Enterprise', '4.30.0', 'Lansweeper', datetime.date(2024, 1, 10)),
            ('ABX-014', 'JetBrains IntelliJ IDEA Ultimate', '2024.2', 'Intune', datetime.date(2024, 1, 25)),
        ]
        installed_count = 0
        for tag, sw_name, version, agent, install_date in sw_installs:
            sw_product = self._software_products.get(sw_name)
            if sw_product and tag in self._assets:
                _, created = InstalledSoftware.objects.get_or_create(
                    asset=self._assets[tag],
                    software=sw_product,
                    version_detected=version,
                    defaults={
                        'discovered_by_agent': agent,
                        'install_date': install_date,
                        'last_seen_date': timezone.now() - datetime.timedelta(days=random.randint(0, 30)),
                    }
                )
                if created:
                    installed_count += 1
        self.stdout.write(f'  {installed_count} installed software records created.')

    # ─────────────────────────────────────────
    # Phase 5: Subscriptions
    # ─────────────────────────────────────────

    def _seed_phase5(self):
        from subscriptions.models import Provider, Subscription, SubscriptionAssignment
        from assets.models import Asset

        self.stdout.write('--- Phase 5: Subscriptions ---')

        # Providers
        provider_data = [
            ('Amazon Web Services', 'aws-helheim', 'https://aws.amazon.com/console'),
            ('Microsoft Azure', 'az-helheim', 'https://portal.azure.com'),
            ('Google Cloud Platform', 'gcp-helheim', 'https://console.cloud.google.com'),
            ('GitHub Enterprise', 'gh-helheim', 'https://github.com/enterprises/helheim'),
            ('Cloudflare', 'cf-helheim', 'https://dash.cloudflare.com'),
        ]
        self._providers = {}
        for name, acct_id, url in provider_data:
            obj, _ = Provider.objects.get_or_create(
                name=name,
                defaults={'account_id': acct_id, 'portal_url': url}
            )
            self._providers[name] = obj

        # Subscriptions
        sub_data = [
            ('AWS Production Account', 'Amazon Web Services', 'saas', datetime.date(2024, 1, 1), datetime.date(2025, 1, 1), 120000.00, 12, 'helheim-cloud-gmbh'),
            ('Azure Enterprise Agreement', 'Microsoft Azure', 'saas', datetime.date(2024, 3, 1), datetime.date(2027, 3, 1), 250000.00, 36, 'helheim-cloud-gmbh'),
            ('GCP Starter', 'Google Cloud Platform', 'saas', datetime.date(2024, 6, 1), datetime.date(2025, 6, 1), 36000.00, 12, 'helheim-cloud-gmbh'),
            ('GitHub Enterprise Cloud', 'GitHub Enterprise', 'saas', datetime.date(2024, 1, 1), datetime.date(2025, 1, 1), 42000.00, 12, 'helheim-labs-inc'),
            ('Cloudflare Enterprise', 'Cloudflare', 'support', datetime.date(2024, 2, 1), datetime.date(2025, 2, 1), 24000.00, 12, 'helheim-security-ag'),
            ('AWS Dev/Test Sandbox', 'Amazon Web Services', 'saas', datetime.date(2024, 4, 1), datetime.date(2025, 4, 1), 18000.00, 12, 'helheim-labs-inc'),
            # Customer Subscriptions
            ('Finova AWS Workloads', 'Amazon Web Services', 'saas', datetime.date(2024, 5, 1), datetime.date(2025, 5, 1), 85000.00, 12, 'finova-capital-ag'),
            ('Finova Azure Backup', 'Microsoft Azure', 'saas', datetime.date(2024, 6, 1), datetime.date(2026, 6, 1), 48000.00, 24, 'finova-capital-ag'),
            ('MediCare GCP Analytics', 'Google Cloud Platform', 'saas', datetime.date(2024, 7, 1), datetime.date(2025, 7, 1), 32000.00, 12, 'medicare-health-gmbh'),
            ('TechStartup GitHub Team', 'GitHub Enterprise', 'saas', datetime.date(2024, 8, 1), datetime.date(2025, 8, 1), 4800.00, 12, 'techstartup-io-gmbh'),
            ('GreenEnergy Cloudflare Pro', 'Cloudflare', 'support', datetime.date(2024, 9, 1), datetime.date(2025, 9, 1), 12000.00, 12, 'greenenergy-solutions-se'),
        ]
        self._subscriptions = []
        for name, prov_name, stype, start, renewal, cost, term, tenant_slug in sub_data:
            obj = Subscription.objects.create(
                name=name, provider=self._providers[prov_name],
                type=stype,
                start_date=start, renewal_date=renewal,
                renewal_cost=cost, term_months=term,
                description=f'{prov_name} subscription - {stype.upper()}',
                tenant=self._tenants.get(tenant_slug),
            )
            self._subscriptions.append(obj)

        # SubscriptionAssignments (link to Locations and Assets)
        ct_asset = ContentType.objects.get_for_model(Asset)
        servers = [a for a in self._assets.values() if a.asset_role and a.asset_role.slug == 'server']
        for server in servers[:4]:
            SubscriptionAssignment.objects.get_or_create(
                subscription=self._subscriptions[0],
                content_type=ct_asset,
                object_id=server.pk,
                defaults={'notes': 'Provisioned workload node'}
            )

        self.stdout.write(f'  {len(self._providers)} providers, {len(self._subscriptions)} subscriptions.')

    # ─────────────────────────────────────────
    # Phase 6: Kits, Maintenance, ActivityLogs
    # ─────────────────────────────────────────

    def _seed_phase6(self):
        from assets.models import Asset, ActivityLog
        from inventory.models import Kit, KitItem
        from compliance.models import AssetMaintenance
        from licenses.models import License

        self.stdout.write('--- Phase 6: Kits, Maintenance, Activities ---')

        # Kits
        kit1 = Kit.objects.create(name='Developer Onboarding Kit', description='All essentials for a new developer.', tenant=self._tenants.get('helheim-cloud-gmbh'))
        KitItem.objects.create(kit=kit1, asset_type=self._asset_types['dell-latitude-5550'], qty=1)
        KitItem.objects.create(kit=kit1, accessory=self._accessories['mx-master-3s'], qty=1)
        KitItem.objects.create(kit=kit1, accessory=self._accessories['mx-keys'], qty=1)
        KitItem.objects.create(kit=kit1, accessory=self._accessories['usb-c-hdmi-adapter'], qty=1)
        KitItem.objects.create(kit=kit1, license=self._licenses[0], qty=1)

        kit2 = Kit.objects.create(name='Executive Onboarding Kit', description='Premium onboarding package for executives.', tenant=self._tenants.get('helheim-cloud-gmbh'))
        KitItem.objects.create(kit=kit2, asset_type=self._asset_types['macbook-pro-16-2024'], qty=1)
        KitItem.objects.create(kit=kit2, asset_type=self._asset_types['iphone-15-pro'], qty=1)
        KitItem.objects.create(kit=kit2, accessory=self._accessories['usb-c-charger-65w'], qty=2)
        KitItem.objects.create(kit=kit2, accessory=self._accessories['magic-mouse'], qty=1)
        KitItem.objects.create(kit=kit2, license=self._licenses[1], qty=1)

        kit3 = Kit.objects.create(name='Sales Representative Kit', description='Standard kit for sales team members.', tenant=self._tenants.get('helheim-labs-inc'))
        KitItem.objects.create(kit=kit3, asset_type=self._asset_types['thinkpad-x1-carbon-g12'], qty=1)
        KitItem.objects.create(kit=kit3, accessory=self._accessories['zone-wireless-2'], qty=1)
        KitItem.objects.create(kit=kit3, accessory=self._accessories['webcam-c920s'], qty=1)
        KitItem.objects.create(kit=kit3, license=self._licenses[7], qty=1)  # Zoom

        kit4 = Kit.objects.create(name='Server Provisioning Bundle', description='Rack-ready server hardware bundle.', tenant=self._tenants.get('helheim-cloud-gmbh'))
        KitItem.objects.create(kit=kit4, asset_type=self._asset_types['dell-poweredge-r760'], qty=1)
        KitItem.objects.create(kit=kit4, accessory=self._accessories['thunderbolt-4-cable'], qty=2)

        # Customer Kit
        kit5 = Kit.objects.create(name='Finova Trader Setup', description='Standard trading desk setup for Finova Capital.', tenant=self._tenants.get('finova-capital-ag'))
        KitItem.objects.create(kit=kit5, asset_type=self._asset_types['dell-latitude-5550'], qty=1)
        KitItem.objects.create(kit=kit5, accessory=self._accessories['mx-master-3s'], qty=1)
        KitItem.objects.create(kit=kit5, accessory=self._accessories['dell-p2723de'], qty=2)
        KitItem.objects.create(kit=kit5, accessory=self._accessories['usb-c-hdmi-adapter'], qty=1)

        # Asset Maintenance records (supplier must be FK now)
        maintenance_data = [
            ('ABX-001', 'repair', 'dell-direct', 0.00, datetime.date(2024, 6, 10), datetime.date(2024, 6, 11), 'Keyboard replacement under warranty'),
            ('ABX-022', 'upgrade', 'dell-direct', 2500.00, datetime.date(2024, 5, 1), datetime.date(2024, 5, 2), 'Added 2x 32GB RAM modules'),
            ('ABX-023', 'upgrade', 'dell-direct', 2500.00, datetime.date(2024, 5, 3), datetime.date(2024, 5, 4), 'Added 2x 32GB RAM modules'),
            ('ABX-032', 'repair', 'cdw-deutschland', 850.00, datetime.date(2024, 8, 15), None, 'Port failure on blade 3 - RMA in progress'),
            ('ABX-037', 'software_support', 'itz-solutions', 0.00, datetime.date(2024, 9, 1), datetime.date(2024, 9, 1), 'DSM 7.2 upgrade'),
            ('ABX-003', 'repair', 'dell-direct', 0.00, datetime.date(2024, 10, 5), datetime.date(2024, 10, 6), 'Display hinge repair under warranty'),
            ('ABX-042', 'repair', 'apple-business', 899.00, datetime.date(2024, 11, 1), None, 'Logic board failure - awaiting parts'),
            ('ABX-024', 'calibration', 'hp-enterprise-store', 450.00, datetime.date(2024, 7, 20), datetime.date(2024, 7, 20), 'Annual RAID battery replacement'),
            ('ABX-016', 'software_support', 'apple-business', 0.00, datetime.date(2024, 10, 10), datetime.date(2024, 10, 10), 'macOS 15.2 enterprise deployment'),
            ('ABX-025', 'hardware_support', 'dell-direct', 1200.00, datetime.date(2024, 6, 15), datetime.date(2024, 6, 16), 'PSU replacement - redundant unit failed'),
            # Customer maintenance records
            ('FIN-001', 'repair', 'notebooksbilliger-ag', 250.00, datetime.date(2024, 9, 10), datetime.date(2024, 9, 12), 'SSD upgrade from 256GB to 512GB'),
            ('MED-001', 'repair', 'hp-enterprise-store', 650.00, datetime.date(2024, 11, 5), datetime.date(2024, 11, 7), 'Display replacement after damage'),
            ('GRE-001', 'software_support', 'lenovo-pro', 0.00, datetime.date(2024, 10, 20), datetime.date(2024, 10, 20), 'BIOS update and driver refresh'),
            ('TSI-002', 'repair', 'apple-business', 350.00, datetime.date(2024, 8, 25), datetime.date(2024, 8, 26), 'Battery replacement'),
        ]
        for tag, mtype, supplier_slug, cost, start, completion, notes in maintenance_data:
            supplier = self._suppliers.get(supplier_slug)
            AssetMaintenance.objects.create(
                asset=self._assets[tag],
                title=f'{mtype.replace("_", " ").title()} - {self._assets[tag].name}',
                maintenance_type=mtype,
                supplier=supplier,
                cost=cost,
                start_date=start,
                completion_date=completion,
                notes=notes,
            )

        # ActivityLogs
        log_data = [
            ('ABX-001', 'checked_out', self._users[0], 'Checked out to Rene Rettig'),
            ('ABX-002', 'checked_out', self._users[0], 'Checked out to Sarah Chen'),
            ('ABX-003', 'checked_out', self._users[0], 'Checked out to Marcus Johnson'),
            ('ABX-003', 'audited', self._users[0], 'Annual audit completed'),
            ('ABX-022', 'updated_at', self._users[0], 'RAM upgraded from 128GB to 256GB'),
            ('ABX-042', 'updated_at', self._users[0], 'Status changed to Pending Repair'),
            ('ABX-001', 'audited', self._users[1], 'Quarterly audit completed'),
            ('ABX-007', 'checked_out', self._users[1], 'Checked out to James Wilson'),
            ('FIN-001', 'checked_out', self._users[0], 'Checked out to Klaus Fischer (Finova)'),
            ('MED-001', 'checked_out', self._users[0], 'Checked out to Dr. Markus Wagner (MediCare)'),
            ('GRE-001', 'checked_out', self._users[1], 'Checked out to Jonas Hoffmann (GreenEnergy)'),
            ('TSI-001', 'checked_out', self._users[1], 'Checked out to Felix Bauer (TechStartup)'),
            ('FIN-003', 'audited', self._users[0], 'Quarterly compliance audit'),
            ('MED-003', 'audited', self._users[0], 'Device inventory verification'),
        ]
        for tag, action, user, notes in log_data:
            ActivityLog.objects.create(
                asset=self._assets[tag],
                action=action,
                user=user,
                notes=notes,
            )

        self.stdout.write('  5 kits, 14 maintenance records, 14 activity logs.')
