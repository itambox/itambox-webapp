from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from core.errors import IntegrationContext, RetryBudget
from core.importers.snipeit import SnipeITClient
from core.importers.snipeit.contracts import ImportContext, StageReporter
from core.importers.snipeit.stages.organization import (
    CompanyDependencies,
    CompanyImporter,
    LocationDependencies,
    LocationImporter,
    UserDependencies,
    UserImporter,
)
from core.managers import get_current_tenant
from core.tasks.context import TaskContext
from core.tests.mixins import TenantTestMixin


def _make_client(payloads: dict[str, dict]) -> SnipeITClient:
    client = SnipeITClient.__new__(SnipeITClient)
    client.base_url = "https://snipe.example"
    client.PAGE_SIZE = 500
    client.context = IntegrationContext(provider="snipe-it", operation="test")
    client.retry_budget_factory = RetryBudget

    def fake_get(endpoint, params=None, **kwargs):
        path = endpoint.split("?")[0]
        for prefix, payload in payloads.items():
            if path == prefix or path.startswith(prefix + "/"):
                return payload
        return {"total": 0, "rows": []}

    client._get = MagicMock(side_effect=fake_get)
    return client


class TestOrganizationStages(TenantTestMixin):
    @pytest.fixture(autouse=True)
    def _setup(self, db):
        self.setup_tenant_context(name="Acme", slug="acme")
        self.admin = self.tenant_admin

    def _context(self, payloads, *, dry_run=False, update=False, map_companies=False):
        return ImportContext(
            client=_make_client(payloads),
            default_tenant=self.tenant,
            user=self.admin,
            dry_run=dry_run,
            update=update,
            map_companies=map_companies,
            reporter=StageReporter(StringIO(), default_tenant=self.tenant, user=self.admin),
        )

    def _run(self, stage):
        with TaskContext(tenant_id=self.tenant.pk, user_id=self.admin.pk):
            return stage.run()

    def _run_global(self, stage):
        with TaskContext():
            return stage.run()

    def test_companies_create_rerun_skip_dry_run_and_publish_map(self):
        from organization.models import Tenant

        payloads = {"/api/v1/companies": {"total": 1, "rows": [{"id": 100, "name": "Globex"}]}}
        first_map = {}
        first = self._run_global(CompanyImporter(self._context(payloads), CompanyDependencies(first_map)))

        tenant = Tenant.all_objects.get(name="Globex")
        assert first.counts.created == 1
        assert first_map[100] == tenant

        rerun_map = {}
        rerun = self._run_global(CompanyImporter(self._context(payloads), CompanyDependencies(rerun_map)))
        assert rerun.counts.skipped == 1
        assert rerun_map[100] == tenant
        assert Tenant.all_objects.filter(name="Globex").count() == 1

        dry_payloads = {"/api/v1/companies": {"total": 1, "rows": [{"id": 101, "name": "Dry Globex"}]}}
        dry_map = {}
        dry = self._run_global(
            CompanyImporter(
                self._context(dry_payloads, dry_run=True),
                CompanyDependencies(dry_map),
            )
        )
        assert dry.counts.created == 1
        assert dry_map[101].id == -101
        assert dry_map[101].name == "Dry Globex"
        assert not Tenant.all_objects.filter(name="Dry Globex").exists()

    def test_locations_parent_child_two_pass_and_rerun_idempotency(self):
        from organization.models import Location, Site

        rows = [
            {"id": 11, "name": "Floor 1", "parent": {"id": 10}},
            {"id": 10, "name": "HQ", "parent": None},
        ]
        payloads = {"/api/v1/locations": {"total": 2, "rows": rows}}
        first_map = {}
        first = self._run(
            LocationImporter(
                self._context(payloads),
                LocationDependencies({}, first_map),
            )
        )

        parent = Location.all_objects.get(name="HQ")
        child = Location.all_objects.get(name="Floor 1")
        assert first.counts.created == 2
        assert child.parent == parent
        assert first_map == {10: parent, 11: child}
        assert Site.all_objects.filter(name="Imported (Snipe-IT)").count() == 1

        rerun_map = {}
        rerun = self._run(
            LocationImporter(
                self._context(payloads),
                LocationDependencies({}, rerun_map),
            )
        )
        assert rerun.counts.skipped == 2
        assert rerun_map == {10: parent, 11: child}
        assert Location.all_objects.filter(name__in=["HQ", "Floor 1"]).count() == 2
        assert Site.all_objects.filter(name="Imported (Snipe-IT)").count() == 1

    def test_locations_update_refreshes_parent_and_custom_field_data(self):
        from organization.models import Location

        rows = [
            {"id": 10, "name": "HQ", "parent": None},
            {"id": 11, "name": "Floor 1", "parent": {"id": 10}},
        ]
        payloads = {"/api/v1/locations": {"total": 2, "rows": rows}}
        self._run(LocationImporter(self._context(payloads), LocationDependencies({}, {})))
        child = Location.all_objects.get(name="Floor 1")
        child.parent = None
        child.custom_field_data = {}
        child.save(update_fields=["parent", "custom_field_data"])

        updated_map = {}
        result = self._run(
            LocationImporter(
                self._context(payloads, update=True),
                LocationDependencies({}, updated_map),
            )
        )

        child.refresh_from_db()
        assert result.counts.updated == 2
        assert child.parent.name == "HQ"
        assert child.custom_field_data == {"snipeit_id": "11"}
        assert updated_map[11] == child

    def test_locations_failed_parent_fails_child_without_creating_root(self):
        from organization.models import Location

        rows = [
            {"id": 10, "name": "Parent", "parent": None},
            {"id": 11, "name": "Child", "parent": {"id": 10}},
        ]
        payloads = {"/api/v1/locations": {"total": 2, "rows": rows}}
        locations = {}
        with patch.object(Location.objects, "create", side_effect=RuntimeError("database failure")):
            result = self._run(
                LocationImporter(
                    self._context(payloads),
                    LocationDependencies({}, locations),
                )
            )

        assert result.counts.failed == 2
        assert locations == {}
        assert not Location.all_objects.filter(name="Child").exists()

    def test_locations_dry_run_publishes_negative_objects_without_rows(self):
        from organization.models import Location, Site

        rows = [
            {"id": 10, "name": "HQ", "parent": None},
            {"id": 11, "name": "Floor 1", "parent": {"id": 10}},
        ]
        payloads = {"/api/v1/locations": {"total": 2, "rows": rows}}
        locations = {}
        result = self._run(
            LocationImporter(
                self._context(payloads, dry_run=True),
                LocationDependencies({}, locations),
            )
        )

        assert result.counts.created == 2
        assert locations[10].id == -10
        assert locations[11].id == -11
        assert locations[11].parent is None
        assert locations[10].site.id == -1
        assert Location.all_objects.count() == 0
        assert Site.all_objects.count() == 0

    def test_import_site_restores_tenant_after_exception(self):
        from organization.models import Site

        context = self._context({"/api/v1/locations": {"total": 0, "rows": []}})
        stage = LocationImporter(context, LocationDependencies({}, {}))
        with self.tenant_context(self.tenant):
            with patch.object(Site.objects, "create", side_effect=RuntimeError("site failure")):
                with pytest.raises(RuntimeError):
                    stage.run()
            assert get_current_tenant() == self.tenant

    def test_users_create_fallback_upns_rerun_update_and_dry_run(self):
        from organization.models import AssetHolder

        rows = [
            {
                "id": 5,
                "username": "jdoe",
                "first_name": "John",
                "last_name": "Doe",
                "email": "jdoe@example.com",
            },
            {
                "id": 6,
                "username": "",
                "first_name": "Email",
                "last_name": "User",
                "email": "email@example.com",
            },
            {
                "id": 7,
                "username": "",
                "first_name": "Imported",
                "last_name": "User",
                "email": "",
            },
        ]
        payloads = {"/api/v1/users": {"total": 3, "rows": rows}}
        first_map = {}
        first = self._run(UserImporter(self._context(payloads), UserDependencies({}, first_map)))

        assert first.counts.created == 3
        assert {holder.upn for holder in first_map.values()} == {"jdoe", "email@example.com", "imported-user-7"}
        assert AssetHolder.all_objects.filter(custom_field_data__snipeit_id="5").exists()

        rerun_map = {}
        rerun = self._run(UserImporter(self._context(payloads), UserDependencies({}, rerun_map)))
        assert rerun.counts.skipped == 3
        assert rerun_map[5].id == first_map[5].id
        assert AssetHolder.all_objects.count() == 3

        updated_rows = [
            {
                "id": 5,
                "username": "new-jdoe",
                "first_name": "Updated",
                "last_name": "Person",
                "email": "updated@example.com",
            }
        ]
        updated_map = {}
        updated = self._run(
            UserImporter(
                self._context({"/api/v1/users": {"total": 1, "rows": updated_rows}}, update=True),
                UserDependencies({}, updated_map),
            )
        )
        holder = AssetHolder.all_objects.get(custom_field_data__snipeit_id="5")
        assert updated.counts.updated == 1
        assert holder.upn == "new-jdoe"
        assert holder.first_name == "Updated"
        assert holder.email == "updated@example.com"
        assert updated_map[5] == holder

        dry_map = {}
        dry = self._run(
            UserImporter(
                self._context(
                    {"/api/v1/users": {"total": 1, "rows": [{"id": 8, "username": "dry", "email": ""}]}},
                    dry_run=True,
                ),
                UserDependencies({}, dry_map),
            )
        )
        assert dry.counts.created == 1
        assert dry_map[8].id == -8
        assert dry_map[8].upn == "dry"
        assert AssetHolder.all_objects.count() == 3
