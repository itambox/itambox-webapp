"""Catalog seed mixin: tenant-agnostic reference data.

Designed to be mixed into ``Command`` in seed_data.py:

    from core.management.commands._seed.catalog import SeedCatalogMixin

    class Command(SeedCatalogMixin, BaseCommand):
        ...

``_seed_catalog`` runs first; it populates ``self._status_labels``,
``self._tags``, ``self._asset_roles``, ``self._manufacturers``,
``self._suppliers``, ``self._depreciations``, ``self._demo_depreciation_afa``,
``self._custom_fields``, the fieldset handles, ``self._categories``,
``self._asset_types``, ``self._components``, ``self._accessory_defs`` /
``self._consumable_defs`` (consumed later by the stock phase), ``self._software``
and ``self._providers``. It reads ``self._status_label_defs()`` from Command.
"""


class SeedCatalogMixin:
    """Mixin for Command(BaseCommand).  Reads/writes self._ registries."""

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

        # Categories — (slug, color). Every category ships a distinct colour so
        # the colour-chipped category cells (asset / asset-type lists, etc.)
        # always render a swatch instead of a blank.
        self._categories = {}
        category_defs = [
            ('laptops', '4263eb'), ('desktops', '1864ab'), ('servers', '5f3dc4'),
            ('monitors', '0c8599'), ('mobile-phones', '2b8a3e'), ('tablets', '37b24d'),
            ('network-devices', 'e8590c'), ('storage-devices', '9c36b5'),
            ('conference-systems', '1098ad'), ('charger', 'f59f00'), ('adaptor', 'f08c00'),
            ('mouse', '868e96'), ('keyboard', '495057'), ('webcam', '0ca678'),
            ('headset', '7048e8'), ('cable', 'adb5bd'), ('display', '15aabf'),
            ('dock', '3b5bdb'), ('toner', '343a40'), ('ink', '1c7ed6'),
            ('batteries', '66a80f'), ('thermal-paste', 'c2255c'), ('other', '6c757d'),
            ('ram-memory', 'e64980'), ('ssd-nvme', 'be4bdb'), ('hdd', '7950f2'),
            ('nic', 'f76707'), ('gpu', 'e03131'), ('cpu', 'd6336c'),
        ]
        applies = {'asset': True, 'accessory': True, 'consumable': True, 'component': True}
        for slug, color in category_defs:
            obj, created = Category.objects.get_or_create(slug=slug, defaults={
                'name': slug.replace('-', ' ').title(), 'applies_to': applies, 'color': color})
            if not created and not obj.color:
                # Backfill a category seeded before colours were assigned.
                obj.color = color
                obj.save(update_fields=['color'])
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
