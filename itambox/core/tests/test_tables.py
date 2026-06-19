import datetime
import uuid
from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from model_bakery import baker
from assets.models import (
    AssetAssignment, Asset, AssetRole, StatusLabel,
    Warranty, WarrantyTypeChoices,
)
from organization.models import Site, Location
from core.tables import BaseTable, AssigneeColumn, ObjectChangeTable
from core.models import ObjectChange
from extras.models import NotificationChannel
from itambox.middleware import _request_id
from assets.tables import AssetTable, WarrantyTable

User = get_user_model()


class IDColumnTestCase(TestCase):
    """The universal `id` detail-link column (NetBox-style): hidden by default,
    available via the column selector, and — on tables that have no natural
    identity column — shown as the first data column linking to the detail."""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            username='idcoluser', password='password123', is_superuser=True
        )

    def _req(self, query=''):
        request = self.factory.get('/' + query)
        request.user = self.user
        return request

    def _asset(self, tag):
        role = AssetRole.objects.create(name='Role ' + tag, slug='role-' + tag.lower())
        return Asset.objects.create(name='Asset ' + tag, asset_tag=tag, asset_role=role)

    def test_id_hidden_by_default(self):
        """A table with a natural identity column (AssetTable.name) keeps id
        hidden by default, but exposes it in the column selector."""
        table = AssetTable(Asset.objects.none())
        table.configure(self._req())
        self.assertNotIn('id', [c.name for c in table.columns])  # not in visible set
        self.assertIn('id', [name for name, _verbose in table.available_columns])

    def test_id_enable_via_include_columns_is_first_data_column(self):
        asset = self._asset('IDC1')
        table = AssetTable(Asset.objects.filter(pk=asset.pk))
        table.configure(self._req('?include_columns=id'))
        visible = [c.name for c in table.columns]
        self.assertIn('id', visible)
        self.assertEqual(visible[0], 'pk')   # checkbox stays leftmost
        self.assertEqual(visible[1], 'id')   # id is the first *data* column

    def test_id_links_to_detail(self):
        asset = self._asset('IDC2')
        table = AssetTable(Asset.objects.filter(pk=asset.pk))
        table.configure(self._req('?include_columns=id'))
        cell = table.rows[0].get_cell('id')
        self.assertIn(asset.get_absolute_url(), cell)
        self.assertIn(str(asset.pk), cell)


class LifecycleTableIDColumnTest(TestCase):
    """Warranty/Reservation/Disposal lack a name column, so the id column is
    shown by default (first data column) and carries the detail link — the
    descriptive columns (type/method/reserved_for) are no longer linkified."""

    def setUp(self):
        status = baker.make(StatusLabel, type='deployable')
        self.asset = baker.make(Asset, status=status, tenant=None)
        today = datetime.date.today()
        self.warranty = baker.make(
            Warranty, asset=self.asset,
            warranty_type=WarrantyTypeChoices.HARDWARE,
            start_date=today, end_date=today + datetime.timedelta(days=30),
        )

    def test_id_visible_first_and_linked_without_configure(self):
        # Detail-view tab path uses RequestConfig only (no custom configure()).
        table = WarrantyTable(Warranty.objects.filter(pk=self.warranty.pk))
        visible = [c.name for c in table.columns]
        self.assertEqual(visible[0], 'pk')
        self.assertEqual(visible[1], 'id')
        cell = table.rows[0].get_cell('id')
        self.assertIn(self.warranty.get_absolute_url(), cell)
        self.assertIn(str(self.warranty.pk), cell)

    def test_descriptive_column_not_linkified(self):
        table = WarrantyTable(Warranty.objects.filter(pk=self.warranty.pk))
        cell = table.rows[0].get_cell('warranty_type')
        self.assertNotIn('<a ', cell)        # the link lives on the id column now
        self.assertIn('Hardware', cell)

class CoreTablesTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username='testuser', password='password123', is_superuser=True)

    def test_assignee_column_cache_build(self):
        """Test that AssigneeColumn build cache resolves without crashing for ForeignKey relationships."""
        site = Site.objects.create(name='Test Site 2', slug='test-site-2')
        location = Location.objects.create(name='Test Location 2', slug='test-location-2', site=site)
        
        role = AssetRole.objects.create(name='Desktop', slug='desktop-ac')
        asset = Asset.objects.create(name='Test Asset 2', asset_tag='TAG-AC', asset_role=role)
        
        AssetAssignment.objects.create(
            asset=asset,
            assigned_location=location,
            checked_out_by=self.user
        )
        
        column = AssigneeColumn(assignment_model_path='assets.AssetAssignment')
        table = AssetTable(Asset.objects.filter(pk=asset.pk))
        
        cache_attr = '_assignee_cache_test'
        column._build_cache(table, Asset, cache_attr)
        cache = getattr(table, cache_attr)
        
        self.assertIn(asset.pk, cache)
        self.assertEqual(cache[asset.pk], location)

    def test_assignee_column_render(self):
        """Test that AssigneeColumn renders correctly for checked-out location assets and available assets."""
        site = Site.objects.create(name='Test Site 3', slug='test-site-3')
        location = Location.objects.create(name='Test Location 3', slug='test-location-3', site=site)

        role = AssetRole.objects.create(name='Desktop', slug='desktop-ar')
        
        # Asset 1: checked out to location
        asset_checked_out = Asset.objects.create(name='Asset Checked Out', asset_tag='TAG-AR1', asset_role=role, location=location)
        AssetAssignment.objects.create(
            asset=asset_checked_out,
            assigned_location=location,
            checked_out_by=self.user
        )

        # Asset 2: available, physical location is location
        asset_available = Asset.objects.create(name='Asset Available', asset_tag='TAG-AR2', asset_role=role, location=location)

        column = AssigneeColumn(location_field='location', assignment_model_path='assets.AssetAssignment')
        table = AssetTable(Asset.objects.filter(pk__in=[asset_checked_out.pk, asset_available.pk]))

        # Render check for Asset 1 (checked out to location)
        col_bound = table.columns['assignee']
        rendered_1 = column.render(asset_checked_out.pk, asset_checked_out, col_bound, table)
        self.assertIn(f'Location: <a href="{location.get_absolute_url()}">{location.name}</a>', rendered_1)

        # Render check for Asset 2 (available)
        rendered_2 = column.render(asset_available.pk, asset_available, col_bound, table)
        self.assertEqual(rendered_2, column.EMPTY_MARK)

    def test_table_optimizations(self):
        class DummyTable(BaseTable):
            assignee = AssigneeColumn(location_field='name')
            class Meta:
                model = Location
                fields = ('name', 'assignee')

        site = Site.objects.create(name='Test Site', slug='test-site')
        Location.objects.create(name='Test Location', slug='test-location', site=site)

        # Initialize table and call internal methods
        table = DummyTable(Location.objects.all())
        table._apply_column_width_classes()
        table._apply_prefetching()

        request = self.factory.get('/')
        request.user = self.user
        table.configure(request)

        # Ensure assigning mechanism and cache builder are run
        for row in table.data:
            pass

        # ObjectChangeTable render coverage
        _request_id.set(uuid.uuid4())
        channel2 = NotificationChannel.objects.create(name='Another Channel', channel_type='webhook')
        _request_id.set(None)
        
        oc_table = ObjectChangeTable(ObjectChange.objects.all())
        for row in oc_table.rows:
            html = oc_table.render_action(row.record.action, row.record)
            self.assertIn('badge bg-', html)

        # Edge case empty ObjectChange
        oc = ObjectChange()
        self.assertIsNone(oc.get_changed_object_url())
