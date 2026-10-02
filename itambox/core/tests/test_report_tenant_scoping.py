"""B4/B5 regression: report compiler tenant scoping & correct figures.

B4: License Utilization counted soft-deleted (checked-in) seats as assigned.
B5: Software Inventory ignored active_tenant/filter_tenants, leaking every
    tenant's catalogue (and installs/licence counts) into MSP/scheduled reports.

The tests clear the ambient tenant context before compiling (the scheduled /
MSP scenario) so it is the report's explicit tenant scoping that is exercised,
not the manager's ambient scope.

F1 regression (review of PR #573): the reverse direction -- an authorized
constellation compiled while a *different* ambient tenant is bound -- must
still include every pinned tenant's real rows, because the manager truncation
runs before the provider's explicit scope filter. ``CrossTenantCompilationScopeTests``
covers that with real records and the real provider queryset.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from model_bakery import baker

from assets.models import Asset, StatusLabel
from core.tests.mixins import TenantTestMixin, compile_report_with_system_authorization, grant
from extras.models import ReportTemplate
from licenses.models import License, LicenseSeatAssignment
from organization.models import AssetHolder, Role, Tenant
from software.models import InstalledSoftware, Software

User = get_user_model()


class LicenseUtilizationReportTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="Rep Tenant", slug="rep-tenant")
        self.set_active_tenant(self.tenant)
        self.mfr = baker.make("assets.Manufacturer")
        self.software = baker.make(Software, name="Acme", manufacturer=self.mfr, tenant=self.tenant)
        self.license = baker.make(License, name="Acme EA", software=self.software, seats=10, tenant=self.tenant)
        h1, h2, h3 = (baker.make(AssetHolder, tenant=self.tenant) for _ in range(3))
        # 2 active seats + 1 checked-in (soft-deleted) seat.
        LicenseSeatAssignment.objects.create(license=self.license, assigned_holder=h1)
        LicenseSeatAssignment.objects.create(license=self.license, assigned_holder=h2)
        gone = LicenseSeatAssignment.objects.create(license=self.license, assigned_holder=h3)
        LicenseSeatAssignment.all_objects.filter(pk=gone.pk).update(deleted_at=timezone.now())

        self.template = ReportTemplate.objects.create(
            name="Lic Util",
            report_type=ReportTemplate.REPORT_TYPE_LICENSE_UTILIZATION,
            included_columns=["license_name", "seats", "assigned_seats", "available_seats"],
            include_summary_cards=True,
        )

    def test_assigned_seats_excludes_soft_deleted(self):
        self.clear_tenant_context()
        _, rows, *_ = compile_report_with_system_authorization(self.template, active_tenant=self.tenant)
        row = next(r for r in rows if r.get("License Name") == "Acme EA")
        self.assertEqual(row["Assigned Seats"], "2")  # not 3
        self.assertEqual(row["Available Seats"], "8")  # 10 - 2


class SoftwareInventoryReportTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="Tenant A", slug="soft-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="soft-b")
        self.mfr = baker.make("assets.Manufacturer")
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)

        self.set_active_tenant(self.tenant)
        self.sw_a = baker.make(Software, name="SoftA", manufacturer=self.mfr, tenant=self.tenant)
        # One install of SoftA on a tenant-A asset (a cross-tenant install is
        # rejected by InstalledSoftware.clean, so the count is inherently
        # tenant-scoped; this just exercises the scoped count path).
        asset_a = baker.make(Asset, tenant=self.tenant, asset_tag="A-1", status=self.status)
        InstalledSoftware.objects.create(software=self.sw_a, asset=asset_a)

        self.sw_b = baker.make(Software, name="SoftB", manufacturer=self.mfr, tenant=self.tenant_b)

        self.template = ReportTemplate.objects.create(
            name="Soft Inv",
            report_type=ReportTemplate.REPORT_TYPE_SOFTWARE_INVENTORY,
            included_columns=["software_name", "manufacturer", "installed_count"],
            include_summary_cards=True,
        )

    def test_inventory_scoped_to_report_tenant(self):
        self.clear_tenant_context()
        _, rows, summary_cards, *_ = compile_report_with_system_authorization(self.template, active_tenant=self.tenant)
        names = [r.get("Software Product") for r in rows]
        self.assertIn("SoftA", names)
        self.assertNotIn("SoftB", names)

        total = next(c["value"] for c in summary_cards if c["label"] == "Total Software Products")
        self.assertEqual(total, "1")

        row_a = next(r for r in rows if r.get("Software Product") == "SoftA")
        self.assertEqual(row_a["Installed Count"], "1")


class CrossTenantCompilationScopeTests(TenantTestMixin, TestCase):
    """F1 regression: an authorized constellation survives ambient scoping.

    The tenant manager truncates every scoped queryset to the ambient request
    tenant *before* the provider's explicit ``scope_to_tenants`` filter can
    run, so a constellation that differs from the active tenant used to lose
    the other tenants' rows. These tests compile with the ambient tenant set
    (the preview/download/worker situation) against real provider querysets.
    """

    def setUp(self):
        self.setup_tenant_context(
            name="Tenant A",
            slug="cross-a",
            permissions=["reports.view_cross_tenant_reports", "assets.view_asset"],
        )
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="cross-b")
        role_b = Role.objects.create(
            tenant=self.tenant_b,
            name="Cross Role",
            permissions=["reports.view_cross_tenant_reports", "assets.view_asset"],
        )
        grant(self.tenant_user, self.tenant_b, role_b)
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        self.asset_a = baker.make(Asset, tenant=self.tenant, asset_tag="A-1", status=self.status)
        self.asset_b = baker.make(Asset, tenant=self.tenant_b, asset_tag="B-1", status=self.status)
        # A third tenant the principal is not authorized for: its rows must
        # never appear in an authorized constellation's compile.
        self.tenant_c = Tenant.objects.create(name="Tenant C", slug="cross-c")
        self.asset_c = baker.make(Asset, tenant=self.tenant_c, asset_tag="C-1", status=self.status)
        self.template = ReportTemplate.objects.create(
            name="Cross Scope",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            included_columns=["asset_tag"],
            include_summary_cards=False,
        )

    def tearDown(self):
        self.clear_tenant_context()
        # inline import: app-registry: middleware pulls in request machinery.
        from itambox.middleware import set_current_user

        set_current_user(None)
        super().tearDown()

    def _compile_tags(self, *, filter_tenants, active_tenant=None, user=None):
        # inline import: app-registry: middleware pulls in request machinery.
        from itambox.middleware import get_current_user, set_current_user

        previous_user = get_current_user()
        set_current_user(user if user is not None else self.tenant_user)
        try:
            _, rows, *_ = compile_report_with_system_authorization(
                self.template, active_tenant=active_tenant, filter_tenants=filter_tenants
            )
        finally:
            set_current_user(previous_user)
        return [row.get("Asset Tag") for row in rows]

    def test_single_pinned_tenant_compiles_with_a_different_active_tenant(self):
        self.set_active_tenant(self.tenant)
        tags = self._compile_tags(filter_tenants=[self.tenant_b], active_tenant=self.tenant)
        self.assertEqual(tags, ["B-1"])

    def test_multi_tenant_constellation_compiles_every_pinned_tenant(self):
        self.set_active_tenant(self.tenant)
        tags = self._compile_tags(filter_tenants=[self.tenant, self.tenant_b], active_tenant=self.tenant)
        self.assertEqual(sorted(tags), ["A-1", "B-1"])
        self.assertNotIn("C-1", tags)

    def test_worker_task_context_compiles_every_pinned_tenant(self):
        # The scheduled path compiles under a TaskContext bound to the
        # schedule's owning tenant; the aggregation must still include the
        # second pinned tenant's rows.
        # inline import: app-registry: the task context is only needed here.
        from core.tasks.context import TaskContext

        with TaskContext(tenant_id=self.tenant.pk, user_id=self.tenant_user.pk):
            tags = self._compile_tags(filter_tenants=[self.tenant, self.tenant_b], active_tenant=self.tenant)
        self.assertEqual(sorted(tags), ["A-1", "B-1"])
        self.assertNotIn("C-1", tags)

    def test_unauthorized_multi_tenant_compilation_fails_closed(self):
        plain_user = User.objects.create_user(username="plain-cross", password="password")
        plain_role = Role.objects.create(tenant=self.tenant, name="Plain Role", permissions=[])
        grant(plain_user, self.tenant, plain_role)
        self.set_active_tenant(self.tenant)
        with self.assertRaises(PermissionError):
            self._compile_tags(filter_tenants=[self.tenant, self.tenant_b], active_tenant=self.tenant, user=plain_user)

    def test_unauthorized_single_pinned_tenant_fails_closed(self):
        plain_user = User.objects.create_user(username="plain-single", password="password")
        plain_role = Role.objects.create(tenant=self.tenant, name="Plain Single Role", permissions=[])
        grant(plain_user, self.tenant, plain_role)
        self.set_active_tenant(self.tenant)
        with self.assertRaises(PermissionError):
            self._compile_tags(filter_tenants=[self.tenant_b], active_tenant=self.tenant, user=plain_user)
