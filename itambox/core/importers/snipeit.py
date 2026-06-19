"""
Snipe-IT → ITAMbox migration importer.

Usage (via management command):
    python manage.py import_snipeit --url https://snipe.example --token-env SNIPEIT_TOKEN
                                    [--tenant <slug>] [--map-companies-to-tenants]
                                    [--dry-run] [--skip assets,licenses,...] [--update]
"""
from __future__ import annotations

import re
import time
import datetime
import logging
from decimal import Decimal, InvalidOperation
from typing import Iterator

import requests
from dateutil.relativedelta import relativedelta
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SnipeITError(Exception):
    pass


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

class SnipeITClient:
    """Thin HTTP client that handles auth, pagination, and 429 back-off."""

    PAGE_SIZE = 500

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip('/')
        self._session = requests.Session()
        self._session.headers.update({
            'Authorization': f'Bearer {token}',
            'Accept': 'application/json',
        })

    def get_all(self, endpoint: str, params: dict | None = None) -> Iterator[dict]:
        """Yield every row from a paginated list endpoint."""
        offset = 0
        while True:
            data = self._get(endpoint, {**(params or {}), 'limit': self.PAGE_SIZE, 'offset': offset})
            rows = data.get('rows') or []
            yield from rows
            offset += len(rows)
            if offset >= (data.get('total') or 0) or not rows:
                break

    def get_detail(self, endpoint: str) -> dict:
        """GET a single resource."""
        return self._get(endpoint)

    def _get(self, endpoint: str, params: dict | None = None, _retries: int = 0) -> dict:
        url = f"{self.base_url}{endpoint}"
        try:
            resp = self._session.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            raise SnipeITError(f"Network error fetching {url}: {exc}") from exc

        if resp.status_code == 429:
            wait = int(resp.headers.get('Retry-After', 30))
            if _retries < 5:
                logger.warning("Snipe-IT rate-limited — sleeping %ds (retry %d)", wait, _retries + 1)
                time.sleep(wait)
                return self._get(endpoint, params=params, _retries=_retries + 1)
            raise SnipeITError(f"Rate-limited after 5 retries on {url}")

        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise SnipeITError(f"HTTP {resp.status_code} from {url}: {exc}") from exc

        return resp.json()


# ---------------------------------------------------------------------------
# Field-type conversion helpers
# ---------------------------------------------------------------------------

_FIELD_FORMAT_MAP = {
    'TEXT': 'text',
    'TEXTAREA': 'text',
    'NUMERIC': 'number',
    'DATE': 'date',
    'BOOLEAN': 'boolean',
    'CHECKBOX': 'boolean',
    'LIST': 'select',
    'LISTBOX': 'select',
    'RADIO': 'select',
}

_MAINTENANCE_TYPE_MAP = {
    'maintenance': 'repair',
    'repair': 'repair',
    'upgrade': 'upgrade',
    'hardware support': 'hardware_support',
    'software support': 'software_support',
    'pat test': 'calibration',
    'asset review': 'calibration',
    'firmware update': 'upgrade',
    'other': 'repair',
}

_MAINTENANCE_STATUS_MAP = {
    'pending': 'scheduled',
    'complete': 'completed',
    'in progress': 'in_progress',
}

_STATUS_TYPE_MAP = {
    'deployable': 'deployable',
    'pending': 'pending',
    'undeployable': 'undeployable',
    'archived': 'archived',
    'out of deployable': 'deployed',
}

_CATEGORY_APPLIES_MAP = {
    'asset': {'asset': True},
    'accessory': {'accessory': True},
    'consumable': {'consumable': True},
    'component': {'component': True},
    'license': {'asset': True},
}


def _parse_date(val: str | None) -> datetime.date | None:
    if not val:
        return None
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y'):
        try:
            return datetime.datetime.strptime(val, fmt).date()
        except (ValueError, TypeError):
            pass
    return None


def _parse_decimal(val) -> Decimal | None:
    if val is None:
        return None
    try:
        return Decimal(str(val)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError):
        return None


def _clean_field_name(db_column: str) -> str:
    """Strip _snipeit_ prefix and trailing _<id> from a Snipe-IT db_column_name."""
    name = db_column
    name = re.sub(r'^_snipeit_', '', name)
    name = re.sub(r'_\d+$', '', name)
    return name[:100]


def _unique_slug(model_class, name: str, extra: str = '') -> str:
    """Generate a unique slug for model_class by slugifying name, appending counter on collision."""
    from django.core.exceptions import FieldError
    base = (slugify(f"{name} {extra}") if extra else slugify(name)) or 'imported'
    base = base[:90]
    slug = base
    counter = 1
    manager = getattr(model_class, '_base_manager', model_class.objects)
    while True:
        try:
            exists = manager.filter(slug=slug, deleted_at__isnull=True).exists()
        except FieldError:
            exists = manager.filter(slug=slug).exists()
        if not exists:
            break
        slug = f"{base}-{counter}"
        counter += 1
    return slug


def _nested_id(obj) -> int | None:
    if isinstance(obj, dict):
        return obj.get('id')
    return None


def _nested_str(obj, key='name') -> str:
    if isinstance(obj, dict):
        return obj.get(key) or ''
    return ''


# ---------------------------------------------------------------------------
# Main importer
# ---------------------------------------------------------------------------

class SnipeITImporter:
    """
    Orchestrates the full Snipe-IT → ITAMbox import.

    Each entity-type pass runs inside TaskContext so change-logging and tenant
    scoping work correctly.  Writes are wrapped in per-batch transaction.atomic()
    rather than one giant transaction so partial progress survives errors.
    """

    def __init__(
        self,
        client: SnipeITClient,
        tenant,
        user,
        dry_run: bool = False,
        update: bool = False,
        map_companies: bool = False,
        skip: set | None = None,
        job=None,
        stdout=None,
    ):
        self.client = client
        self.default_tenant = tenant
        self.user = user
        self.dry_run = dry_run
        self.update = update
        self.map_companies = map_companies
        self.skip = skip or set()
        self.job = job
        self._stdout = stdout

        # Snipe-IT ID → local model instance caches
        self._status_map: dict[int, object] = {}
        self._manufacturer_map: dict[int, object] = {}
        self._supplier_map: dict[int, object] = {}
        self._category_map: dict[int, object] = {}
        self._location_map: dict[int, object] = {}
        self._holder_map: dict[int, object] = {}
        self._field_map: dict[str, object] = {}    # db_column_name → CustomField
        self._fieldset_map: dict[int, object] = {}
        self._model_map: dict[int, object] = {}    # asset-model (AssetType)
        self._asset_map: dict[int, object] = {}
        self._tenant_map: dict[int, object] = {}   # company → Tenant
        self._software_map: dict[int, object] = {}

        # Import counts: entity → {created, updated, skipped, failed}
        self.counts: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> dict:
        """Run the full import in dependency order and return counts."""
        self._import_status_labels()
        self._import_manufacturers()
        self._import_categories()
        self._import_suppliers()
        if self.map_companies:
            self._import_companies()
        self._import_locations()
        self._import_users()
        self._import_fields()
        self._import_fieldsets()
        self._import_models()
        if 'assets' not in self.skip:
            self._import_hardware()
        if 'accessories' not in self.skip:
            self._import_accessories()
        if 'consumables' not in self.skip:
            self._import_consumables()
        if 'components' not in self.skip:
            self._import_components()
        if 'licenses' not in self.skip:
            self._import_licenses()
        if 'maintenances' not in self.skip:
            self._import_maintenances()
        return self.counts

    # ------------------------------------------------------------------
    # Logging helpers
    # ------------------------------------------------------------------

    def _log(self, msg: str) -> None:
        if self._stdout:
            self._stdout.write(msg)
        if self.job:
            self.job.append_log(msg)
        logger.info(msg)

    def _counter(self, key: str) -> dict:
        if key not in self.counts:
            self.counts[key] = {'created': 0, 'updated': 0, 'skipped': 0, 'failed': 0}
        return self.counts[key]

    def _finish(self, key: str) -> None:
        c = self.counts.get(key, {})
        self._log(
            f"  {key}: {c.get('created',0)} created, {c.get('updated',0)} updated, "
            f"{c.get('skipped',0)} skipped, {c.get('failed',0)} failed"
        )

    # ------------------------------------------------------------------
    # Context: get or infer active tenant for a row
    # ------------------------------------------------------------------

    def _tenant_for(self, row: dict) -> object:
        if self.map_companies:
            cid = _nested_id(row.get('company'))
            if cid and cid in self._tenant_map:
                return self._tenant_map[cid]
        return self.default_tenant

    # ------------------------------------------------------------------
    # Status labels
    # ------------------------------------------------------------------

    def _import_status_labels(self) -> None:
        from assets.models import StatusLabel
        key = 'statuslabels'
        self._log(f"\n[{key}]")
        c = self._counter(key)
        rows = list(self.client.get_all('/api/v1/statuslabels'))
        for row in rows:
            sid = row['id']
            name = row.get('name', '').strip() or f'Imported Status {sid}'
            snipe_type = (row.get('type') or '').lower()
            itam_type = _STATUS_TYPE_MAP.get(snipe_type, 'deployable')
            try:
                with transaction.atomic():
                    obj = StatusLabel.all_objects.filter(name=name).first()
                    if obj and not self.update:
                        c['skipped'] += 1
                        self._status_map[sid] = obj
                        continue
                    if obj and self.update:
                        if not self.dry_run:
                            obj.type = itam_type
                            obj.save(update_fields=['type'])
                        c['updated'] += 1
                        self._status_map[sid] = obj
                        continue
                    if not self.dry_run:
                        obj = StatusLabel.objects.create(name=name, type=itam_type, color='6c757d')
                    else:
                        obj = StatusLabel(id=-sid, name=name, type=itam_type)
                    c['created'] += 1
                    self._status_map[sid] = obj
            except Exception as exc:
                self._log(f"  ! statuslabel {sid} '{name}': {exc}")
                c['failed'] += 1
        self._finish(key)

    # ------------------------------------------------------------------
    # Manufacturers
    # ------------------------------------------------------------------

    def _import_manufacturers(self) -> None:
        from assets.models import Manufacturer
        key = 'manufacturers'
        self._log(f"\n[{key}]")
        c = self._counter(key)
        for row in self.client.get_all('/api/v1/manufacturers'):
            sid = row['id']
            name = (row.get('name') or '').strip() or f'Manufacturer {sid}'
            try:
                with transaction.atomic():
                    obj = Manufacturer.all_objects.filter(name=name).first()
                    if obj and not self.update:
                        c['skipped'] += 1
                        self._manufacturer_map[sid] = obj
                        continue
                    if obj and self.update:
                        c['updated'] += 1
                        self._manufacturer_map[sid] = obj
                        continue
                    if not self.dry_run:
                        obj, created = Manufacturer.objects.get_or_create(name=name)
                    else:
                        obj = Manufacturer(id=-sid, name=name)
                        created = True
                    c['created' if created else 'skipped'] += 1
                    self._manufacturer_map[sid] = obj
            except Exception as exc:
                self._log(f"  ! manufacturer {sid} '{name}': {exc}")
                c['failed'] += 1
        self._finish(key)

    # ------------------------------------------------------------------
    # Categories
    # ------------------------------------------------------------------

    def _import_categories(self) -> None:
        from assets.models import Category
        key = 'categories'
        self._log(f"\n[{key}]")
        c = self._counter(key)
        for row in self.client.get_all('/api/v1/categories'):
            sid = row['id']
            name = (row.get('name') or '').strip() or f'Category {sid}'
            cat_type = (row.get('category_type') or 'asset').lower()
            applies_to = _CATEGORY_APPLIES_MAP.get(cat_type, {'asset': True})
            try:
                with transaction.atomic():
                    obj = Category.all_objects.filter(name=name).first()
                    if obj and not self.update:
                        c['skipped'] += 1
                        self._category_map[sid] = obj
                        continue
                    if obj and self.update:
                        if not self.dry_run:
                            obj.applies_to = applies_to
                            obj.save(update_fields=['applies_to'])
                        c['updated'] += 1
                        self._category_map[sid] = obj
                        continue
                    if not self.dry_run:
                        obj = Category.objects.create(name=name, applies_to=applies_to)
                    else:
                        obj = Category(id=-sid, name=name, applies_to=applies_to)
                    c['created'] += 1
                    self._category_map[sid] = obj
            except Exception as exc:
                self._log(f"  ! category {sid} '{name}': {exc}")
                c['failed'] += 1
        self._finish(key)

    # ------------------------------------------------------------------
    # Suppliers
    # ------------------------------------------------------------------

    def _import_suppliers(self) -> None:
        from assets.models import Supplier
        from organization.models import Contact, ContactRole
        from django.contrib.contenttypes.models import ContentType as CT
        key = 'suppliers'
        self._log(f"\n[{key}]")
        c = self._counter(key)
        for row in self.client.get_all('/api/v1/suppliers'):
            sid = row['id']
            name = (row.get('name') or '').strip() or f'Supplier {sid}'
            contact_email = (row.get('email') or '')[:254]
            contact_phone = (row.get('phone') or '')[:50]
            contact_name = (row.get('contact') or '')[:255]
            defaults = {
                'website': (row.get('url') or '')[:200],
                'notes': row.get('notes') or '',
                'custom_field_data': {'snipeit_id': str(sid)},
            }
            try:
                with transaction.atomic():
                    obj = Supplier.all_objects.filter(custom_field_data__snipeit_id=str(sid)).first()
                    if not obj:
                        obj = Supplier.all_objects.filter(name=name).first()
                    if obj:
                        if not self.update:
                            c['skipped'] += 1
                            self._supplier_map[sid] = obj
                            continue
                        if not self.dry_run:
                            for field, val in defaults.items():
                                setattr(obj, field, val)
                            obj.save()
                        c['updated'] += 1
                        self._supplier_map[sid] = obj
                        continue
                    if not self.dry_run:
                        obj = Supplier.objects.create(name=name, **defaults)
                        if contact_name or contact_email or contact_phone:
                            from organization.models import ContactAssignment
                            supplier_ct = CT.objects.get_for_model(Supplier)
                            primary_role, _ = ContactRole.objects.get_or_create(
                                slug='primary-contact',
                                defaults={'name': 'Primary Contact', 'description': 'Primary Contact'},
                            )
                            contact = Contact.objects.create(
                                name=contact_name or f"{name} Contact",
                                phone=contact_phone,
                                email=contact_email,
                            )
                            ContactAssignment.objects.create(
                                contact=contact,
                                role=primary_role,
                                content_type=supplier_ct,
                                object_id=obj.pk,
                                priority='primary',
                            )
                    else:
                        obj = Supplier(id=-sid, name=name)
                    c['created'] += 1
                    self._supplier_map[sid] = obj
            except Exception as exc:
                self._log(f"  ! supplier {sid} '{name}': {exc}")
                c['failed'] += 1
        self._finish(key)

    # ------------------------------------------------------------------
    # Companies → Tenants (optional, MSP mode)
    # ------------------------------------------------------------------

    def _import_companies(self) -> None:
        from organization.models import Tenant
        key = 'companies'
        self._log(f"\n[{key}] (--map-companies-to-tenants)")
        c = self._counter(key)
        for row in self.client.get_all('/api/v1/companies'):
            sid = row['id']
            name = (row.get('name') or '').strip() or f'Company {sid}'
            try:
                with transaction.atomic():
                    obj = Tenant.all_objects.filter(name=name).first()
                    if obj:
                        c['skipped'] += 1
                        self._tenant_map[sid] = obj
                        continue
                    if not self.dry_run:
                        obj = Tenant.objects.create(
                            name=name,
                            slug=_unique_slug(Tenant, name),
                        )
                    else:
                        obj = Tenant(id=-sid, name=name)
                    c['created'] += 1
                    self._tenant_map[sid] = obj
            except Exception as exc:
                self._log(f"  ! company {sid} '{name}': {exc}")
                c['failed'] += 1
        self._finish(key)

    # ------------------------------------------------------------------
    # Locations
    # ------------------------------------------------------------------

    def _import_locations(self) -> None:
        from organization.models import Location, Site
        key = 'locations'
        self._log(f"\n[{key}]")
        c = self._counter(key)

        # Ensure a default import site exists.
        # Site has no tenant field for this shared record, so we must bypass
        # the tenant-scoped manager to find it on idempotent re-runs.
        if not self.dry_run:
            from core.managers import get_current_tenant, set_current_tenant
            _saved_tenant = get_current_tenant()
            set_current_tenant(None)
            try:
                import_site = Site.all_objects.filter(
                    name='Imported (Snipe-IT)', deleted_at__isnull=True
                ).first()
                if not import_site:
                    import_site = Site.objects.create(
                        name='Imported (Snipe-IT)',
                        status='active',
                        slug=_unique_slug(Site, 'Imported Snipe-IT'),
                    )
            finally:
                set_current_tenant(_saved_tenant)
        else:
            import_site = Site(id=-1, name='Imported (Snipe-IT)')

        rows = list(self.client.get_all('/api/v1/locations'))

        def _upsert_location(row):
            sid = row['id']
            name = (row.get('name') or '').strip() or f'Location {sid}'
            parent_id = _nested_id(row.get('parent'))
            parent_obj = self._location_map.get(parent_id) if parent_id else None
            tenant = self._tenant_for(row)
            defaults = {
                'custom_field_data': {'snipeit_id': str(sid)},
                'site': import_site,
                'tenant': tenant,
                'parent': parent_obj,
            }
            with transaction.atomic():
                obj = Location.all_objects.filter(custom_field_data__snipeit_id=str(sid)).first()
                if not obj:
                    obj = Location.all_objects.filter(name=name, tenant=tenant).first()
                if obj:
                    if not self.update:
                        c['skipped'] += 1
                        self._location_map[sid] = obj
                        return
                    if not self.dry_run:
                        obj.parent = parent_obj
                        obj.custom_field_data['snipeit_id'] = str(sid)
                        obj.save(update_fields=['parent', 'custom_field_data'])
                    c['updated'] += 1
                    self._location_map[sid] = obj
                    return
                if not self.dry_run:
                    defaults['slug'] = _unique_slug(Location, name)
                    obj = Location.objects.create(name=name, **defaults)
                else:
                    obj = Location(id=-sid, name=name, site=import_site, tenant=tenant)
                c['created'] += 1
                self._location_map[sid] = obj

        # Two passes: parents first, then children
        for row in rows:
            if not _nested_id(row.get('parent')):
                try:
                    _upsert_location(row)
                except Exception as exc:
                    self._log(f"  ! location {row['id']} (pass 1): {exc}")
                    c['failed'] += 1

        for row in rows:
            if _nested_id(row.get('parent')):
                try:
                    _upsert_location(row)
                except Exception as exc:
                    self._log(f"  ! location {row['id']} (pass 2): {exc}")
                    c['failed'] += 1

        self._finish(key)

    # ------------------------------------------------------------------
    # Users → AssetHolder (no Django auth users created)
    # ------------------------------------------------------------------

    def _import_users(self) -> None:
        from organization.models import AssetHolder
        key = 'users'
        self._log(f"\n[{key}]")
        c = self._counter(key)
        for row in self.client.get_all('/api/v1/users'):
            sid = row['id']
            first = (row.get('first_name') or '').strip()
            last = (row.get('last_name') or '').strip()
            email = (row.get('email') or '').strip()
            username = (row.get('username') or '').strip()
            upn = username or email or f'imported-user-{sid}'
            tenant = self._tenant_for(row)
            defaults = {
                'first_name': first,
                'last_name': last,
                'email': email,
                'upn': upn,
                'tenant': tenant,
                'custom_field_data': {'snipeit_id': str(sid)},
            }
            try:
                with transaction.atomic():
                    obj = AssetHolder.all_objects.filter(custom_field_data__snipeit_id=str(sid)).first()
                    if not obj:
                        obj = AssetHolder.all_objects.filter(upn=upn, tenant=tenant).first()
                    if obj:
                        if not self.update:
                            c['skipped'] += 1
                            self._holder_map[sid] = obj
                            continue
                        if not self.dry_run:
                            for field, val in defaults.items():
                                setattr(obj, field, val)
                            obj.save()
                        c['updated'] += 1
                        self._holder_map[sid] = obj
                        continue
                    if not self.dry_run:
                        obj = AssetHolder.objects.create(**defaults)
                    else:
                        obj = AssetHolder(id=-sid, upn=upn, tenant=tenant)
                    c['created'] += 1
                    self._holder_map[sid] = obj
            except Exception as exc:
                self._log(f"  ! user {sid} '{upn}': {exc}")
                c['failed'] += 1
        self._finish(key)

    # ------------------------------------------------------------------
    # Custom fields
    # ------------------------------------------------------------------

    def _import_fields(self) -> None:
        from extras.models import CustomField
        from assets.models import Asset
        key = 'fields'
        self._log(f"\n[{key}]")
        c = self._counter(key)
        asset_ct = ContentType.objects.get_for_model(Asset)
        for row in self.client.get_all('/api/v1/fields'):
            sid = row['id']
            db_col = row.get('db_column_name') or ''
            raw_name = _clean_field_name(db_col) if db_col else f'snipeit_field_{sid}'
            label = (row.get('name') or raw_name).strip()[:100]
            fmt = (row.get('format') or row.get('type') or 'TEXT').upper()
            field_type = _FIELD_FORMAT_MAP.get(fmt, 'text')
            raw_choices = row.get('field_values') or ''
            choices = '\n'.join(v.strip() for v in raw_choices.split('\n') if v.strip()) if raw_choices else ''
            try:
                with transaction.atomic():
                    obj = CustomField.all_objects.filter(name=raw_name).first()
                    if obj:
                        if not self.update:
                            c['skipped'] += 1
                        else:
                            if not self.dry_run:
                                obj.label = label
                                obj.field_type = field_type
                                if choices:
                                    obj.choices = choices
                                obj.save(update_fields=['label', 'field_type', 'choices'])
                            c['updated'] += 1
                        self._field_map[db_col] = obj
                        continue
                    if not self.dry_run:
                        obj = CustomField.objects.create(
                            name=raw_name, label=label, field_type=field_type, choices=choices)
                        obj.object_types.add(asset_ct)
                    else:
                        obj = CustomField(id=-sid, name=raw_name, label=label, field_type=field_type)
                    c['created'] += 1
                    self._field_map[db_col] = obj
            except Exception as exc:
                self._log(f"  ! field {sid} '{raw_name}': {exc}")
                c['failed'] += 1
        self._finish(key)

    # ------------------------------------------------------------------
    # Custom fieldsets
    # ------------------------------------------------------------------

    def _import_fieldsets(self) -> None:
        from extras.models import CustomFieldset
        key = 'fieldsets'
        self._log(f"\n[{key}]")
        c = self._counter(key)
        for row in self.client.get_all('/api/v1/fieldsets'):
            sid = row['id']
            name = (row.get('name') or '').strip() or f'Fieldset {sid}'
            field_ids = [f.get('id') for f in (row.get('fields', {}).get('rows') or []) if f.get('id')]
            try:
                with transaction.atomic():
                    obj = CustomFieldset.all_objects.filter(name=name).first()
                    if obj:
                        if not self.update:
                            c['skipped'] += 1
                            self._fieldset_map[sid] = obj
                            continue
                        c['updated'] += 1
                        self._fieldset_map[sid] = obj
                        continue
                    if not self.dry_run:
                        obj = CustomFieldset.objects.create(name=name)
                        cf_objs = [
                            self._field_map[db_col]
                            for row2 in (row.get('fields', {}).get('rows') or [])
                            if (db_col := row2.get('db_column_name')) and db_col in self._field_map
                        ]
                        if cf_objs:
                            obj.fields.set(cf_objs)
                    else:
                        obj = CustomFieldset(id=-sid, name=name)
                    c['created'] += 1
                    self._fieldset_map[sid] = obj
            except Exception as exc:
                self._log(f"  ! fieldset {sid} '{name}': {exc}")
                c['failed'] += 1
        self._finish(key)

    # ------------------------------------------------------------------
    # Models → AssetType
    # ------------------------------------------------------------------

    def _import_models(self) -> None:
        from assets.models import AssetType
        key = 'models'
        self._log(f"\n[{key}]")
        c = self._counter(key)
        for row in self.client.get_all('/api/v1/models'):
            sid = row['id']
            model_name = (row.get('name') or '').strip() or f'Model {sid}'
            mfr = self._manufacturer_map.get(_nested_id(row.get('manufacturer')))
            cat = self._category_map.get(_nested_id(row.get('category')))
            fieldset = self._fieldset_map.get(_nested_id(row.get('fieldset')))
            eol_months = row.get('eol') or None
            part_number = (row.get('model_number') or '')[:100]
            defaults = {
                'model': model_name,
                'manufacturer': mfr,
                'category': cat,
                'custom_fieldset': fieldset,
                'eol_months': int(eol_months) if eol_months else None,
                'part_number': part_number,
                'custom_field_data': {'snipeit_id': str(sid)},
            }
            try:
                with transaction.atomic():
                    obj = AssetType.all_objects.filter(custom_field_data__snipeit_id=str(sid)).first()
                    if not obj:
                        obj = AssetType.all_objects.filter(model=model_name, manufacturer=mfr).first()
                    if obj:
                        if not self.update:
                            c['skipped'] += 1
                            self._model_map[sid] = obj
                            continue
                        if not self.dry_run:
                            for field, val in defaults.items():
                                setattr(obj, field, val)
                            obj.save()
                        c['updated'] += 1
                        self._model_map[sid] = obj
                        continue
                    if not self.dry_run:
                        obj = AssetType.objects.create(**defaults)
                    else:
                        obj = AssetType(id=-sid, model=model_name, manufacturer=mfr)
                    c['created'] += 1
                    self._model_map[sid] = obj
            except Exception as exc:
                self._log(f"  ! model {sid} '{model_name}': {exc}")
                c['failed'] += 1
        self._finish(key)

    # ------------------------------------------------------------------
    # Hardware → Asset + optional AssetAssignment
    # ------------------------------------------------------------------

    def _import_hardware(self) -> None:
        from assets.models import Asset, StatusLabel, AssetAssignment
        from assets.choices import StatusTypeChoices
        from assets.services import checkout_asset
        key = 'assets'
        self._log(f"\n[{key}]")
        c = self._counter(key)

        # Ensure a "Deployed (imported)" status label exists for checkout
        if not self.dry_run:
            deployed_status, _ = StatusLabel.objects.get_or_create(
                name='Deployed (imported)',
                defaults={'type': StatusTypeChoices.DEPLOYED, 'color': '007bff'},
            )
        else:
            deployed_status = StatusLabel(id=-9999, name='Deployed (imported)', type='deployed')

        for row in self.client.get_all('/api/v1/hardware'):
            sid = row['id']
            asset_tag = (row.get('asset_tag') or '').strip() or f'IMPORT-{sid}'
            serial = (row.get('serial') or '').strip()
            name = (row.get('name') or '').strip() or asset_tag
            asset_type = self._model_map.get(_nested_id(row.get('model')))
            status_obj = self._status_map.get(_nested_id(row.get('status_label')))
            tenant = self._tenant_for(row)
            supplier = self._supplier_map.get(_nested_id(row.get('supplier')))
            location = (
                self._location_map.get(_nested_id(row.get('location')))
                or self._location_map.get(_nested_id(row.get('rtd_location')))
            )
            purchase_date = _parse_date(_nested_str(row.get('purchase_date'), 'date'))
            purchase_cost = _parse_decimal(row.get('purchase_cost'))
            order_number = (row.get('order_number') or '')[:100]
            notes = row.get('notes') or ''
            warranty_months = row.get('warranty_months') or None

            warranty_expiration = None
            if purchase_date and warranty_months:
                try:
                    warranty_expiration = (
                        purchase_date + relativedelta(months=int(warranty_months))
                    )
                except (TypeError, ValueError):
                    pass

            # Build custom_field_data from snipe custom fields
            cf_data: dict = {'snipeit_id': str(sid)}
            for cf_label, cf_info in (row.get('custom_fields') or {}).items():
                if not isinstance(cf_info, dict):
                    continue
                db_col = cf_info.get('field') or ''
                value = cf_info.get('value')
                if value is None or value == '':
                    continue
                local_field = self._field_map.get(db_col)
                if local_field:
                    cf_data[local_field.name] = value

            try:
                with transaction.atomic():
                    obj = Asset.all_objects.filter(custom_field_data__snipeit_id=str(sid)).first()
                    if not obj and serial:
                        obj = Asset.all_objects.filter(serial_number=serial, tenant=tenant).first()
                    if not obj:
                        obj = Asset.all_objects.filter(asset_tag=asset_tag, tenant=tenant).first()

                    if obj:
                        if not self.update:
                            c['skipped'] += 1
                            self._asset_map[sid] = obj
                            continue
                        if not self.dry_run:
                            obj.name = name
                            obj.serial_number = serial
                            obj.asset_type = asset_type
                            obj.status = status_obj
                            obj.location = location
                            obj.purchase_date = purchase_date
                            obj.purchase_cost = purchase_cost
                            obj.order_number = order_number
                            obj.notes = notes
                            obj.supplier = supplier
                            obj.custom_field_data.update(cf_data)
                            obj.save()
                            if warranty_expiration and purchase_date:
                                from assets.models import Warranty, WarrantyTypeChoices
                                Warranty.objects.update_or_create(
                                    asset=obj, warranty_type=WarrantyTypeChoices.HARDWARE,
                                    defaults={'start_date': purchase_date, 'end_date': warranty_expiration,
                                              'provider': supplier.name if supplier else ''},
                                )
                        c['updated'] += 1
                        self._asset_map[sid] = obj
                        continue

                    if not self.dry_run:
                        obj = Asset.objects.create(
                            name=name,
                            asset_tag=asset_tag,
                            serial_number=serial,
                            asset_type=asset_type,
                            status=status_obj,
                            location=location,
                            tenant=tenant,
                            purchase_date=purchase_date,
                            purchase_cost=purchase_cost,
                            order_number=order_number,
                            notes=notes,
                            supplier=supplier,
                            custom_field_data=cf_data,
                        )
                    else:
                        obj = Asset(id=-sid, asset_tag=asset_tag, tenant=tenant)
                    if not self.dry_run and warranty_expiration and purchase_date:
                        from assets.models import Warranty, WarrantyTypeChoices
                        Warranty.objects.update_or_create(
                            asset=obj, warranty_type=WarrantyTypeChoices.HARDWARE,
                            defaults={'start_date': purchase_date, 'end_date': warranty_expiration,
                                      'provider': supplier.name if supplier else ''},
                        )
                    c['created'] += 1
                    self._asset_map[sid] = obj

            except Exception as exc:
                self._log(f"  ! asset {sid} '{asset_tag}': {exc}")
                c['failed'] += 1
                continue

            # Handle checkout / assignment
            assigned_to = row.get('assigned_to')
            if not assigned_to or self.dry_run:
                continue
            target_type = (assigned_to.get('type') or '').lower()
            target_id = assigned_to.get('id')
            try:
                with transaction.atomic():
                    if target_type == 'user':
                        holder = self._holder_map.get(target_id)
                        if holder and obj.pk and obj.pk > 0:
                            checkout_asset(
                                asset=obj, holder=holder, user=self.user,
                                status=deployed_status, notes='Imported from Snipe-IT',
                            )
                    elif target_type == 'location':
                        loc = self._location_map.get(target_id)
                        if loc and obj.pk and obj.pk > 0:
                            checkout_asset(
                                asset=obj, location=loc, user=self.user,
                                status=deployed_status, notes='Imported from Snipe-IT',
                            )
                    elif target_type == 'asset':
                        target_asset = self._asset_map.get(target_id)
                        if target_asset and obj.pk and obj.pk > 0:
                            checkout_asset(
                                asset=obj, asset_target=target_asset, user=self.user,
                                status=deployed_status, notes='Imported from Snipe-IT',
                            )
            except Exception as exc:
                self._log(f"  ! checkout asset {sid}: {exc}")

        self._finish(key)

    # ------------------------------------------------------------------
    # Accessories
    # ------------------------------------------------------------------

    def _import_accessories(self) -> None:
        from inventory.models import Accessory, AccessoryAssignment
        from organization.models import AssetHolder
        key = 'accessories'
        self._log(f"\n[{key}]")
        c = self._counter(key)

        for row in self.client.get_all('/api/v1/accessories'):
            sid = row['id']
            name = (row.get('name') or '').strip() or f'Accessory {sid}'
            mfr = self._manufacturer_map.get(_nested_id(row.get('manufacturer')))
            cat = self._category_map.get(_nested_id(row.get('category')))
            supplier = self._supplier_map.get(_nested_id(row.get('supplier')))
            tenant = self._tenant_for(row)
            qty = row.get('qty') or 1

            defaults = {
                'manufacturer': mfr,
                'category': cat,
                'supplier': supplier,
                'tenant': tenant,
                'notes': row.get('notes') or '',
                'custom_field_data': {'snipeit_id': str(sid)},
            }
            try:
                with transaction.atomic():
                    obj = Accessory.all_objects.filter(custom_field_data__snipeit_id=str(sid)).first()
                    if not obj:
                        obj = Accessory.all_objects.filter(name=name, tenant=tenant).first()
                    if obj:
                        if not self.update:
                            c['skipped'] += 1
                            # Still try to import checkouts for existing items
                        else:
                            if not self.dry_run:
                                for field, val in defaults.items():
                                    setattr(obj, field, val)
                                obj.save()
                            c['updated'] += 1

                        # Import checkouts from /accessories/{id}/checkedout
                        if not self.dry_run:
                            self._import_accessory_checkouts(obj, sid)
                        continue

                    if not self.dry_run:
                        obj = Accessory.objects.create(name=name, **defaults)
                        # Create initial stock entry
                        from inventory.models import AccessoryStock
                        from organization.models import Location
                        # Use first available location for this tenant or no location
                        loc = (
                            Location.objects.filter(tenant=tenant).first()
                            if tenant else None
                        )
                        if loc:
                            AccessoryStock.objects.create(accessory=obj, location=loc, qty=qty)
                        self._import_accessory_checkouts(obj, sid)
                    else:
                        obj = Accessory(id=-sid, name=name, tenant=tenant)
                    c['created'] += 1

            except Exception as exc:
                self._log(f"  ! accessory {sid} '{name}': {exc}")
                c['failed'] += 1

        self._finish(key)

    def _import_accessory_checkouts(self, accessory, snipe_id: int) -> None:
        """Import per-user checkouts for an accessory."""
        from inventory.models import AccessoryAssignment
        try:
            data = self.client.get_all(f'/api/v1/accessories/{snipe_id}/checkedout')
            for co in data:
                user_id = _nested_id(co.get('assigned_to'))
                if not user_id:
                    continue
                holder = self._holder_map.get(user_id)
                if not holder or not holder.pk or holder.pk < 0:
                    continue
                qty = co.get('qty') or 1
                AccessoryAssignment.objects.get_or_create(
                    accessory=accessory,
                    assigned_holder=holder,
                    defaults={'qty': qty, 'notes': 'Imported from Snipe-IT'},
                )
        except Exception as exc:
            logger.warning("Could not import checkouts for accessory %s: %s", snipe_id, exc)

    # ------------------------------------------------------------------
    # Consumables
    # ------------------------------------------------------------------

    def _import_consumables(self) -> None:
        from inventory.models import Consumable
        key = 'consumables'
        self._log(f"\n[{key}]")
        c = self._counter(key)

        for row in self.client.get_all('/api/v1/consumables'):
            sid = row['id']
            name = (row.get('name') or '').strip() or f'Consumable {sid}'
            mfr = self._manufacturer_map.get(_nested_id(row.get('manufacturer')))
            cat = self._category_map.get(_nested_id(row.get('category')))
            supplier = self._supplier_map.get(_nested_id(row.get('supplier')))
            tenant = self._tenant_for(row)
            qty = row.get('qty') or 0

            defaults = {
                'manufacturer': mfr,
                'category': cat,
                'supplier': supplier,
                'tenant': tenant,
                'notes': row.get('notes') or '',
                'custom_field_data': {'snipeit_id': str(sid)},
            }
            try:
                with transaction.atomic():
                    obj = Consumable.all_objects.filter(custom_field_data__snipeit_id=str(sid)).first()
                    if not obj:
                        obj = Consumable.all_objects.filter(name=name, tenant=tenant).first()
                    if obj:
                        if not self.update:
                            c['skipped'] += 1
                        else:
                            if not self.dry_run:
                                for field, val in defaults.items():
                                    setattr(obj, field, val)
                                obj.save()
                            c['updated'] += 1
                        continue
                    if not self.dry_run:
                        obj = Consumable.objects.create(name=name, **defaults)
                        from inventory.models import ConsumableStock
                        from organization.models import Location
                        loc = Location.objects.filter(tenant=tenant).first() if tenant else None
                        if loc and qty:
                            ConsumableStock.objects.create(consumable=obj, location=loc, qty=qty)
                    else:
                        obj = Consumable(id=-sid, name=name, tenant=tenant)
                    c['created'] += 1

            except Exception as exc:
                self._log(f"  ! consumable {sid} '{name}': {exc}")
                c['failed'] += 1

        self._finish(key)

    # ------------------------------------------------------------------
    # Components + allocations
    # ------------------------------------------------------------------

    def _import_components(self) -> None:
        from inventory.models import Component, ComponentAllocation, ComponentStock
        from organization.models import Location
        key = 'components'
        self._log(f"\n[{key}]")
        c = self._counter(key)

        for row in self.client.get_all('/api/v1/components'):
            sid = row['id']
            name = (row.get('name') or '').strip() or f'Component {sid}'
            mfr = self._manufacturer_map.get(_nested_id(row.get('manufacturer')))
            cat = self._category_map.get(_nested_id(row.get('category')))
            supplier = self._supplier_map.get(_nested_id(row.get('supplier')))
            tenant = self._tenant_for(row)
            qty = row.get('qty') or 0

            defaults = {
                'manufacturer': mfr,
                'category': cat,
                'supplier': supplier,
                'tenant': tenant,
                'notes': row.get('notes') or '',
                'custom_field_data': {'snipeit_id': str(sid)},
            }
            try:
                with transaction.atomic():
                    obj = Component.all_objects.filter(custom_field_data__snipeit_id=str(sid)).first()
                    if not obj:
                        obj = Component.all_objects.filter(name=name, manufacturer=mfr).first()
                    if obj:
                        if not self.update:
                            c['skipped'] += 1
                        else:
                            if not self.dry_run:
                                for field, val in defaults.items():
                                    setattr(obj, field, val)
                                obj.save()
                            c['updated'] += 1
                        # Import allocations for existing component too
                        if not self.dry_run:
                            self._import_component_allocations(obj, sid)
                        continue
                    if not self.dry_run:
                        obj = Component.objects.create(name=name, **defaults)
                        loc = Location.objects.filter(tenant=tenant).first() if tenant else None
                        if loc and qty:
                            ComponentStock.objects.create(component=obj, location=loc, qty=qty)
                        self._import_component_allocations(obj, sid)
                    else:
                        obj = Component(id=-sid, name=name, tenant=tenant)
                    c['created'] += 1

            except Exception as exc:
                self._log(f"  ! component {sid} '{name}': {exc}")
                c['failed'] += 1

        self._finish(key)

    def _import_component_allocations(self, component, snipe_id: int) -> None:
        """Import per-asset allocations for a component."""
        from inventory.models import ComponentAllocation
        try:
            data = self.client.get_all(f'/api/v1/components/{snipe_id}/assets')
            for al in data:
                asset_id = al.get('id')
                if not asset_id:
                    continue
                asset = self._asset_map.get(asset_id)
                if not asset or not asset.pk or asset.pk < 0:
                    continue
                qty = al.get('qty') or 1
                ComponentAllocation.objects.get_or_create(
                    component=component,
                    assigned_asset=asset,
                    defaults={'qty': qty, 'notes': 'Imported from Snipe-IT'},
                )
        except Exception as exc:
            logger.warning("Could not import allocations for component %s: %s", snipe_id, exc)

    # ------------------------------------------------------------------
    # Licenses + seat assignments
    # ------------------------------------------------------------------

    def _import_licenses(self) -> None:
        from licenses.models import License, LicenseSeatAssignment
        from software.models import Software
        key = 'licenses'
        self._log(f"\n[{key}]")
        c = self._counter(key)

        for row in self.client.get_all('/api/v1/licenses'):
            sid = row['id']
            name = (row.get('name') or '').strip() or f'License {sid}'
            mfr_id = _nested_id(row.get('manufacturer'))
            mfr = self._manufacturer_map.get(mfr_id)
            sw_name = (row.get('product_name') or name).strip()
            tenant = self._tenant_for(row)
            supplier = self._supplier_map.get(_nested_id(row.get('supplier')))

            seats = row.get('seats') or 1
            product_key = row.get('serial') or ''
            purchase_date = _parse_date(_nested_str(row.get('purchase_date'), 'date'))
            expiration_date = _parse_date(_nested_str(row.get('expiration_date'), 'date'))
            purchase_cost = _parse_decimal(row.get('purchase_cost'))
            order_number = (row.get('order_number') or '')[:100]
            notes = row.get('notes') or ''
            license_type = 'subscription_seat' if expiration_date else 'perpetual_seat'

            try:
                with transaction.atomic():
                    # Get or create Software
                    sw = self._software_map.get(sid)
                    if not sw:
                        if not self.dry_run:
                            sw_qs = Software.all_objects.filter(
                                name=sw_name,
                                manufacturer=mfr,
                            )
                            if mfr:
                                sw_qs = Software.all_objects.filter(name=sw_name, manufacturer=mfr)
                            else:
                                sw_qs = Software.all_objects.filter(name=sw_name)
                            sw = sw_qs.first()
                            if not sw:
                                sw = Software.objects.create(
                                    name=sw_name,
                                    manufacturer=mfr,
                                    custom_field_data={'snipeit_id': f'sw_{sid}'},
                                )
                        else:
                            sw = Software(id=-sid, name=sw_name)
                    self._software_map[sid] = sw

                    lic = License.all_objects.filter(custom_field_data__snipeit_id=str(sid)).first()
                    if not lic:
                        lic = License.all_objects.filter(name=name, software=sw, tenant=tenant).first()
                    if lic:
                        if not self.update:
                            c['skipped'] += 1
                        else:
                            if not self.dry_run:
                                lic.seats = seats
                                lic.product_key = product_key
                                lic.purchase_date = purchase_date
                                lic.expiration_date = expiration_date
                                lic.purchase_cost = purchase_cost
                                lic.order_number = order_number
                                lic.notes = notes
                                lic.license_type = license_type
                                lic.custom_field_data['snipeit_id'] = str(sid)
                                lic.save()
                            c['updated'] += 1
                        # Import seat assignments for existing licenses too
                        if not self.dry_run:
                            self._import_license_seats(lic, sid)
                        continue

                    if not self.dry_run:
                        lic = License.objects.create(
                            name=name,
                            software=sw,
                            license_type=license_type,
                            product_key=product_key,
                            seats=seats,
                            purchase_date=purchase_date,
                            expiration_date=expiration_date,
                            purchase_cost=purchase_cost,
                            order_number=order_number,
                            notes=notes,
                            supplier=supplier,
                            tenant=tenant,
                            custom_field_data={'snipeit_id': str(sid)},
                        )
                        self._import_license_seats(lic, sid)
                    else:
                        lic = License(id=-sid, name=name, software=sw, tenant=tenant)
                    c['created'] += 1

            except Exception as exc:
                self._log(f"  ! license {sid} '{name}': {exc}")
                c['failed'] += 1

        self._finish(key)

    def _import_license_seats(self, license, snipe_id: int) -> None:
        """Import seat assignments for a license."""
        from licenses.models import LicenseSeatAssignment
        try:
            data = self.client.get_all(f'/api/v1/licenses/{snipe_id}/seats')
            for seat in data:
                assigned_user = seat.get('assigned_user') or {}
                assigned_asset = seat.get('assigned_asset') or {}
                holder_id = assigned_user.get('id')
                asset_id = assigned_asset.get('id')
                holder = self._holder_map.get(holder_id) if holder_id else None
                asset = self._asset_map.get(asset_id) if asset_id else None
                if not holder and not asset:
                    continue
                if holder and holder.pk and holder.pk > 0:
                    LicenseSeatAssignment.objects.get_or_create(
                        license=license, assigned_holder=holder,
                        defaults={'notes': 'Imported from Snipe-IT'},
                    )
                elif asset and asset.pk and asset.pk > 0:
                    LicenseSeatAssignment.objects.get_or_create(
                        license=license, asset=asset,
                        defaults={'notes': 'Imported from Snipe-IT'},
                    )
        except Exception as exc:
            logger.warning("Could not import seats for license %s: %s", snipe_id, exc)

    # ------------------------------------------------------------------
    # Maintenances
    # ------------------------------------------------------------------

    def _import_maintenances(self) -> None:
        from assets.models import AssetMaintenance
        key = 'maintenances'
        self._log(f"\n[{key}]")
        c = self._counter(key)

        for row in self.client.get_all('/api/v1/maintenances'):
            sid = row['id']
            asset_id = _nested_id(row.get('asset'))
            asset = self._asset_map.get(asset_id)
            if not asset:
                c['skipped'] += 1
                continue

            raw_type = (row.get('asset_maintenance_type') or 'maintenance').lower()
            mtype = _MAINTENANCE_TYPE_MAP.get(raw_type, 'repair')
            raw_status = (row.get('completion_date') and 'complete') or (row.get('is_warranty') and 'complete') or 'pending'
            completion_raw = _nested_str(row.get('completion_date'), 'date') or row.get('completion_date')
            if isinstance(completion_raw, dict):
                completion_raw = completion_raw.get('date')
            start_raw = _nested_str(row.get('start_date'), 'date') or row.get('start_date')
            if isinstance(start_raw, dict):
                start_raw = start_raw.get('date')
            start_date = _parse_date(start_raw) or datetime.date.today()
            completion_date = _parse_date(completion_raw)
            mstatus = 'completed' if completion_date else 'scheduled'
            supplier = self._supplier_map.get(_nested_id(row.get('supplier')))
            cost = _parse_decimal(row.get('cost'))
            notes = row.get('notes') or ''

            try:
                with transaction.atomic():
                    if not self.dry_run and asset.pk and asset.pk > 0:
                        # Idempotency: match by asset + start_date + maintenance_type
                        obj = AssetMaintenance.all_objects.filter(
                            asset=asset, start_date=start_date, maintenance_type=mtype
                        ).first()
                        if obj:
                            if not self.update:
                                c['skipped'] += 1
                                continue
                            obj.maintenance_type = mtype
                            obj.status = mstatus
                            obj.completion_date = completion_date
                            obj.cost = cost
                            obj.notes = notes
                            obj.supplier = supplier
                            obj.save()
                            c['updated'] += 1
                            continue
                        AssetMaintenance.objects.create(
                            asset=asset,
                            maintenance_type=mtype,
                            status=mstatus,
                            start_date=start_date,
                            completion_date=completion_date,
                            cost=cost,
                            notes=notes,
                            supplier=supplier,
                        )
                    c['created'] += 1

            except Exception as exc:
                self._log(f"  ! maintenance {sid} (asset {asset_id}): {exc}")
                c['failed'] += 1

        self._finish(key)
