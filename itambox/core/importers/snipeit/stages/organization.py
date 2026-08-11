from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass

from django.apps import apps
from django.db import transaction

from core.importers.snipeit.common import _nested_id, _unique_slug, tenant_for
from core.importers.snipeit.contracts import ImportContext, StageResult
from core.managers import get_current_tenant, set_current_tenant


@dataclass(frozen=True)
class CompanyDependencies:
    tenants: MutableMapping[int, object]


@dataclass(frozen=True)
class LocationDependencies:
    tenants: Mapping[int, object]
    locations: MutableMapping[int, object]


@dataclass(frozen=True)
class UserDependencies:
    tenants: Mapping[int, object]
    holders: MutableMapping[int, object]


class CompanyImporter:
    key = "companies"
    endpoint = "/api/v1/companies"

    def __init__(self, context: ImportContext, dependencies: CompanyDependencies) -> None:
        self.context = context
        self.dependencies = dependencies

    def run(self) -> StageResult:
        Tenant = apps.get_model("organization", "Tenant")
        result = StageResult(self.key)
        self.context.reporter.start(result)
        for row in self.context.client.get_all(self.endpoint):
            sid = row["id"]
            name = (row.get("name") or "").strip() or f"Company {sid}"
            try:
                with transaction.atomic():
                    obj = Tenant.all_objects.filter(name=name).first()
                    if obj:
                        outcome = "skipped"
                    elif not self.context.dry_run:
                        obj = Tenant.objects.create(name=name, slug=_unique_slug(Tenant, name))
                        outcome = "created"
                    else:
                        obj = Tenant(id=-sid, name=name)
                        outcome = "created"
                self.dependencies.tenants[sid] = obj
                result.counts.record(outcome)
            # broad except: task-isolation: one remote row must not abort the reviewed import batch
            except Exception as exc:
                self.context.reporter.row_failure(result, "companies.persist", exc)
        self.context.reporter.finish(result)
        return result


class LocationImporter:
    key = "locations"
    endpoint = "/api/v1/locations"

    def __init__(self, context: ImportContext, dependencies: LocationDependencies) -> None:
        self.context = context
        self.dependencies = dependencies

    def run(self) -> StageResult:
        Location = apps.get_model("organization", "Location")
        Site = apps.get_model("organization", "Site")
        result = StageResult(self.key)
        self.context.reporter.start(result)

        import_site = self._import_site(Site)
        rows = list(self.context.client.get_all(self.endpoint))

        for row in rows:
            if not _nested_id(row.get("parent")):
                try:
                    self._upsert_location(row, import_site, result, Location)
                # broad except: task-isolation: one remote row must not abort the reviewed import batch
                except Exception as exc:
                    self.context.reporter.row_failure(result, "locations.pass1", exc)

        for row in rows:
            if _nested_id(row.get("parent")):
                try:
                    self._upsert_location(row, import_site, result, Location)
                # broad except: task-isolation: one remote row must not abort the reviewed import batch
                except Exception as exc:
                    self.context.reporter.row_failure(result, "locations.pass2", exc)

        self.context.reporter.finish(result)
        return result

    def _upsert_location(self, row: dict, import_site, result: StageResult, Location) -> None:
        sid = row["id"]
        name = (row.get("name") or "").strip() or f"Location {sid}"
        parent_id = _nested_id(row.get("parent"))
        if parent_id and parent_id not in self.dependencies.locations:
            raise LookupError("location parent is not available")
        parent_obj = self.dependencies.locations.get(parent_id) if parent_id else None
        tenant = tenant_for(
            row,
            default_tenant=self.context.default_tenant,
            map_companies=self.context.map_companies,
            tenants=self.dependencies.tenants,
        )
        defaults = {
            "custom_field_data": {"snipeit_id": str(sid)},
            "site": import_site,
            "tenant": tenant,
            "parent": parent_obj,
        }
        with transaction.atomic():
            obj = Location.all_objects.filter(custom_field_data__snipeit_id=str(sid)).first()
            if not obj:
                obj = Location.all_objects.filter(name=name, tenant=tenant).first()
            if obj:
                if not self.context.update:
                    outcome = "skipped"
                else:
                    if not self.context.dry_run:
                        obj.parent = parent_obj
                        obj.custom_field_data["snipeit_id"] = str(sid)
                        obj.save(update_fields=["parent", "custom_field_data"])
                    outcome = "updated"
            elif not self.context.dry_run:
                defaults["slug"] = _unique_slug(Location, name)
                obj = Location.objects.create(name=name, **defaults)
                outcome = "created"
            else:
                obj = Location(id=-sid, name=name, site=import_site, tenant=tenant)
                outcome = "created"
        self.dependencies.locations[sid] = obj
        result.counts.record(outcome)

    def _import_site(self, Site):
        if self.context.dry_run:
            return Site(id=-1, name="Imported (Snipe-IT)")

        saved_tenant = get_current_tenant()
        set_current_tenant(None)
        try:
            import_site = Site.all_objects.filter(name="Imported (Snipe-IT)", deleted_at__isnull=True).first()
            if not import_site:
                import_site = Site.objects.create(
                    name="Imported (Snipe-IT)",
                    status="active",
                    slug=_unique_slug(Site, "Imported Snipe-IT"),
                )
            return import_site
        finally:
            set_current_tenant(saved_tenant)


class UserImporter:
    key = "users"
    endpoint = "/api/v1/users"

    def __init__(self, context: ImportContext, dependencies: UserDependencies) -> None:
        self.context = context
        self.dependencies = dependencies

    def run(self) -> StageResult:
        AssetHolder = apps.get_model("organization", "AssetHolder")
        result = StageResult(self.key)
        self.context.reporter.start(result)
        for row in self.context.client.get_all(self.endpoint):
            sid = row["id"]
            first = (row.get("first_name") or "").strip()
            last = (row.get("last_name") or "").strip()
            email = (row.get("email") or "").strip()
            username = (row.get("username") or "").strip()
            upn = username or email or f"imported-user-{sid}"
            tenant = tenant_for(
                row,
                default_tenant=self.context.default_tenant,
                map_companies=self.context.map_companies,
                tenants=self.dependencies.tenants,
            )
            defaults = {
                "first_name": first,
                "last_name": last,
                "email": email,
                "upn": upn,
                "tenant": tenant,
                "custom_field_data": {"snipeit_id": str(sid)},
            }
            try:
                with transaction.atomic():
                    obj = AssetHolder.all_objects.filter(custom_field_data__snipeit_id=str(sid)).first()
                    if not obj:
                        obj = AssetHolder.all_objects.filter(upn=upn, tenant=tenant).first()
                    if obj:
                        if not self.context.update:
                            outcome = "skipped"
                        else:
                            if not self.context.dry_run:
                                for field, value in defaults.items():
                                    setattr(obj, field, value)
                                obj.save()
                            outcome = "updated"
                    elif not self.context.dry_run:
                        obj = AssetHolder.objects.create(**defaults)
                        outcome = "created"
                    else:
                        obj = AssetHolder(id=-sid, upn=upn, tenant=tenant)
                        outcome = "created"
                self.dependencies.holders[sid] = obj
                result.counts.record(outcome)
            # broad except: task-isolation: one remote row must not abort the reviewed import batch
            except Exception as exc:
                self.context.reporter.row_failure(result, "users.persist", exc)
        self.context.reporter.finish(result)
        return result
