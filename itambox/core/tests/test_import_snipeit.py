"""
Tests for the Snipe-IT importer (core/importers/snipeit.py).

No network access: all HTTP calls are intercepted via unittest.mock.patch on
SnipeITClient._get, which backs every paginated get_all() call.
"""
from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model

from core.tests.mixins import TenantTestMixin
from core.importers.snipeit import SnipeITClient, SnipeITImporter, _clean_field_name

User = get_user_model()

# ---------------------------------------------------------------------------
# Fixtures shared by multiple tests
# ---------------------------------------------------------------------------

# Minimal Snipe-IT API payloads. Pagination is simulated by returning 'total'
# equal to the number of rows so a single page is sufficient.

SNIPE_STATUS_LABELS = {
    'total': 2,
    'rows': [
        {'id': 1, 'name': 'Ready to Deploy', 'type': 'deployable'},
        {'id': 2, 'name': 'In Use',           'type': 'deployed'},
    ],
}
SNIPE_MANUFACTURERS = {
    'total': 1,
    'rows': [{'id': 1, 'name': 'Acme Corp'}],
}
SNIPE_CATEGORIES = {
    'total': 1,
    'rows': [{'id': 1, 'name': 'Laptops', 'category_type': 'asset'}],
}
SNIPE_SUPPLIERS = {
    'total': 1,
    'rows': [{'id': 1, 'name': 'TechSupply', 'email': 'sales@techsupply.com', 'phone': '', 'url': '', 'contact': '', 'notes': ''}],
}
SNIPE_LOCATIONS = {
    'total': 2,
    'rows': [
        {'id': 10, 'name': 'HQ',       'parent': None},
        {'id': 11, 'name': 'Floor 1',  'parent': {'id': 10}},
    ],
}
SNIPE_USERS = {
    'total': 1,
    'rows': [
        {'id': 5, 'username': 'jdoe', 'first_name': 'John', 'last_name': 'Doe',
         'email': 'jdoe@example.com', 'company': None},
    ],
}
SNIPE_FIELDS = {
    'total': 1,
    'rows': [
        {'id': 3, 'name': 'CPU Model', 'db_column_name': '_snipeit_cpu_model_3',
         'format': 'TEXT', 'field_values': None, 'type': 'text'},
    ],
}
SNIPE_FIELDSETS = {
    'total': 1,
    'rows': [
        {'id': 2, 'name': 'Laptop Specs', 'fields': {'rows': [
            {'db_column_name': '_snipeit_cpu_model_3', 'id': 3}
        ]}},
    ],
}
SNIPE_MODELS = {
    'total': 1,
    'rows': [
        {'id': 7, 'name': 'ThinkPad X1',
         'manufacturer': {'id': 1}, 'category': {'id': 1},
         'fieldset': {'id': 2}, 'eol': 36, 'model_number': 'TP-X1'},
    ],
}
SNIPE_HARDWARE = {
    'total': 1,
    'rows': [
        {
            'id': 42,
            'asset_tag': 'NW-0001',
            'serial': 'SN-ABC123',
            'name': 'Alice Laptop',
            'model': {'id': 7},
            'status_label': {'id': 1},
            'supplier': {'id': 1},
            'location': {'id': 10},
            'rtd_location': None,
            'purchase_date': {'date': '2023-01-15'},
            'purchase_cost': '1299.00',
            'order_number': 'PO-2023-0001',
            'notes': 'Primary device',
            'warranty_months': 36,
            'company': None,
            'assigned_to': None,
            'custom_fields': {
                'CPU Model': {
                    'field': '_snipeit_cpu_model_3',
                    'value': 'Intel i7-1270P',
                    'field_format': 'TEXT',
                },
            },
        },
    ],
}
SNIPE_HARDWARE_CHECKED_OUT = {
    'total': 1,
    'rows': [
        {
            'id': 43,
            'asset_tag': 'NW-0002',
            'serial': 'SN-DEF456',
            'name': 'Bob Laptop',
            'model': {'id': 7},
            'status_label': {'id': 1},
            'supplier': None,
            'location': None,
            'rtd_location': None,
            'purchase_date': None,
            'purchase_cost': None,
            'order_number': '',
            'notes': '',
            'warranty_months': None,
            'company': None,
            'assigned_to': {'id': 5, 'type': 'user'},
            'custom_fields': {},
        },
    ],
}
SNIPE_COMPANIES = {
    'total': 2,
    'rows': [
        {'id': 100, 'name': 'Acme Corp'},
        {'id': 101, 'name': 'Globex'},
    ],
}
SNIPE_ACCESSORIES    = {'total': 0, 'rows': []}
SNIPE_CONSUMABLES    = {'total': 0, 'rows': []}
SNIPE_COMPONENTS     = {'total': 0, 'rows': []}
SNIPE_LICENSES       = {'total': 0, 'rows': []}
SNIPE_MAINTENANCES   = {'total': 0, 'rows': []}


def _make_client_mock(pages: dict | None = None) -> SnipeITClient:
    """
    Return a SnipeITClient whose _get is patched to return fixture pages.

    `pages` maps endpoint prefixes to response dicts.  Defaults to the
    standard single-asset scenario.
    """
    defaults = {
        '/api/v1/statuslabels': SNIPE_STATUS_LABELS,
        '/api/v1/manufacturers': SNIPE_MANUFACTURERS,
        '/api/v1/categories': SNIPE_CATEGORIES,
        '/api/v1/suppliers': SNIPE_SUPPLIERS,
        '/api/v1/locations': SNIPE_LOCATIONS,
        '/api/v1/users': SNIPE_USERS,
        '/api/v1/fields': SNIPE_FIELDS,
        '/api/v1/fieldsets': SNIPE_FIELDSETS,
        '/api/v1/models': SNIPE_MODELS,
        '/api/v1/hardware': SNIPE_HARDWARE,
        '/api/v1/accessories': SNIPE_ACCESSORIES,
        '/api/v1/consumables': SNIPE_CONSUMABLES,
        '/api/v1/components': SNIPE_COMPONENTS,
        '/api/v1/licenses': SNIPE_LICENSES,
        '/api/v1/maintenances': SNIPE_MAINTENANCES,
    }
    if pages:
        defaults.update(pages)

    client = SnipeITClient.__new__(SnipeITClient)
    client.base_url = 'https://snipe.example'
    client.PAGE_SIZE = 500

    def fake_get(endpoint, params=None, _retries=0):
        path = endpoint.split('?')[0]
        for prefix, data in defaults.items():
            if path == prefix or path.startswith(prefix + '/'):
                return data
        return {'total': 0, 'rows': []}

    client._get = fake_get
    return client


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_clean_field_name_strips_prefix_and_id(self):
        assert _clean_field_name('_snipeit_cpu_model_3') == 'cpu_model'

    def test_clean_field_name_no_prefix(self):
        assert _clean_field_name('hostname') == 'hostname'

    def test_clean_field_name_trailing_large_id(self):
        assert _clean_field_name('_snipeit_department_123') == 'department'


# ---------------------------------------------------------------------------
# Integration tests (hit a real test DB, no network)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSnipeITImporter(TenantTestMixin):

    @pytest.fixture(autouse=True)
    def _setup(self, db):
        self.setup_tenant_context(name='Acme', slug='acme')
        self.admin = User.objects.create_superuser(
            username='impadmin', email='impadmin@example.com', password='pw')

    def _run(self, pages=None, dry_run=False, update=False, map_companies=False, skip=None):
        from core.tasks.context import TaskContext
        client = _make_client_mock(pages)
        with TaskContext(tenant_id=self.tenant.pk, user_id=self.admin.pk):
            importer = SnipeITImporter(
                client=client,
                tenant=self.tenant,
                user=self.admin,
                dry_run=dry_run,
                update=update,
                map_companies=map_companies,
                skip=skip or set(),
            )
            return importer.run()

    # ------------------------------------------------------------------
    # Basic import
    # ------------------------------------------------------------------

    def test_basic_import_creates_records(self):
        from assets.models import StatusLabel, Manufacturer, Category, Asset
        from organization.models import AssetHolder, Location
        from extras.models import CustomField, CustomFieldset

        counts = self._run()

        assert StatusLabel._base_manager.filter(name='Ready to Deploy').exists()
        assert StatusLabel._base_manager.filter(name='In Use').exists()
        assert Manufacturer._base_manager.filter(name='Acme Corp').exists()
        assert Category._base_manager.filter(name='Laptops').exists()
        assert Location._base_manager.filter(name='HQ').exists()
        assert Location._base_manager.filter(name='Floor 1').exists()
        assert AssetHolder._base_manager.filter(upn='jdoe').exists()
        assert CustomField._base_manager.filter(name='cpu_model').exists()
        assert CustomFieldset._base_manager.filter(name='Laptop Specs').exists()
        assert Asset._base_manager.filter(asset_tag='NW-0001').exists()

        assert counts['assets']['created'] == 1
        assert counts['users']['created'] == 1

    def test_custom_field_value_stored_in_custom_field_data(self):
        from assets.models import Asset

        self._run()
        asset = Asset._base_manager.get(asset_tag='NW-0001')
        assert asset.custom_field_data.get('cpu_model') == 'Intel i7-1270P'
        assert asset.custom_field_data.get('snipeit_id') == '42'

    # ------------------------------------------------------------------
    # Parent-child location hierarchy
    # ------------------------------------------------------------------

    def test_location_parent_wired_up(self):
        from organization.models import Location

        self._run()
        parent = Location._base_manager.get(name='HQ')
        child = Location._base_manager.get(name='Floor 1')
        assert child.parent == parent

    # ------------------------------------------------------------------
    # Idempotency: run twice → same counts, no duplicates
    # ------------------------------------------------------------------

    def test_idempotent_rerun_no_duplicates(self):
        from assets.models import Asset, StatusLabel, Manufacturer

        self._run()
        counts2 = self._run()

        assert Asset._base_manager.filter(asset_tag='NW-0001').count() == 1
        assert StatusLabel._base_manager.filter(name='Ready to Deploy').count() == 1
        assert Manufacturer._base_manager.filter(name='Acme Corp').count() == 1
        # Second run should have zero created (everything already exists)
        assert counts2['assets']['created'] == 0
        assert counts2['assets']['skipped'] == 1

    # ------------------------------------------------------------------
    # --update syncs fields
    # ------------------------------------------------------------------

    def test_update_flag_refreshes_existing_records(self):
        from assets.models import Asset

        self._run()
        # Modify locally
        asset = Asset._base_manager.get(asset_tag='NW-0001')
        asset.notes = 'Overwritten locally'
        asset.save(update_fields=['notes'])

        # Re-run with --update
        self._run(update=True)
        asset.refresh_from_db()
        assert asset.notes == 'Primary device'

    # ------------------------------------------------------------------
    # Checkout flips status to deployed type
    # ------------------------------------------------------------------

    def test_checkout_creates_assignment(self):
        from assets.models import Asset, AssetAssignment

        self._run(pages={'/api/v1/hardware': SNIPE_HARDWARE_CHECKED_OUT})

        asset = Asset._base_manager.get(asset_tag='NW-0002')
        assert asset.active_assignment is not None
        assert asset.active_assignment.assigned_user is not None

    def test_checked_out_asset_gets_deployed_status(self):
        from assets.models import Asset
        from assets.choices import StatusTypeChoices

        self._run(pages={'/api/v1/hardware': SNIPE_HARDWARE_CHECKED_OUT})

        asset = Asset._base_manager.get(asset_tag='NW-0002')
        assert asset.status.type == StatusTypeChoices.DEPLOYED

    # ------------------------------------------------------------------
    # Dry-run writes nothing
    # ------------------------------------------------------------------

    def test_dry_run_writes_nothing(self):
        from assets.models import Asset, Manufacturer, StatusLabel
        from organization.models import AssetHolder, Location

        # Capture baseline counts (migration seeds pre-existing status labels)
        sl_before = StatusLabel._base_manager.count()

        self._run(dry_run=True)

        assert Asset._base_manager.count() == 0
        assert Manufacturer._base_manager.count() == 0
        assert StatusLabel._base_manager.count() == sl_before  # no new ones created
        assert AssetHolder._base_manager.count() == 0
        assert Location._base_manager.count() == 0

    def test_dry_run_returns_nonzero_created_counts(self):
        counts = self._run(dry_run=True)
        assert counts['assets']['created'] == 1
        # "Ready to Deploy" is new; "In Use" already exists from migration seed → skipped
        assert counts['statuslabels']['created'] == 1
        assert counts['statuslabels']['skipped'] == 1

    # ------------------------------------------------------------------
    # --map-companies-to-tenants
    # ------------------------------------------------------------------

    def test_map_companies_creates_tenants(self):
        from organization.models import Tenant

        before = Tenant._base_manager.count()
        self._run(map_companies=True, pages={
            '/api/v1/companies': SNIPE_COMPANIES,
            '/api/v1/hardware': SNIPE_HARDWARE,
        })
        after = Tenant._base_manager.count()
        assert after >= before + 2

    def test_no_map_companies_does_not_create_extra_tenants(self):
        from organization.models import Tenant

        before = Tenant._base_manager.count()
        self._run(map_companies=False)
        after = Tenant._base_manager.count()
        assert after == before  # default tenant already exists; none created

    # ------------------------------------------------------------------
    # --skip
    # ------------------------------------------------------------------

    def test_skip_assets_does_not_create_assets(self):
        from assets.models import Asset

        self._run(skip={'assets'})
        assert Asset._base_manager.count() == 0

    # ------------------------------------------------------------------
    # Pagination: two pages → all rows imported
    # ------------------------------------------------------------------

    def test_pagination_imports_all_rows(self):
        from assets.models import StatusLabel

        two_page_labels = {
            'total': 4,
            'rows': [
                {'id': 10, 'name': 'Status A', 'type': 'deployable'},
                {'id': 11, 'name': 'Status B', 'type': 'pending'},
            ],
        }
        page2 = {
            'total': 4,
            'rows': [
                {'id': 12, 'name': 'Status C', 'type': 'undeployable'},
                {'id': 13, 'name': 'Status D', 'type': 'archived'},
            ],
        }
        def fake_get(endpoint, params=None, _retries=0):
            if endpoint == '/api/v1/statuslabels':
                if (params or {}).get('offset', 0) == 0:
                    return two_page_labels
                return {**page2, 'rows': page2['rows']}
            return {'total': 0, 'rows': []}

        from core.tasks.context import TaskContext
        client = SnipeITClient.__new__(SnipeITClient)
        client.base_url = 'https://snipe.example'
        client.PAGE_SIZE = 2
        client._get = fake_get

        with TaskContext(tenant_id=self.tenant.pk, user_id=self.admin.pk):
            importer = SnipeITImporter(
                client=client,
                tenant=self.tenant,
                user=self.admin,
                dry_run=False,
                skip={'assets', 'accessories', 'consumables', 'components', 'licenses', 'maintenances'},
            )
            importer.run()

        assert StatusLabel._base_manager.filter(name__in=['Status A', 'Status B', 'Status C', 'Status D']).count() == 4
