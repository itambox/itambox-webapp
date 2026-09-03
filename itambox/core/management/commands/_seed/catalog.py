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

from decimal import Decimal

from django.utils import timezone

from assets.models import AssetTypeFieldset, CategoryDefaultFieldset
from extras.models import CustomField, CustomFieldChoice, CustomFieldChoiceSet, CustomFieldset, CustomFieldsetField, Tag


def _get_core_choice_set(slug, label):
    matches = list(CustomFieldChoiceSet.all_objects.filter(slug=slug))
    if len(matches) > 1:
        raise ValueError(f"Ambiguous core Choice Set identity: itambox/{slug}")
    if matches and matches[0].deleted_at is not None:
        raise ValueError(f"Core Choice Set identity is reserved by a tombstone: itambox/{slug}")
    if matches and (
        matches[0].namespace != "itambox"
        or matches[0].management_kind != CustomFieldChoiceSet.MANAGEMENT_CORE
        or matches[0].lifecycle != CustomFieldChoiceSet.LIFECYCLE_ACTIVE
    ):
        raise ValueError(f"Core Choice Set identity has incompatible management or lifecycle: itambox/{slug}")
    if matches:
        return matches[0]
    return CustomFieldChoiceSet.objects.create(
        namespace="itambox",
        slug=slug,
        label=label,
        management_kind=CustomFieldChoiceSet.MANAGEMENT_CORE,
        version=1,
        lifecycle=CustomFieldChoiceSet.LIFECYCLE_ACTIVE,
    )


def _validate_core_choice_row(choice, slug, desired_keys):
    if choice.key not in desired_keys:
        raise ValueError(f"Core Choice identity is unexpected: itambox/{slug}#{choice.key}")
    if (
        choice.deleted_at is not None
        or choice.management_kind != CustomFieldChoice.MANAGEMENT_CORE
        or choice.lifecycle != CustomFieldChoice.LIFECYCLE_ACTIVE
    ):
        raise ValueError(f"Core Choice identity has incompatible management or lifecycle: itambox/{slug}#{choice.key}")


def _reconcile_core_choice_rows(choice_set, slug, choices):
    existing_choices = list(CustomFieldChoice.all_objects.filter(choice_set=choice_set).order_by("position", "key"))
    if len(existing_choices) > 64:
        raise ValueError(f"Core Choice Set has more than 64 choices: itambox/{slug}")
    desired_keys = {key for key, _choice_label in choices}
    for choice in existing_choices:
        _validate_core_choice_row(choice, slug, desired_keys)
    for rank, choice in enumerate(existing_choices, start=1):
        CustomFieldChoice.all_objects.filter(pk=choice.pk).update(position=1000000000 + rank)
    existing_by_key = {choice.key: choice for choice in existing_choices}
    desired_keys = set()
    for index, (key, choice_label) in enumerate(choices, start=1):
        desired_keys.add(key)
        choice = existing_by_key.get(key)
        if choice is not None and (
            choice.deleted_at is not None
            or choice.management_kind != CustomFieldChoice.MANAGEMENT_CORE
            or choice.lifecycle != CustomFieldChoice.LIFECYCLE_ACTIVE
        ):
            raise ValueError(f"Core Choice identity has incompatible management or lifecycle: itambox/{slug}#{key}")
        if choice is None:
            CustomFieldChoice.objects.create(
                choice_set=choice_set,
                key=key,
                label=choice_label,
                position=index * 10,
                management_kind=CustomFieldChoice.MANAGEMENT_CORE,
                version=1,
                lifecycle=CustomFieldChoice.LIFECYCLE_ACTIVE,
            )
            continue
        choice.label = choice_label
        choice.position = index * 10
        choice.management_kind = CustomFieldChoice.MANAGEMENT_CORE
        choice.version = 1
        choice.lifecycle = CustomFieldChoice.LIFECYCLE_ACTIVE
        choice.save(update_fields=["label", "position", "management_kind", "version", "lifecycle"])
    for choice in existing_choices:
        if choice.key not in desired_keys and choice.deleted_at is None:
            choice.lifecycle = CustomFieldChoice.LIFECYCLE_DELETED
            choice.deleted_at = timezone.now()
            choice.save(update_fields=["lifecycle", "deleted_at"])


def _reconcile_core_choice_set(slug, label, choices):
    choice_set = _get_core_choice_set(slug, label)
    choice_set.label = label
    choice_set.management_kind = CustomFieldChoiceSet.MANAGEMENT_CORE
    choice_set.version = 1
    choice_set.lifecycle = CustomFieldChoiceSet.LIFECYCLE_ACTIVE
    choice_set.save(update_fields=["label", "management_kind", "version", "lifecycle"])
    _reconcile_core_choice_rows(choice_set, slug, choices)
    return choice_set


def _reconcile_core_choice_sets(choice_set_data):
    return {
        slug: _reconcile_core_choice_set(slug, label, choices) for slug, (label, choices) in choice_set_data.items()
    }


def _validate_core_field_identity(matches, key):
    if len(matches) > 1:
        raise ValueError(f"Ambiguous core field identity: {key}")
    if matches and matches[0].deleted_at is not None:
        raise ValueError(f"Core field identity is reserved by a tombstone: {key}")
    if matches and (
        matches[0].namespace != "itambox"
        or matches[0].management_kind != CustomField.MANAGEMENT_CORE
        or matches[0].lifecycle != CustomField.LIFECYCLE_ACTIVE
    ):
        raise ValueError(f"Core field identity has incompatible management or lifecycle: {key}")


def _reconcile_core_fields(field_rows, choice_sets, asset_ct, assettype_ct):
    custom_fields = {}
    for row in field_rows:
        options = {
            key: value
            for key, value in row.items()
            if key
            in {
                "scope",
                "field_type",
                "quantity_kind",
                "canonical_unit",
                "minimum_value",
                "maximum_value",
                "regex",
                "decimal_scale",
                "max_values",
                "text_max_length",
                "validation_rule",
            }
        }
        options.update(
            {
                "namespace": "itambox",
                "label": row["label"],
                "help_text": row["label"],
                "required": False,
                "nullable": False,
                "management_kind": "core",
                "version": 1,
                "lifecycle": "active",
                "mappings": [],
                "choice_set": choice_sets.get(row["choice_set"]),
            }
        )
        for key in ("minimum_value", "maximum_value"):
            if options[key] is not None:
                options[key] = Decimal(options[key])
        matches = list(CustomField.all_objects.filter(name=row["key"]))
        _validate_core_field_identity(matches, row["key"])
        field = matches[0] if matches else CustomField.objects.create(name=row["key"], **options)
        immutable_fields = (
            "name",
            "namespace",
            "field_type",
            "scope",
            "quantity_kind",
            "canonical_unit",
            "minimum_value",
            "maximum_value",
            "regex",
            "decimal_scale",
            "max_values",
            "text_max_length",
            "validation_rule",
            "nullable",
            "choice_set_id",
        )
        if matches:
            for key in immutable_fields:
                desired = (
                    row["key"]
                    if key == "name"
                    else (
                        options["choice_set"].pk
                        if key == "choice_set_id" and options["choice_set"]
                        else options.get(key)
                    )
                )
                current = field.choice_set_id if key == "choice_set_id" else getattr(field, key)
                if current != desired:
                    raise ValueError(f"Core field semantics differ for identity: {row['key']}")
            for key in ("label", "help_text", "required", "mappings", "management_kind", "version", "lifecycle"):
                setattr(field, key, options[key])
            field.save(
                update_fields=["label", "help_text", "required", "mappings", "management_kind", "version", "lifecycle"]
            )
        target_content_types = {
            "asset": [asset_ct],
            "asset_type": [assettype_ct],
            "both": [asset_ct, assettype_ct],
        }[options["scope"]]
        field.object_types.set(target_content_types)
        custom_fields[row["key"]] = field
    return custom_fields


def _get_core_fieldset(slug, label):
    matches = list(CustomFieldset.all_objects.filter(slug=slug))
    if len(matches) > 1:
        raise ValueError(f"Ambiguous core fieldset identity: itambox/{slug}")
    if matches and matches[0].deleted_at is not None:
        raise ValueError(f"Core fieldset identity is reserved by a tombstone: itambox/{slug}")
    if matches and (
        matches[0].namespace != "itambox"
        or matches[0].management_kind != CustomFieldset.MANAGEMENT_CORE
        or matches[0].lifecycle != CustomFieldset.LIFECYCLE_ACTIVE
    ):
        raise ValueError(f"Core fieldset identity has incompatible management or lifecycle: itambox/{slug}")
    if matches:
        return matches[0]
    return CustomFieldset.objects.create(
        namespace="itambox",
        slug=slug,
        label=label,
        description=f"Normative {label.lower()} specification section.",
        management_kind=CustomFieldset.MANAGEMENT_CORE,
        version=1,
        lifecycle=CustomFieldset.LIFECYCLE_ACTIVE,
    )


def _reconcile_core_fieldsets(field_rows, fieldset_labels, custom_fields):
    fieldsets = {}
    for slug, label in fieldset_labels.items():
        fieldset = _get_core_fieldset(slug, label)
        fieldset.namespace = "itambox"
        fieldset.slug = slug
        fieldset.label = label
        fieldset.description = f"Normative {label.lower()} specification section."
        fieldset.management_kind = "core"
        fieldset.version = 1
        fieldset.lifecycle = "active"
        fieldset.save()
        expected_field_ids = {custom_fields[row["key"]].pk for row in field_rows if row["fieldset_slug"] == slug}
        existing_field_ids = set(
            CustomFieldsetField._base_manager.filter(fieldset=fieldset).values_list("custom_field_id", flat=True)
        )
        if existing_field_ids - expected_field_ids:
            raise ValueError(f"Core fieldset has an unexpected membership: itambox/{slug}")
        fieldset.field_memberships.all().delete()
        CustomFieldsetField.objects.bulk_create(
            [
                CustomFieldsetField(
                    fieldset=fieldset,
                    custom_field=custom_fields[row["key"]],
                    position=row["position"],
                )
                for row in field_rows
                if row["fieldset_slug"] == slug
            ]
        )
        fieldsets[slug] = fieldset
    return fieldsets


def _seed_core_category_defaults(category_fieldsets, categories, fieldsets):
    for category_slug, fieldset_slugs in category_fieldsets.items():
        category = categories[category_slug]
        category.default_fieldset_memberships.all().delete()
        CategoryDefaultFieldset.objects.bulk_create(
            [
                CategoryDefaultFieldset(category=category, fieldset=fieldsets[slug], position=index * 10)
                for index, slug in enumerate(fieldset_slugs, start=1)
            ]
        )


def _demo_operating_system_family(atype_slug):
    if "macbook" in atype_slug or "mac-studio" in atype_slug:
        return "macos"
    if "iphone" in atype_slug:
        return "ios"
    if "ipad" in atype_slug:
        return "ipados"
    if "galaxy" in atype_slug:
        return "android"
    if atype_slug == "synology-ds1823xs":
        return "embedded"
    if atype_slug in ("dell-poweredge-r760", "hpe-proliant-dl380-g11"):
        return "linux"
    if atype_slug in ("cisco-catalyst-9300", "unifi-switch-pro-48", "meraki-mr46", "unifi-dream-machine-pro"):
        return "network_os"
    return "windows"


def _translate_legacy_demo_specs(raw_specs):
    specs = {}
    if raw_specs.get("cpu"):
        specs["processor_model"] = raw_specs["cpu"]
    if "ram_gb" in raw_specs:
        specs["memory_capacity"] = f"{float(raw_specs['ram_gb']):.3f}"
    if "storage_gb" in raw_specs:
        specs["storage_capacity"] = f"{float(raw_specs['storage_gb']):.3f}"
    if "storage_type" in raw_specs:
        specs["storage_medium"] = {
            "NVMe": "nvme_ssd",
            "SSD": "ssd",
            "HDD": "hdd",
            "SSD RAID": "ssd",
            "SATA SSD": "ssd",
        }.get(raw_specs["storage_type"], "other")
    if "screen_size" in raw_specs:
        specs["display_size"] = f"{float(raw_specs['screen_size']):.2f}"
    if "port_count" in raw_specs:
        specs["ethernet_port_count"] = int(raw_specs["port_count"])
        specs["poe_port_count"] = int(raw_specs["port_count"])
    if "poe_budget_w" in raw_specs:
        specs["poe_budget"] = f"{float(raw_specs['poe_budget_w']):.3f}"
    return specs


def _add_default_demo_specs(specs, atype_slug, category_slug):
    specs.setdefault(
        "form_factor",
        {
            "laptops": "notebook",
            "desktops": "desktop",
            "servers": "rack",
            "storage-devices": "appliance",
            "mobile-phones": "phone",
            "tablets": "tablet",
            "network-devices": "appliance",
            "monitors": "peripheral",
            "conference-systems": "appliance",
        }.get(category_slug, "other"),
    )
    if category_slug != "monitors":
        specs.setdefault("operating_system_family", _demo_operating_system_family(atype_slug))
    if category_slug in {"laptops", "desktops", "servers", "storage-devices"}:
        specs.setdefault("memory_type", "ddr5")
        specs.setdefault("ethernet_port_count", 1)
        specs.setdefault("ethernet_speeds", ["1g"])
        specs.setdefault("usb_port_count", 4)
    elif category_slug in {"mobile-phones", "tablets"}:
        specs.setdefault("memory_type", "lpddr5")
        specs.setdefault("ethernet_port_count", 0)
        specs.setdefault("ethernet_speeds", [])
        specs.setdefault("usb_port_count", 1)
    elif category_slug == "network-devices":
        specs.setdefault("ethernet_speeds", ["10g", "1g"])
        specs.setdefault("usb_port_count", 1)
        specs.setdefault(
            "network_functions",
            {
                "meraki-mr46": ["wlan_ap"],
                "unifi-dream-machine-pro": ["firewall", "router"],
            }.get(atype_slug, ["switch"] if "switch" in atype_slug else ["gateway"]),
        )
    if "wifi_standards" not in specs and category_slug in {"laptops", "desktops", "mobile-phones", "tablets"}:
        specs["wifi_standards"] = ["802_11ac", "802_11ax"]
    if "management_protocols" not in specs and category_slug in {
        "laptops",
        "desktops",
        "servers",
        "storage-devices",
        "network-devices",
    }:
        specs["management_protocols"] = sorted(["https", "ssh"])
    return specs


def _canonical_demo_specs(raw_specs, atype_slug, category_slug):
    specs = _translate_legacy_demo_specs(raw_specs)
    return _add_default_demo_specs(specs, atype_slug, category_slug)


class SeedCatalogMixin:
    """Mixin for Command(BaseCommand).  Reads/writes self._ registries."""

    def _seed_catalog(self):
        from assets.models import AssetRole, AssetType, Category, Depreciation, Manufacturer, StatusLabel, Supplier
        from inventory.models import Accessory, Component, Consumable
        from software.models import Software
        from subscriptions.models import Provider

        self.stdout.write("--- Catalog: reference data ---")

        # Status labels
        self._status_labels = {}
        for name, slug, stype, color in self._status_label_defs():
            obj, _ = StatusLabel.objects.get_or_create(
                slug=slug, defaults={"name": name, "type": stype, "color": color}
            )
            self._status_labels[slug] = obj

        # Tags
        self._tags = {}
        for name, slug, color in [
            ("Production", "production", "28a745"),
            ("Development", "development", "007bff"),
            ("VIP", "vip", "dc3545"),
            ("GxP Validated", "gxp-validated", "198754"),
            ("PCI Scope", "pci-scope", "0b5ed7"),
            ("Finance", "finance", "198754"),
            ("Field", "field", "fd7e14"),
            ("Loaner", "loaner", "adb5bd"),
            ("Critical", "critical", "dc3545"),
            ("Legacy", "legacy", "6c757d"),
            ("Encrypted", "encrypted", "20c997"),
            ("MDM Enrolled", "mdm-enrolled", "6f42c1"),
        ]:
            obj, _ = Tag.objects.get_or_create(slug=slug, defaults={"name": name, "color": color})
            self._tags[slug] = obj

        # Asset roles — (name, slug, color, desc, allows_components)
        self._asset_roles = {}
        for name, slug, color, desc, allows_comp in [
            ("Standard Workstation", "standard-workstation", "007bff", "Laptop/desktop for general office staff", True),
            (
                "Developer Workstation",
                "developer-workstation",
                "6f42c1",
                "High-performance workstation for engineers",
                True,
            ),
            ("Executive Workstation", "executive-workstation", "e83e8c", "Premium device for executives", True),
            ("CAD/Design Workstation", "cad-design-workstation", "fd7e14", "GPU workstation for CAD/3D", True),
            (
                "Lab / Cleanroom Terminal",
                "lab-terminal",
                "adb5bd",
                "Restricted terminal for lab or production-floor use",
                False,
            ),
            ("Field Tablet", "field-tablet", "20c997", "Ruggedized tablet for field/warehouse work", False),
            ("Corporate Smartphone", "corporate-smartphone", "fd7e14", "Company smartphone for voice/chat/MFA", False),
            (
                "Virtualization Host",
                "virtualization-host-server",
                "dc3545",
                "Hypervisor host (ESXi/Proxmox/Hyper-V)",
                True,
            ),
            ("Database Server", "database-server", "17a2b8", "Production database host", True),
            ("Application Server", "application-server", "20c997", "Line-of-business application host", True),
            ("Backup / Storage", "backup-server", "fd7e14", "Backup target or NAS", True),
            ("Core Router / Firewall", "core-router-firewall", "dc3545", "Edge security gateway", False),
            ("Access / Distribution Switch", "access-switch", "0d6efd", "Network switch", False),
            ("Wireless Access Point", "wireless-ap", "20c997", "Enterprise WiFi access point", False),
            ("Conference Room AV", "conference-av", "e83e8c", "Meeting-room camera/audio hub", False),
            ("Desktop Monitor", "desktop-monitor", "6f42c1", "External display", False),
        ]:
            obj, _ = AssetRole.objects.get_or_create(
                slug=slug,
                defaults={"name": name, "color": color, "description": desc, "allows_components": allows_comp},
            )
            self._asset_roles[slug] = obj

        # Manufacturers
        self._manufacturers = {}
        for name, slug in [
            ("Dell Technologies", "dell-technologies"),
            ("Apple Inc.", "apple-inc"),
            ("HP Inc.", "hp-inc"),
            ("Lenovo Group", "lenovo-group"),
            ("Cisco Systems", "cisco-systems"),
            ("Samsung Electronics", "samsung-electronics"),
            ("Microsoft Corporation", "microsoft-corporation"),
            ("Logitech International", "logitech-international"),
            ("Brother Industries", "brother-industries"),
            ("Synology Inc.", "synology-inc"),
            ("Ubiquiti Inc.", "ubiquiti-inc"),
        ]:
            obj, _ = Manufacturer.objects.get_or_create(slug=slug, defaults={"name": name})
            self._manufacturers[slug] = obj

        # Suppliers
        from django.contrib.contenttypes.models import ContentType as CT

        from organization.models import Contact, ContactAssignment, ContactRole

        supplier_ct = CT.objects.get_for_model(Supplier)
        primary_role, _ = ContactRole.objects.get_or_create(
            slug="primary-contact",
            defaults={"name": "Primary Contact", "description": "Primary Contact"},
        )
        self._suppliers = {}
        for name, slug, email, phone, website in [
            (
                "Northwind Procurement",
                "northwind-procurement",
                "buy@northwind-it.com",
                "+49-30-555-0100",
                "https://northwind-it.com",
            ),
            ("Dell Direct", "dell-direct", "enterprise@dell.com", "+1-800-555-0199", "https://dell.com"),
            ("Apple Business", "apple-business", "business@apple.com", "+1-800-555-0200", "https://apple.com/business"),
            ("CDW Deutschland", "cdw-deutschland", "de.sales@cdw.com", "+49-211-555-0500", "https://cdw.de"),
            ("Bechtle AG", "bechtle-ag", "b2b@bechtle.com", "+49-7132-555-0700", "https://bechtle.com"),
            ("Insight Enterprises", "insight-enterprises", "eu@insight.com", "+44-20-555-0800", "https://insight.com"),
        ]:
            obj, created = Supplier.objects.get_or_create(slug=slug, defaults={"name": name, "website": website})
            if created and not obj.contacts.filter(priority="primary").exists():
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
                    priority="primary",
                )
            self._suppliers[slug] = obj

        # Depreciation schedules — generic named first (used by asset types)
        self._depreciations = {}
        for name, months in [
            ("3-Year Straight-Line", 36),
            ("4-Year Straight-Line", 48),
            ("5-Year Straight-Line", 60),
            ("7-Year Straight-Line", 84),
        ]:
            obj, _ = Depreciation.objects.get_or_create(name=name, defaults={"months": months})
            self._depreciations[name] = obj

        # German AfA / GWG example policies for the demo tenant (opt-in via seed_data;
        # migration 0043 also seeds these on every install — see FIX-05 note).
        _afa_policies = [
            {
                "name": "IT-Hardware 36 Monate (AfA)",
                "months": 36,
                "method": "straight_line",
                "convention": "include_purchase_month",
                "description": "AfA-Tabelle 2021: Computer, Notebooks, Tablets (3 Jahre)",
            },
            {
                "name": "Server 60 Monate (AfA)",
                "months": 60,
                "method": "straight_line",
                "convention": "include_purchase_month",
                "description": "AfA-Tabelle 2021: Server und Workstations (5 Jahre)",
            },
            {
                "name": "Sofortabschreibung GWG (≤ 800 €)",
                "months": 1,
                "method": "straight_line",
                "convention": "include_purchase_month",
                "immediate_expense_threshold": "800.00",
                "description": "Geringwertige Wirtschaftsgüter nach § 6 Abs. 2 EStG: Sofortabschreibung bis 800 €",
            },
        ]
        self._demo_depreciation_afa = None
        for p in _afa_policies:
            obj, _ = Depreciation.objects.get_or_create(
                name=p["name"],
                defaults={k: v for k, v in p.items() if k != "name"},
            )
            if self._demo_depreciation_afa is None:
                self._demo_depreciation_afa = obj  # first entry = tenant default showcase

        # Normative core vocabulary.  These are global, managed definitions: the
        # demo seed must exercise the same identities, scopes, types, choice sets,
        # canonical units, and validation metadata that the final #479 contract uses.
        from django.contrib.contenttypes.models import ContentType

        from assets.models import Asset as AssetModel
        from assets.models import AssetType as AssetTypeModel

        asset_ct = ContentType.objects.get_for_model(AssetModel)
        assettype_ct = ContentType.objects.get_for_model(AssetTypeModel)

        choice_set_data = {
            "form-factor": (
                "Form factor",
                [
                    ("desktop", "Desktop"),
                    ("notebook", "Notebook"),
                    ("tablet", "Tablet"),
                    ("phone", "Phone"),
                    ("rack", "Rack"),
                    ("tower", "Tower"),
                    ("blade", "Blade"),
                    ("appliance", "Appliance"),
                    ("peripheral", "Peripheral"),
                    ("module", "Module"),
                    ("other", "Other"),
                ],
            ),
            "memory-type": (
                "Memory type",
                [
                    (key, label)
                    for key, label in [
                        ("ddr3", "DDR3"),
                        ("ddr4", "DDR4"),
                        ("ddr5", "DDR5"),
                        ("lpddr4x", "LPDDR4X"),
                        ("lpddr5", "LPDDR5"),
                        ("lpddr5x", "LPDDR5X"),
                        ("hbm2", "HBM2"),
                        ("hbm3", "HBM3"),
                        ("other", "Other"),
                    ]
                ],
            ),
            "storage-medium": (
                "Storage medium",
                [
                    (key, label)
                    for key, label in [
                        ("hdd", "Hard disk drive"),
                        ("ssd", "Solid-state drive"),
                        ("nvme_ssd", "NVMe solid-state drive"),
                        ("flash", "Flash"),
                        ("optical", "Optical"),
                        ("tape", "Tape"),
                        ("hybrid", "Hybrid"),
                        ("other", "Other"),
                    ]
                ],
            ),
            "ethernet-speeds": (
                "Ethernet speeds",
                [
                    (key, label)
                    for key, label in [
                        ("10m", "10 Mbit/s"),
                        ("100m", "100 Mbit/s"),
                        ("1g", "1 Gbit/s"),
                        ("2_5g", "2.5 Gbit/s"),
                        ("5g", "5 Gbit/s"),
                        ("10g", "10 Gbit/s"),
                        ("25g", "25 Gbit/s"),
                        ("40g", "40 Gbit/s"),
                        ("50g", "50 Gbit/s"),
                        ("100g", "100 Gbit/s"),
                        ("200g", "200 Gbit/s"),
                        ("400g", "400 Gbit/s"),
                    ]
                ],
            ),
            "wifi-standards": (
                "Wi-Fi standards",
                [
                    (key, label)
                    for key, label in [
                        ("802_11a", "802.11a"),
                        ("802_11b", "802.11b"),
                        ("802_11g", "802.11g"),
                        ("802_11n", "802.11n"),
                        ("802_11ac", "802.11ac"),
                        ("802_11ax", "802.11ax"),
                        ("802_11be", "802.11be"),
                    ]
                ],
            ),
            "network-functions": (
                "Network functions",
                [
                    (key, label)
                    for key, label in [
                        ("switch", "Switch"),
                        ("router", "Router"),
                        ("firewall", "Firewall"),
                        ("wlan_ap", "WLAN access point"),
                        ("wlan_controller", "WLAN controller"),
                        ("load_balancer", "Load balancer"),
                        ("vpn_gateway", "VPN gateway"),
                        ("modem", "Modem"),
                        ("gateway", "Gateway"),
                        ("bridge", "Bridge"),
                    ]
                ],
            ),
            "print-technology": (
                "Print technology",
                [
                    (key, label)
                    for key, label in [
                        ("laser", "Laser"),
                        ("inkjet", "Inkjet"),
                        ("thermal", "Thermal"),
                        ("dot_matrix", "Dot matrix"),
                        ("dye_sublimation", "Dye sublimation"),
                        ("solid_ink", "Solid ink"),
                        ("other", "Other"),
                    ]
                ],
            ),
            "operating-system-family": (
                "Operating system family",
                [
                    (key, label)
                    for key, label in [
                        ("windows", "Windows"),
                        ("windows_server", "Windows Server"),
                        ("macos", "macOS"),
                        ("linux", "Linux"),
                        ("chromeos", "ChromeOS"),
                        ("ios", "iOS"),
                        ("ipados", "iPadOS"),
                        ("android", "Android"),
                        ("bsd", "BSD"),
                        ("network_os", "Network OS"),
                        ("embedded", "Embedded"),
                        ("other", "Other"),
                    ]
                ],
            ),
            "management-protocols": (
                "Management protocols",
                [
                    (key, label)
                    for key, label in [
                        ("https", "HTTPS"),
                        ("ssh", "SSH"),
                        ("snmpv1", "SNMPv1"),
                        ("snmpv2c", "SNMPv2c"),
                        ("snmpv3", "SNMPv3"),
                        ("redfish", "Redfish"),
                        ("ipmi", "IPMI"),
                        ("wsman", "WS-Man"),
                        ("rest", "REST"),
                        ("graphql", "GraphQL"),
                        ("other", "Other"),
                    ]
                ],
            ),
            "sensor-types": (
                "Sensor types",
                [
                    (key, label)
                    for key, label in [
                        ("temperature", "Temperature"),
                        ("humidity", "Humidity"),
                        ("pressure", "Pressure"),
                        ("light", "Light"),
                        ("motion", "Motion"),
                        ("vibration", "Vibration"),
                        ("current", "Current"),
                        ("voltage", "Voltage"),
                        ("power", "Power"),
                        ("energy", "Energy"),
                        ("flow", "Flow"),
                        ("level", "Level"),
                        ("gas", "Gas"),
                        ("smoke", "Smoke"),
                        ("position", "Position"),
                        ("other", "Other"),
                    ]
                ],
            ),
            "industrial-protocols": (
                "Industrial protocols",
                [
                    (key, label)
                    for key, label in [
                        ("modbus_rtu", "Modbus RTU"),
                        ("modbus_tcp", "Modbus TCP"),
                        ("profinet", "PROFINET"),
                        ("profibus", "PROFIBUS"),
                        ("ethernet_ip", "EtherNet/IP"),
                        ("ethercat", "EtherCAT"),
                        ("bacnet_ip", "BACnet/IP"),
                        ("opc_ua", "OPC UA"),
                        ("canopen", "CANopen"),
                        ("other", "Other"),
                    ]
                ],
            ),
            "regulatory-certifications": (
                "Regulatory certifications",
                [
                    (key, label)
                    for key, label in [
                        ("ce", "CE"),
                        ("fcc", "FCC"),
                        ("ul", "UL"),
                        ("csa", "CSA"),
                        ("ukca", "UKCA"),
                        ("vcci", "VCCI"),
                        ("rcm", "RCM"),
                        ("eac", "EAC"),
                        ("other", "Other"),
                    ]
                ],
            ),
            "energy-certifications": (
                "Energy certifications",
                [
                    (key, label)
                    for key, label in [
                        ("energy_star", "ENERGY STAR"),
                        ("epeat_bronze", "EPEAT Bronze"),
                        ("epeat_silver", "EPEAT Silver"),
                        ("epeat_gold", "EPEAT Gold"),
                        ("80plus", "80 PLUS"),
                        ("80plus_bronze", "80 PLUS Bronze"),
                        ("80plus_silver", "80 PLUS Silver"),
                        ("80plus_gold", "80 PLUS Gold"),
                        ("80plus_platinum", "80 PLUS Platinum"),
                        ("80plus_titanium", "80 PLUS Titanium"),
                        ("other", "Other"),
                    ]
                ],
            ),
        }
        self._choice_sets = _reconcile_core_choice_sets(choice_set_data)

        def field_definition(
            key,
            label,
            scope,
            field_type,
            fieldset_slug,
            position,
            *,
            quantity_kind=None,
            canonical_unit=None,
            minimum_value=None,
            maximum_value=None,
            regex=None,
            decimal_scale=None,
            max_values=None,
            text_max_length=None,
            choice_set=None,
            validation_rule=None,
        ):
            return {
                "key": key,
                "label": label,
                "scope": scope,
                "field_type": field_type,
                "fieldset_slug": fieldset_slug,
                "position": position,
                "quantity_kind": quantity_kind,
                "canonical_unit": canonical_unit,
                "minimum_value": minimum_value,
                "maximum_value": maximum_value,
                "regex": regex,
                "decimal_scale": decimal_scale,
                "max_values": max_values,
                "text_max_length": text_max_length,
                "choice_set": choice_set,
                "validation_rule": validation_rule,
            }

        field_rows = [
            field_definition(
                "form_factor",
                "Form factor",
                "asset_type",
                "single-select",
                "product-physical",
                10,
                choice_set="form-factor",
                max_values=1,
            ),
            field_definition(
                "rack_units",
                "Rack units",
                "asset_type",
                "decimal",
                "product-physical",
                20,
                quantity_kind="length",
                canonical_unit="U",
                minimum_value="0",
                maximum_value="100",
                decimal_scale=1,
            ),
            field_definition(
                "weight",
                "Weight",
                "asset_type",
                "decimal",
                "product-physical",
                30,
                quantity_kind="mass",
                canonical_unit="kg",
                minimum_value="0",
                maximum_value="100000",
                decimal_scale=3,
            ),
            field_definition(
                "ip_rating",
                "IP rating",
                "asset_type",
                "text",
                "product-physical",
                40,
                regex=r"^IP[0-6X][0-9X]K?$",
                text_max_length=8,
            ),
            field_definition(
                "processor_model", "Processor model", "both", "text", "compute-memory", 10, text_max_length=255
            ),
            field_definition(
                "core_count",
                "Core count",
                "both",
                "integer",
                "compute-memory",
                20,
                quantity_kind="count",
                minimum_value="0",
                maximum_value="1048576",
            ),
            field_definition(
                "memory_capacity",
                "Memory capacity",
                "both",
                "decimal",
                "compute-memory",
                30,
                quantity_kind="digital_information",
                canonical_unit="GiB",
                minimum_value="0",
                maximum_value="1048576",
                decimal_scale=3,
            ),
            field_definition(
                "memory_type",
                "Memory type",
                "both",
                "single-select",
                "compute-memory",
                40,
                choice_set="memory-type",
                max_values=1,
            ),
            field_definition(
                "storage_capacity",
                "Storage capacity",
                "both",
                "decimal",
                "storage",
                10,
                quantity_kind="digital_information",
                canonical_unit="GiB",
                minimum_value="0",
                maximum_value="1073741824",
                decimal_scale=3,
            ),
            field_definition(
                "storage_medium",
                "Storage medium",
                "both",
                "single-select",
                "storage",
                20,
                choice_set="storage-medium",
                max_values=1,
            ),
            field_definition(
                "drive_bay_count",
                "Drive bay count",
                "asset_type",
                "integer",
                "storage",
                30,
                quantity_kind="count",
                minimum_value="0",
                maximum_value="65535",
            ),
            field_definition("hot_swap_supported", "Hot-swap storage", "asset_type", "boolean", "storage", 40),
            field_definition(
                "ethernet_port_count",
                "Ethernet port count",
                "asset_type",
                "integer",
                "connectivity-io",
                10,
                quantity_kind="count",
                minimum_value="0",
                maximum_value="65535",
            ),
            field_definition(
                "ethernet_speeds",
                "Ethernet speeds",
                "asset_type",
                "multi-select",
                "connectivity-io",
                20,
                choice_set="ethernet-speeds",
                max_values=16,
            ),
            field_definition(
                "wifi_standards",
                "Wi-Fi standards",
                "asset_type",
                "multi-select",
                "connectivity-io",
                30,
                choice_set="wifi-standards",
                max_values=8,
            ),
            field_definition(
                "usb_port_count",
                "USB port count",
                "asset_type",
                "integer",
                "connectivity-io",
                40,
                quantity_kind="count",
                minimum_value="0",
                maximum_value="1024",
            ),
            field_definition(
                "network_functions",
                "Network functions",
                "asset_type",
                "multi-select",
                "network-function",
                10,
                choice_set="network-functions",
                max_values=10,
            ),
            field_definition(
                "switching_capacity",
                "Switching capacity",
                "asset_type",
                "decimal",
                "network-function",
                20,
                quantity_kind="data_rate",
                canonical_unit="Gbit/s",
                minimum_value="0",
                maximum_value="1000000000",
                decimal_scale=3,
            ),
            field_definition(
                "poe_port_count",
                "PoE port count",
                "asset_type",
                "integer",
                "network-function",
                30,
                quantity_kind="count",
                minimum_value="0",
                maximum_value="65535",
            ),
            field_definition(
                "poe_budget",
                "PoE budget",
                "asset_type",
                "decimal",
                "network-function",
                40,
                quantity_kind="power",
                canonical_unit="W",
                minimum_value="0",
                maximum_value="10000000",
                decimal_scale=3,
            ),
            field_definition(
                "input_voltage",
                "Input voltage",
                "asset_type",
                "decimal",
                "power-battery",
                10,
                quantity_kind="voltage",
                canonical_unit="V",
                minimum_value="0",
                maximum_value="1000000",
                decimal_scale=3,
            ),
            field_definition(
                "power_consumption_max",
                "Maximum power consumption",
                "asset_type",
                "decimal",
                "power-battery",
                20,
                quantity_kind="power",
                canonical_unit="W",
                minimum_value="0",
                maximum_value="100000000",
                decimal_scale=3,
            ),
            field_definition(
                "battery_capacity",
                "Battery capacity",
                "both",
                "decimal",
                "power-battery",
                30,
                quantity_kind="energy",
                canonical_unit="Wh",
                minimum_value="0",
                maximum_value="10000000",
                decimal_scale=3,
            ),
            field_definition(
                "battery_runtime",
                "Battery runtime",
                "both",
                "integer",
                "power-battery",
                40,
                quantity_kind="duration",
                canonical_unit="min",
                minimum_value="0",
                maximum_value="5256000",
            ),
            field_definition(
                "display_size",
                "Display size",
                "asset_type",
                "decimal",
                "display-av-imaging",
                10,
                quantity_kind="length",
                canonical_unit="in",
                minimum_value="0",
                maximum_value="1000",
                decimal_scale=2,
            ),
            field_definition(
                "display_resolution",
                "Display resolution",
                "asset_type",
                "text",
                "display-av-imaging",
                20,
                regex=r"^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}$",
                text_max_length=11,
            ),
            field_definition("touch_supported", "Touch supported", "asset_type", "boolean", "display-av-imaging", 30),
            field_definition(
                "camera_resolution",
                "Camera resolution",
                "asset_type",
                "decimal",
                "display-av-imaging",
                40,
                quantity_kind="resolution",
                canonical_unit="MP",
                minimum_value="0",
                maximum_value="10000",
                decimal_scale=2,
            ),
            field_definition(
                "print_technology",
                "Print technology",
                "asset_type",
                "single-select",
                "print-scan",
                10,
                choice_set="print-technology",
                max_values=1,
            ),
            field_definition("color_supported", "Color printing", "asset_type", "boolean", "print-scan", 20),
            field_definition("duplex_supported", "Duplex printing", "asset_type", "boolean", "print-scan", 30),
            field_definition(
                "print_speed",
                "Print speed",
                "asset_type",
                "decimal",
                "print-scan",
                40,
                quantity_kind="rate",
                canonical_unit="pages_per_minute",
                minimum_value="0",
                maximum_value="100000",
                decimal_scale=2,
            ),
            field_definition(
                "operating_system_family",
                "Operating system family",
                "both",
                "single-select",
                "management-security",
                10,
                choice_set="operating-system-family",
                max_values=1,
            ),
            field_definition(
                "management_protocols",
                "Management protocols",
                "asset_type",
                "multi-select",
                "management-security",
                20,
                choice_set="management-protocols",
                max_values=16,
            ),
            field_definition(
                "hostname",
                "Hostname",
                "asset",
                "text",
                "management-security",
                30,
                text_max_length=253,
                validation_rule="rfc1123_hostname",
            ),
            field_definition(
                "firmware_version", "Firmware version", "asset", "text", "management-security", 40, text_max_length=255
            ),
            field_definition(
                "operating_temperature_min",
                "Minimum operating temperature",
                "asset_type",
                "decimal",
                "environmental-ruggedization",
                10,
                quantity_kind="temperature",
                canonical_unit="°C",
                minimum_value="-273.15",
                maximum_value="1000",
                decimal_scale=2,
            ),
            field_definition(
                "operating_temperature_max",
                "Maximum operating temperature",
                "asset_type",
                "decimal",
                "environmental-ruggedization",
                20,
                quantity_kind="temperature",
                canonical_unit="°C",
                minimum_value="-273.15",
                maximum_value="1000",
                decimal_scale=2,
                validation_rule="temperature_max_gte_min",
            ),
            field_definition(
                "outdoor_rated", "Outdoor rated", "asset_type", "boolean", "environmental-ruggedization", 30
            ),
            field_definition(
                "acoustic_level",
                "Acoustic level",
                "asset_type",
                "decimal",
                "environmental-ruggedization",
                40,
                quantity_kind="sound_pressure",
                canonical_unit="dBA",
                minimum_value="0",
                maximum_value="250",
                decimal_scale=2,
            ),
            field_definition(
                "sensor_types",
                "Sensor types",
                "asset_type",
                "multi-select",
                "sensors-control",
                10,
                choice_set="sensor-types",
                max_values=32,
            ),
            field_definition(
                "analog_input_count",
                "Analog input count",
                "asset_type",
                "integer",
                "sensors-control",
                20,
                quantity_kind="count",
                minimum_value="0",
                maximum_value="65535",
            ),
            field_definition(
                "digital_input_count",
                "Digital input count",
                "asset_type",
                "integer",
                "sensors-control",
                30,
                quantity_kind="count",
                minimum_value="0",
                maximum_value="65535",
            ),
            field_definition(
                "industrial_protocols",
                "Industrial protocols",
                "asset_type",
                "multi-select",
                "sensors-control",
                40,
                choice_set="industrial-protocols",
                max_values=16,
            ),
            field_definition(
                "regulatory_certifications",
                "Regulatory certifications",
                "asset_type",
                "multi-select",
                "compliance-sustainability",
                10,
                choice_set="regulatory-certifications",
                max_values=16,
            ),
            field_definition(
                "rohs_compliant", "RoHS compliant", "asset_type", "boolean", "compliance-sustainability", 20
            ),
            field_definition(
                "energy_certifications",
                "Energy certifications",
                "asset_type",
                "multi-select",
                "compliance-sustainability",
                30,
                choice_set="energy-certifications",
                max_values=16,
            ),
            field_definition(
                "country_of_origin",
                "Country of origin",
                "asset_type",
                "text",
                "compliance-sustainability",
                40,
                regex=r"^[A-Z]{2}$",
                text_max_length=2,
            ),
        ]

        if len(field_rows) != 48:
            raise ValueError("The normative core vocabulary must contain exactly 48 fields.")
        self._custom_fields = _reconcile_core_fields(field_rows, self._choice_sets, asset_ct, assettype_ct)

        fieldset_labels = {
            "product-physical": "Product and Physical",
            "compute-memory": "Compute and Memory",
            "storage": "Storage",
            "connectivity-io": "Connectivity and I/O",
            "network-function": "Network Function",
            "power-battery": "Power and Battery",
            "display-av-imaging": "Display, AV and Imaging",
            "print-scan": "Print and Scan",
            "management-security": "Management and Security",
            "environmental-ruggedization": "Environmental and Ruggedization",
            "sensors-control": "Sensors and Control",
            "compliance-sustainability": "Compliance and Sustainability",
        }
        self._fieldsets = _reconcile_core_fieldsets(field_rows, fieldset_labels, self._custom_fields)

        # Keep these handles for the existing asset-type data table while the
        # actual composition below is driven only by the plural through model.
        self._fs_laptop = self._fieldsets["compute-memory"]
        self._fs_mobile = self._fieldsets["compute-memory"]
        self._fs_server = self._fieldsets["compute-memory"]
        self._fs_switch = self._fieldsets["network-function"]
        self._fs_av = self._fieldsets["display-av-imaging"]

        self._category_fieldsets = {
            "laptops": [
                "product-physical",
                "compute-memory",
                "storage",
                "connectivity-io",
                "display-av-imaging",
                "management-security",
                "compliance-sustainability",
            ],
            "desktops": [
                "product-physical",
                "compute-memory",
                "storage",
                "connectivity-io",
                "management-security",
                "compliance-sustainability",
            ],
            "servers": [
                "product-physical",
                "compute-memory",
                "storage",
                "connectivity-io",
                "network-function",
                "power-battery",
                "management-security",
                "compliance-sustainability",
            ],
            "storage-devices": [
                "product-physical",
                "compute-memory",
                "storage",
                "connectivity-io",
                "power-battery",
                "management-security",
                "compliance-sustainability",
            ],
            "mobile-phones": [
                "product-physical",
                "compute-memory",
                "storage",
                "connectivity-io",
                "power-battery",
                "display-av-imaging",
                "management-security",
                "compliance-sustainability",
            ],
            "tablets": [
                "product-physical",
                "compute-memory",
                "storage",
                "connectivity-io",
                "power-battery",
                "display-av-imaging",
                "management-security",
                "compliance-sustainability",
            ],
            "network-devices": [
                "product-physical",
                "connectivity-io",
                "network-function",
                "power-battery",
                "management-security",
                "environmental-ruggedization",
                "compliance-sustainability",
            ],
            "monitors": [
                "product-physical",
                "connectivity-io",
                "power-battery",
                "display-av-imaging",
                "compliance-sustainability",
            ],
            "conference-systems": [
                "product-physical",
                "connectivity-io",
                "power-battery",
                "display-av-imaging",
                "management-security",
                "compliance-sustainability",
            ],
        }
        # Categories — (slug, color). Every category ships a distinct colour so
        # the colour-chipped category cells (asset / asset-type lists, etc.)
        # always render a swatch instead of a blank.
        self._categories = {}
        category_defs = [
            ("laptops", "4263eb"),
            ("desktops", "1864ab"),
            ("servers", "5f3dc4"),
            ("monitors", "0c8599"),
            ("mobile-phones", "2b8a3e"),
            ("tablets", "37b24d"),
            ("network-devices", "e8590c"),
            ("storage-devices", "9c36b5"),
            ("conference-systems", "1098ad"),
            ("charger", "f59f00"),
            ("adaptor", "f08c00"),
            ("mouse", "868e96"),
            ("keyboard", "495057"),
            ("webcam", "0ca678"),
            ("headset", "7048e8"),
            ("cable", "adb5bd"),
            ("display", "15aabf"),
            ("dock", "3b5bdb"),
            ("toner", "343a40"),
            ("ink", "1c7ed6"),
            ("batteries", "66a80f"),
            ("thermal-paste", "c2255c"),
            ("other", "6c757d"),
            ("ram-memory", "e64980"),
            ("ssd-nvme", "be4bdb"),
            ("hdd", "7950f2"),
            ("nic", "f76707"),
            ("gpu", "e03131"),
            ("cpu", "d6336c"),
        ]
        applies = {"asset": True, "accessory": True, "consumable": True, "component": True}
        for slug, color in category_defs:
            obj, created = Category.objects.get_or_create(
                slug=slug, defaults={"name": slug.replace("-", " ").title(), "applies_to": applies, "color": color}
            )
            if not created and not obj.color:
                # Backfill a category seeded before colours were assigned.
                obj.color = color
                obj.save(update_fields=["color"])
            self._categories[slug] = obj

        _seed_core_category_defaults(self._category_fieldsets, self._categories, self._fieldsets)

        # Asset types: (model, slug, mfr, part_number, eol_months, fieldset, depreciation, category, role, specs)
        at_data = [
            (
                "Latitude 5550",
                "dell-latitude-5550",
                "dell-technologies",
                "LAT5550-2025",
                36,
                self._fs_laptop,
                "3-Year Straight-Line",
                "laptops",
                "standard-workstation",
                {
                    "cpu": "Intel Core i7-1365U",
                    "ram_gb": 16,
                    "storage_gb": 512,
                    "storage_type": "NVMe",
                    "cpu_architecture": "x86_64",
                },
            ),
            (
                "EliteBook 860 G11",
                "hp-elitebook-860-g11",
                "hp-inc",
                "866S7EA",
                36,
                self._fs_laptop,
                "3-Year Straight-Line",
                "laptops",
                "standard-workstation",
                {
                    "cpu": "Intel Core i7-1370P",
                    "ram_gb": 32,
                    "storage_gb": 1024,
                    "storage_type": "NVMe",
                    "cpu_architecture": "x86_64",
                },
            ),
            (
                "ThinkPad X1 Carbon Gen 12",
                "thinkpad-x1-carbon-g12",
                "lenovo-group",
                "21KC004PGE",
                36,
                self._fs_laptop,
                "3-Year Straight-Line",
                "laptops",
                "developer-workstation",
                {
                    "cpu": "Intel Core i7-1365U",
                    "ram_gb": 32,
                    "storage_gb": 1024,
                    "storage_type": "NVMe",
                    "cpu_architecture": "x86_64",
                },
            ),
            (
                'MacBook Pro 16"',
                "macbook-pro-16-2024",
                "apple-inc",
                "MBP16-M4",
                36,
                self._fs_laptop,
                "3-Year Straight-Line",
                "laptops",
                "developer-workstation",
                {
                    "cpu": "Apple M4 Pro",
                    "ram_gb": 36,
                    "storage_gb": 1024,
                    "storage_type": "NVMe",
                    "cpu_architecture": "ARM64",
                },
            ),
            (
                'MacBook Air 15"',
                "macbook-air-15-2024",
                "apple-inc",
                "MBA15-M3",
                36,
                self._fs_laptop,
                "3-Year Straight-Line",
                "laptops",
                "standard-workstation",
                {
                    "cpu": "Apple M3",
                    "ram_gb": 16,
                    "storage_gb": 512,
                    "storage_type": "NVMe",
                    "cpu_architecture": "ARM64",
                },
            ),
            (
                "Precision 5680",
                "dell-precision-5680",
                "dell-technologies",
                "PREC5680-WS",
                48,
                self._fs_laptop,
                "4-Year Straight-Line",
                "laptops",
                "developer-workstation",
                {
                    "cpu": "Intel Core i9-13900H",
                    "ram_gb": 64,
                    "storage_gb": 2048,
                    "storage_type": "NVMe",
                    "gpu": "NVIDIA RTX 3000 Ada",
                    "cpu_architecture": "x86_64",
                },
            ),
            (
                "OptiPlex 7010 SFF",
                "dell-optiplex-7010",
                "dell-technologies",
                "OPT7010-SFF",
                48,
                self._fs_laptop,
                "4-Year Straight-Line",
                "desktops",
                "standard-workstation",
                {
                    "cpu": "Intel Core i5-13500",
                    "ram_gb": 16,
                    "storage_gb": 512,
                    "storage_type": "NVMe",
                    "cpu_architecture": "x86_64",
                },
            ),
            (
                "Mac Studio",
                "mac-studio-2024",
                "apple-inc",
                "MSTUDIO-M2U",
                60,
                self._fs_laptop,
                "5-Year Straight-Line",
                "desktops",
                "cad-design-workstation",
                {
                    "cpu": "Apple M2 Ultra",
                    "ram_gb": 64,
                    "storage_gb": 1024,
                    "storage_type": "NVMe",
                    "cpu_architecture": "ARM64",
                },
            ),
            (
                "Precision 7960 Tower",
                "dell-precision-7960-tower",
                "dell-technologies",
                "PREC7960-TWR",
                60,
                self._fs_laptop,
                "5-Year Straight-Line",
                "desktops",
                "cad-design-workstation",
                {
                    "cpu": "Intel Xeon w7-3465X",
                    "ram_gb": 128,
                    "storage_gb": 4096,
                    "storage_type": "SSD RAID",
                    "gpu": "NVIDIA RTX 6000 Ada",
                    "cpu_architecture": "x86_64",
                },
            ),
            (
                "PowerEdge R760",
                "dell-poweredge-r760",
                "dell-technologies",
                "R760-XEON",
                60,
                self._fs_server,
                "5-Year Straight-Line",
                "servers",
                "virtualization-host-server",
                {"cpu": "2x Intel Xeon Gold 6430", "ram_gb": 256, "storage_gb": 8000, "storage_type": "SSD RAID"},
            ),
            (
                "ProLiant DL380 Gen11",
                "hpe-proliant-dl380-g11",
                "hp-inc",
                "P52534-B21",
                60,
                self._fs_server,
                "5-Year Straight-Line",
                "servers",
                "application-server",
                {"cpu": "2x Intel Xeon Silver 4416+", "ram_gb": 128, "storage_gb": 4000, "storage_type": "SSD RAID"},
            ),
            (
                "DiskStation DS1823xs+",
                "synology-ds1823xs",
                "synology-inc",
                "DS1823XS+",
                60,
                self._fs_server,
                "5-Year Straight-Line",
                "storage-devices",
                "backup-server",
                {"cpu": "AMD Ryzen V1780B", "ram_gb": 32, "storage_gb": 64000, "storage_type": "HDD"},
            ),
            (
                "iPhone 15 Pro",
                "iphone-15-pro",
                "apple-inc",
                "A2847",
                24,
                self._fs_mobile,
                "3-Year Straight-Line",
                "mobile-phones",
                "corporate-smartphone",
                {"cpu": "Apple A17 Pro", "ram_gb": 8, "storage_gb": 256, "screen_size": 6.1},
            ),
            (
                "Galaxy S24 Ultra",
                "galaxy-s24-ultra",
                "samsung-electronics",
                "SM-S928B",
                24,
                self._fs_mobile,
                "3-Year Straight-Line",
                "mobile-phones",
                "corporate-smartphone",
                {"cpu": "Snapdragon 8 Gen 3", "ram_gb": 12, "storage_gb": 256, "screen_size": 6.8},
            ),
            (
                'iPad Pro 12.9"',
                "ipad-pro-129-2024",
                "apple-inc",
                "A2436",
                36,
                self._fs_mobile,
                "3-Year Straight-Line",
                "tablets",
                "field-tablet",
                {"cpu": "Apple M4", "ram_gb": 8, "storage_gb": 256, "screen_size": 12.9},
            ),
            (
                "Surface Pro 10",
                "surface-pro-10",
                "microsoft-corporation",
                "SURFPRO10-I7",
                36,
                self._fs_mobile,
                "3-Year Straight-Line",
                "tablets",
                "field-tablet",
                {"cpu": "Intel Core i7-1365U", "ram_gb": 16, "storage_gb": 512, "screen_size": 13.0},
            ),
            (
                "Catalyst 9300",
                "cisco-catalyst-9300",
                "cisco-systems",
                "C9300-48P",
                84,
                self._fs_switch,
                "7-Year Straight-Line",
                "network-devices",
                "access-switch",
                {"port_count": 48, "poe_budget_w": 740},
            ),
            (
                "UniFi Switch Pro 48 PoE",
                "unifi-switch-pro-48",
                "ubiquiti-inc",
                "USW-PRO-48-POE",
                60,
                self._fs_switch,
                "5-Year Straight-Line",
                "network-devices",
                "access-switch",
                {"port_count": 48, "poe_budget_w": 600},
            ),
            (
                "Meraki MR46",
                "meraki-mr46",
                "cisco-systems",
                "MR46-HW",
                60,
                None,
                "5-Year Straight-Line",
                "network-devices",
                "wireless-ap",
                {},
            ),
            (
                "UniFi Dream Machine Pro",
                "unifi-dream-machine-pro",
                "ubiquiti-inc",
                "UDM-Pro",
                60,
                self._fs_switch,
                "5-Year Straight-Line",
                "network-devices",
                "core-router-firewall",
                {"port_count": 8, "poe_budget_w": 0},
            ),
            (
                'Dell P2723DE 27" Monitor',
                "dell-p2723de-monitor",
                "dell-technologies",
                "P2723DE",
                60,
                None,
                "5-Year Straight-Line",
                "monitors",
                "desktop-monitor",
                {},
            ),
            (
                'Dell P2422HE 24" Monitor',
                "dell-p2422he-monitor",
                "dell-technologies",
                "P2422HE",
                60,
                None,
                "5-Year Straight-Line",
                "monitors",
                "desktop-monitor",
                {},
            ),
            (
                "Logitech Rally Bar",
                "logitech-rally-bar",
                "logitech-international",
                "960-001308",
                60,
                self._fs_av,
                "5-Year Straight-Line",
                "conference-systems",
                "conference-av",
                {"screen_size": 0},
            ),
        ]

        self._asset_types = {}
        for model_name, slug, mfr, part, eol, _legacy_fs, dep, cat, role, raw_specs in at_data:
            specs = _canonical_demo_specs(raw_specs, slug, cat)
            obj, _ = AssetType.objects.get_or_create(
                slug=slug,
                defaults={
                    "model": model_name,
                    "manufacturer": self._manufacturers[mfr],
                    "part_number": part,
                    "eol_months": eol,
                    "depreciation": self._depreciations[dep],
                    "category": self._categories[cat],
                    "asset_role": self._asset_roles[role],
                    "custom_field_data": specs,
                    "management_kind": AssetType.MANAGEMENT_LOCAL,
                    "region": "",
                    "configuration": "",
                    "library": None,
                    "library_definition_key": None,
                    "library_release": None,
                },
            )
            obj.custom_field_data = specs
            obj.management_kind = AssetType.MANAGEMENT_LOCAL
            obj.region = ""
            obj.configuration = ""
            obj.library = None
            obj.library_definition_key = None
            obj.library_release = None
            obj.save()
            obj.fieldset_memberships.all().delete()
            AssetTypeFieldset.objects.bulk_create(
                [
                    AssetTypeFieldset(asset_type=obj, fieldset=self._fieldsets[fieldset_slug], position=index * 10)
                    for index, fieldset_slug in enumerate(self._category_fieldsets[cat], start=1)
                ]
            )
            self._asset_types[slug] = obj

        # Components
        self._components = {}
        for name, slug, mfr, cat, part, specs in [
            (
                "Samsung 32GB DDR5-4800",
                "samsung-32gb-ddr5",
                "samsung-electronics",
                "ram-memory",
                "M324R4GA3BB0",
                {"capacity_gb": 32, "type": "DDR5"},
            ),
            (
                "Crucial 16GB DDR5-5600",
                "crucial-16gb-ddr5",
                "samsung-electronics",
                "ram-memory",
                "CT16G56C46S5",
                {"capacity_gb": 16, "type": "DDR5"},
            ),
            (
                "Samsung 1TB 990 Pro NVMe",
                "samsung-1tb-nvme",
                "samsung-electronics",
                "ssd-nvme",
                "MZ-V9P1T0B",
                {"capacity_gb": 1000, "type": "NVMe"},
            ),
            (
                "Samsung 2TB 990 Pro NVMe",
                "samsung-2tb-nvme",
                "samsung-electronics",
                "ssd-nvme",
                "MZ-V9P2T0B",
                {"capacity_gb": 2000, "type": "NVMe"},
            ),
            (
                "WD Red Pro 8TB HDD",
                "wd-red-8tb",
                "dell-technologies",
                "hdd",
                "WD8003FFBX",
                {"capacity_gb": 8000, "type": "HDD"},
            ),
            (
                "Seagate IronWolf Pro 12TB",
                "seagate-ironwolf-12tb",
                "dell-technologies",
                "hdd",
                "ST12000NE0008",
                {"capacity_gb": 12000, "type": "HDD"},
            ),
            ("Intel X710 10GbE NIC", "intel-x710-nic", "dell-technologies", "nic", "X710DA2", {"speed": "10GbE"}),
            ("NVIDIA RTX 6000 Ada 48GB", "nvidia-rtx-6000", "dell-technologies", "gpu", "RTX6000-ADA", {"vram_gb": 48}),
            ("Intel Xeon Gold 6430", "xeon-gold-6430", "dell-technologies", "cpu", "SRMZS", {"cores": 32}),
            (
                "Dell PERC H755 RAID Controller",
                "dell-perc-h755",
                "dell-technologies",
                "other",
                "PERC-H755",
                {"interface": "SAS 12Gb/s"},
            ),
        ]:
            obj, _ = Component.objects.get_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "manufacturer": self._manufacturers[mfr],
                    "category": self._categories[cat],
                    "part_number": part,
                    "specs": specs,
                },
            )
            self._components[slug] = obj

        # Accessories (tenant set later per stock; catalog rows are global definitions)
        self._accessory_defs = [
            ("USB-C Charger 65W", "usb-c-charger-65w", "dell-technologies", "charger", "450-AFGM", 10),
            ("USB-C to HDMI Adapter", "usb-c-hdmi-adapter", "dell-technologies", "adaptor", "470-AEGM", 10),
            ("Wireless Mouse MX Master 3S", "mx-master-3s", "logitech-international", "mouse", "910-006556", 10),
            ("Wireless Keyboard MX Keys", "mx-keys", "logitech-international", "keyboard", "920-009413", 10),
            ("Webcam Brio 500", "webcam-brio-500", "logitech-international", "webcam", "960-001422", 8),
            ("Headset Zone Wireless 2", "zone-wireless-2", "logitech-international", "headset", "981-000886", 8),
            ("Thunderbolt 4 Dock", "tb4-dock", "dell-technologies", "dock", "WD22TB4", 6),
            ('Dell 27" Monitor P2723DE', "dell-p2723de", "dell-technologies", "display", "DELL-P2723DE", 6),
        ]
        self._consumable_defs = [
            ("HP 26X Laser Toner - Black", "hp-26x-toner-black", "hp-inc", "toner", "CF226X", 5),
            ("Brother DR-241CL Drum Unit", "brother-dr-241cl", "brother-industries", "toner", "DR-241CL", 3),
            ("Arctic MX-6 Thermal Paste", "arctic-mx-6", "dell-technologies", "thermal-paste", "MX6-4G", 8),
            ("AA Batteries Pack 24", "aa-batteries-24", "logitech-international", "batteries", "AA-24PK", 15),
        ]
        # Accessory/Consumable catalogue objects are created per primary tenant
        # (they are tenant-scoped); store the definitions for the stock phase.
        self._accessories = {}
        self._consumables = {}

        # Software
        self._software = {}
        for name, mfr in [
            ("Windows 11 Enterprise", "microsoft-corporation"),
            ("macOS Sequoia", "apple-inc"),
            ("Microsoft 365 E5", "microsoft-corporation"),
            ("Microsoft Office LTSC 2024", "microsoft-corporation"),
            ("Adobe Creative Cloud", "microsoft-corporation"),
            ("JetBrains All Products Pack", "microsoft-corporation"),
            ("VMware vSphere 8 Enterprise Plus", "dell-technologies"),
            ("CrowdStrike Falcon", "microsoft-corporation"),
            ("1Password Business", "microsoft-corporation"),
            ("Zoom Workplace Enterprise", "microsoft-corporation"),
            ("Veeam Backup & Replication", "dell-technologies"),
            ("Autodesk AutoCAD", "microsoft-corporation"),
            ("SAS Analytics Pro", "dell-technologies"),
            ("Bloomberg Terminal", "microsoft-corporation"),
            ("Ubuntu Pro 24.04", "dell-technologies"),
        ]:
            obj, _ = Software.objects.get_or_create(name=name, defaults={"manufacturer": self._manufacturers[mfr]})
            self._software[name] = obj

        # Cloud / SaaS providers
        self._providers = {}
        for name, acct, url in [
            ("Amazon Web Services", "aws-org", "https://console.aws.amazon.com"),
            ("Microsoft Azure", "azure-ea", "https://portal.azure.com"),
            ("Google Cloud Platform", "gcp-org", "https://console.cloud.google.com"),
            ("GitHub Enterprise", "github-ent", "https://github.com/enterprises"),
            ("Cloudflare", "cloudflare", "https://dash.cloudflare.com"),
            ("Datadog", "datadog", "https://app.datadoghq.eu"),
        ]:
            obj, _ = Provider.objects.get_or_create(name=name, defaults={"account_id": acct, "portal_url": url})
            self._providers[name] = obj

        self.stdout.write(
            f"  {len(self._asset_types)} asset types, {len(self._components)} components, "
            f"{len(self._software)} software products, {len(self._providers)} providers."
        )
