import datetime

from django.test import TestCase

from assets.models import Asset, AssetType, Manufacturer, Supplier
from core.managers import Scope, set_current_tenant
from licenses.models import License, LicenseSeatAssignment
from organization.models import Tenant
from software.models import InstalledSoftware, Software
from subscriptions.models import Subscription, SubscriptionStatusChoices
from subscriptions.tasks import check_subscription_expiries_and_reminders


class SoftwareLicenseSubscriptionScopingTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name="Vendor", slug="vendor")
        self.supplier = Supplier.objects.create(name="Reseller", is_active=True)
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        self.sw_a = Software.objects.create(name="SW A", manufacturer=self.manufacturer, tenant=self.tenant_a)
        self.sw_b = Software.objects.create(name="SW B", manufacturer=self.manufacturer, tenant=self.tenant_b)
        self.sw_global = Software.objects.create(name="SW Global", manufacturer=self.manufacturer, tenant=None)
        self.lic_a = License.objects.create(name="Lic A", software=self.sw_a, seats=3, tenant=self.tenant_a)
        self.lic_b = License.objects.create(name="Lic B", software=self.sw_b, seats=3, tenant=self.tenant_b)
        today = datetime.date.today()
        self.sub_a = Subscription.objects.create(
            name="Sub A",
            supplier=self.supplier,
            tenant=self.tenant_a,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_date=today - datetime.timedelta(days=5),
        )
        self.sub_b = Subscription.objects.create(
            name="Sub B",
            supplier=self.supplier,
            tenant=self.tenant_b,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_date=today + datetime.timedelta(days=300),
        )

    def tearDown(self):
        set_current_tenant(None)

    def test_scoped_reads_follow_the_explicit_scope(self):
        set_current_tenant(self.tenant_a)
        scope = Scope.current()
        software = list(Software.objects.for_scope(scope))
        self.assertIn(self.sw_a, software)
        self.assertIn(self.sw_global, software)
        self.assertNotIn(self.sw_b, software)
        self.assertEqual(list(License.objects.for_scope(scope)), [self.lic_a])
        self.assertEqual(list(Subscription.objects.for_scope(scope)), [self.sub_a])

    def test_installs_and_seats_scope_through_parent(self):
        asset_type = AssetType.objects.create(name="Laptop", slug="laptop-t", manufacturer=self.manufacturer)
        asset_a = Asset.objects.create(asset_tag="A1", asset_type=asset_type, tenant=self.tenant_a)
        asset_b = Asset.objects.create(asset_tag="B1", asset_type=asset_type, tenant=self.tenant_b)
        inst_a = InstalledSoftware.objects.create(asset=asset_a, software=self.sw_a)
        inst_b = InstalledSoftware.objects.create(asset=asset_b, software=self.sw_b)
        seat_a = LicenseSeatAssignment.objects.create(license=self.lic_a, asset=asset_a)
        seat_b = LicenseSeatAssignment.objects.create(license=self.lic_b, asset=asset_b)
        set_current_tenant(self.tenant_a)
        scope = Scope.current()
        self.assertEqual(list(InstalledSoftware.objects.for_scope(scope)), [inst_a])
        self.assertEqual(list(LicenseSeatAssignment.objects.for_scope(scope)), [seat_a])
        self.assertNotIn(inst_b, InstalledSoftware.objects.for_scope(scope))
        self.assertNotIn(seat_b, LicenseSeatAssignment.objects.for_scope(scope))
        self.assertEqual(self.sw_a.installed_count, 1)

    def test_expiry_task_enumerates_every_tenant_under_a_bound_scope(self):
        set_current_tenant(self.tenant_b)
        check_subscription_expiries_and_reminders()
        set_current_tenant(None)
        self.sub_a.refresh_from_db()
        self.sub_b.refresh_from_db()
        self.assertEqual(self.sub_a.status, SubscriptionStatusChoices.EXPIRED)
        self.assertEqual(self.sub_b.status, SubscriptionStatusChoices.ACTIVE)
