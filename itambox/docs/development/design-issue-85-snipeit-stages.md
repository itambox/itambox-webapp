# Design: decompose the Snipe-IT importer into resource stages

Issue: `itambox/itambox-webapp#85`

## Summary

Replace `itambox/core/importers/snipeit.py` with the import-compatible package
`itambox/core/importers/snipeit/`. Keep `SnipeITImporter` as a small public orchestrator,
move the HTTP adapter unchanged into `client.py`, and move resource behavior into
16 stage classes. There is no compatibility copy of the old importer and no
large facade class: `core.importers.snipeit.__init__` only re-exports the existing
public symbols.

Each stage receives an immutable `ImportContext` and a stage-specific frozen
dependency bundle. Cross-resource maps are named fields in those bundles; they
are never hidden in a shared mutable `ImportState`. A stage returns a typed
`StageResult`, while the orchestrator renders that result to the existing
command-facing dictionary with exactly `created`, `updated`, `skipped`, and
`failed` keys.

This decomposition also pays down existing architecture and local-import debt.
Stage modules resolve domain models with the repository-sanctioned
`apps.get_model()` inversion and receive domain service callables from the
management-command composition root. They do not relocate the current deferred
domain imports into new files.

## Goals and non-goals

The design must:

- preserve the documented imports, constructor compatibility, command result
  shape, dry-run behavior, update behavior, skip names, and natural-key/external-
  ID matching rules;
- make execution order, stage inputs, and stage outputs reviewable without
  reading stage implementations;
- isolate each parent-row transaction and publish an ID-map entry only after that
  transaction commits;
- distinguish a failed parent row from a warning about an optional checkout,
  allocation, or seat subresource;
- keep reruns safe after either a row failure or a later stage abort; and
- make a new resource an additional stage and plan entry, not another method on a
  growing importer class.

The change does not redesign the Snipe-IT API, add new imported resource types,
change command options, introduce migrations, or change the external integration
error taxonomy.

## Target structure

```text
itambox/core/importers/snipeit/
  __init__.py            # public compatibility exports only
  client.py              # SnipeITClient and SnipeITError alias
  common.py              # constants, parsing helpers, tenant selection, service gateways
  contracts.py           # context, counts, issues, results, reporter, Stage protocol
  orchestrator.py        # public SnipeITImporter and explicit stage plan
  stages/
    __init__.py          # internal stage/dependency-bundle exports
    catalog.py           # status labels, manufacturers, categories, suppliers
    organization.py      # companies, locations, users
    custom_fields.py     # custom fields and fieldsets
    asset_models.py      # Snipe-IT models -> ITAMbox AssetType
    hardware.py          # assets, warranties, and checkout sub-pass
    inventory.py         # accessories, consumables, and components
    licenses.py          # software, licenses, and seat sub-pass
    maintenances.py      # asset maintenances
```

`__init__.py` re-exports these exact existing symbols:

```python
from .client import SnipeITClient, SnipeITError
from .common import IMPORT_NOTE, _clean_field_name
from .orchestrator import SnipeITImporter
```

This keeps all documented imports and monkeypatches of
`core.importers.snipeit.SnipeITClient` or `.SnipeITImporter` valid. Internal test
patches of `core.importers.snipeit.time.sleep` move to
`core.importers.snipeit.client.time.sleep`; `time` is not a documented public
symbol and should not be re-exported.

### Package-layout decision

Recommendation: use the package above, with one class per remotely ordered
resource and related small stages co-located by domain. Hardware, inventory,
licenses, and maintenances remain separate modules because their subresources,
services, and idempotency rules warrant dedicated boundaries. No production
module should approach the size of the deleted monolith; a review threshold of
roughly 300 lines per module should trigger another split.

Rationale:

- The package preserves the module name `core.importers.snipeit`, so callers do
  not need a legacy `snipeit.py` facade.
- Distinct stage classes remain independently constructible and testable even
  where several short classes share a file.
- Domain grouping avoids 16 nearly empty modules while keeping the large stages
  isolated.
- Imports can be chosen per bounded module, avoiding the duplicate-name/F811
  hazards that would arise from mechanically hoisting every old inline import
  into one replacement file.
- Coverage will attribute the moved production lines to new paths regardless of
  grouping; the chosen structure aligns test files with meaningful behavior
  groups rather than optimizing for Git rename detection.

Alternative: keep flat siblings such as `snipeit_client.py` and
`snipeit_hardware.py`. This requires either retaining `snipeit.py` as a facade,
which conflicts with the no-shim requirement and the purpose of the issue, or
breaking the public import path. A file per class was also considered, but it
adds repetitive module and dependency-bundle boilerplate without improving
class-level isolation.

On case-insensitive filesystems, implementation must delete/rename
`snipeit.py` before creating the `snipeit/` directory in the working tree. The
final Git diff is one deleted file plus the package files; there is never a
committed file and directory with the same path stem.

## Stage inventory and internal grouping

There are exactly 16 ordered stage classes:

1. `StatusLabelImporter`
2. `ManufacturerImporter`
3. `CategoryImporter`
4. `SupplierImporter`
5. `CompanyImporter` (enabled only by `map_companies`)
6. `LocationImporter`
7. `UserImporter`
8. `CustomFieldImporter`
9. `FieldsetImporter`
10. `AssetModelImporter`
11. `HardwareImporter` (skipped by `assets`)
12. `AccessoryImporter` (skipped by `accessories`)
13. `ConsumableImporter` (skipped by `consumables`)
14. `ComponentImporter` (skipped by `components`)
15. `LicenseImporter` (skipped by `licenses`)
16. `MaintenanceImporter` (skipped by `maintenances`)

Each class owns one top-level Snipe-IT collection and its necessary child
endpoints. Seats are therefore an internal `LicenseImporter` sub-pass rather
than a stage with no independent source object. Accessory checkouts and component
allocations follow the same rule.

Alternative: combine the small resources into a single `CatalogImporter`, or
promote every child endpoint to its own stage. The former would hide the order
and prevent resource-level construction; the latter would give seats and
allocations artificial lifecycles without independent parent inputs. One class
per top-level collection is the stable boundary.

`LocationImporter` retains two explicit passes over a fully fetched location
collection: roots first, then children. A child whose declared parent failed the
first pass is recorded as a failed row and is not silently created as a root.
The map is published after each successful row transaction, so the second pass
sees only committed parents (or negative-ID dry-run parents).

`HardwareImporter` has two internal phases. The persistence phase streams and
upserts hardware, warranties, and custom-field data, publishing each committed
asset to its output map. The checkout phase runs only after the persistence
stream completes, so user, location, and asset-to-asset targets resolve against
complete maps. If a later API page aborts the persistence phase, the checkout
phase does not run; already committed assets are recovered by the normal
idempotent rerun.

The old `_import_assignment` does not become a base-class method. It becomes an
`InventoryAssignmentGateway` in `common.py`, constructed from the two required
inventory service callables and the actor. Only accessory and component
dependencies receive it. The gateway retains target-tenant `TaskContext`,
`IMPORT_NOTE`, and the distinction between holder checkout and asset allocation.
This keeps the sanctioned service calls AST-visible at the new canonical
boundary without giving unrelated stages inventory mutation capabilities.

Alternative: a template-method base class could own logging, transactions, and
assignment behavior. The stages have materially different pass structures, and
putting all hooks on a base class would recreate implicit coupling. The design
uses a small protocol, a reporter, and explicit helper gateways instead.

## Common stage contract

The internal contract is typed, but serialization preserves the existing public
dictionary. The sketch below is the complete shape, not a requirement to use
these exact import spellings.

```python
Outcome = Literal["created", "updated", "skipped"]
Severity = Literal["warning", "failure"]
@dataclass(frozen=True)
class ImportContext:
    client: SnipeITClient
    default_tenant: object | None
    user: object
    dry_run: bool
    update: bool
    map_companies: bool
    reporter: StageReporter
@dataclass
class ImportCounts:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    def record(self, outcome: Outcome) -> None:
        setattr(self, outcome, getattr(self, outcome) + 1)
    def as_dict(self) -> dict[str, int]:
        return asdict(self)
@dataclass(frozen=True)
class StageIssue:
    severity: Severity
    operation: str
    error_code: str
    disposition: str
@dataclass
class StageResult:
    key: str
    counts: ImportCounts = field(default_factory=ImportCounts)
    issues: Counter[StageIssue] = field(default_factory=Counter)
    @property
    def warning_count(self) -> int:
        return sum(n for issue, n in self.issues.items() if issue.severity == "warning")
class ImportStage(Protocol):
    key: ClassVar[str]
    def run(self) -> StageResult: ...
```

`Counter[StageIssue]` bounds memory by safe issue category rather than retaining
one exception or remote row per failure. `StageIssue` deliberately contains no
payload, exception text, URL, source object ID, email, asset tag, or other remote
identity. `ImportCounts.failed` counts failed parent rows. Optional child failures
increment an issue with severity `warning` without changing the successful
parent's outcome.

`StageReporter` owns the sink trio: `stdout`, `job.append_log`, and the stable
`core.importers.snipeit` logger. It provides `start(result)`,
`row_failure(result, operation, exc)`, `warning(result, operation, exc)`, and
`finish(result)`. Both issue methods:

1. preserve an existing `IntegrationError` or wrap an unknown exception in
   `IntegrationUnexpectedError` with an allowlisted `IntegrationContext`;
2. log only `IntegrationError.log_extra()` fields;
3. write the same generic, non-identifying message to stdout and the job log;
4. aggregate a safe `StageIssue`; and
5. increment `counts.failed` only for `row_failure`.

`finish` always prints the four existing counts plus `warning_count`. The
orchestrator keeps `stage_results` for the command to add per-stage warning
counts to console/job summaries, but `run()` returns only
`result.counts.as_dict()` for compatibility.

The command's final entity lines add `warnings=N`. The completed Job result adds
`warning_counts: {stage_key: N}` and `total_warnings`; its existing `counts` and
total-created/updated/failed fields are unchanged.

### Continue versus abort

Primary collection failures occur outside row exception boundaries and propagate
as `SnipeITError`/`IntegrationError`; the orchestrator does not catch them. This
preserves the command's existing abort path and safe `display_message()` handling.
Authentication, configuration, transport/unavailable, rate-limit, and retry-
budget errors from a child endpoint also propagate rather than being demoted to
row warnings. A terminal not-found/request/contract error for one optional child
endpoint may be reported as a warning so the committed parent remains usable.

Transformation, validation, database, and domain-service errors for one parent
row are recorded and the stage continues. A checkout/allocation/seat error after
the parent commits is a warning. No stage returns a partially successful
`StageResult` after an abort; already completed stage results remain available
for diagnostics, and the raised integration error stops all downstream stages.

Alternative: retain raw nested dictionaries and let each stage catch broad
exceptions independently. That cannot enforce warning/failure semantics or safe
logging consistently. Making every exception fatal was also rejected because it
would remove the current row-isolation and resumability behavior.

## Context, domain resolution, and explicit map dependencies

`ImportContext` is immutable in the sense that stages cannot rebind its fields.
It contains shared configuration and sinks, not ID maps or counters. Stage
results own counters. The orchestrator owns every mutable ID map and passes each
stage only the maps it may read or write.

Domain models are resolved inside stage execution with, for example,
`apps.get_model("assets", "Manufacturer")`. `django.apps.apps` is imported at
module top. This is the architecture policy's sanctioned inversion for an
integration that must persist domain models, avoids new architecture-baseline
identities, and avoids new deferred imports. Django and kernel imports such as
`ContentType`, `transaction`, `FieldError`, and `core.managers` are placed at
module top where used.

The management command remains the composition root for domain services. It
continues to pass the two required keyword-only inventory services and also
passes `assets.services.checkout_asset` through a new optional keyword-only
`checkout_asset` argument. Existing constructor calls remain valid: when that
new argument is omitted, the orchestrator resolves the same callable with a
small `import_string()` fallback. The production command and all new tests pass
it explicitly. The two inventory arguments remain required and have no defaults,
so the current resource-grant boundary is unchanged.

The fallback is an additive constructor-compatibility path, not a legacy
importer shim. Making `checkout_asset` newly required would break existing
callers; leaving a static or deferred `assets.services` import in the hardware
stage would create a new forbidden architecture identity.

### Dependency-bundle shape

Recommendation: use one frozen dataclass per stage, with `Mapping` for read-only maps and
`MutableMapping` only for declared outputs. The orchestrator should pass
`MappingProxyType` views for inputs so accidental writes fail during tests.
Representative constructor signatures are:

```python
ManufacturerImporter(context: ImportContext, dependencies: ManufacturerDependencies)
# ManufacturerDependencies(manufacturers: MutableMapping[int, object])

AssetModelImporter(context: ImportContext, dependencies: AssetModelDependencies)
# AssetModelDependencies(manufacturers: Mapping[int, object],
#                        categories: Mapping[int, object],
#                        fieldsets: Mapping[int, object],
#                        asset_models: MutableMapping[int, object])

HardwareImporter(context: ImportContext, dependencies: HardwareDependencies)
# HardwareDependencies(status_labels, asset_models, tenants, suppliers,
#                      locations, custom_fields, holders: Mapping[...],
#                      assets: MutableMapping[int, object],
#                      checkout_asset: HardwareCheckoutGateway)
```

This keeps constructors short without hiding dependencies. Alternative: a
single mutable `ImportState` would let any stage mutate any map and make the
dependency graph implicit again. Passing every map as a direct constructor
argument is more explicit but makes hardware unwieldy; the frozen per-stage
bundle gives named fields and a stable unit-test fixture.

### ID-map publication rules

- The orchestrator creates fresh maps for each `run()`. Maps are not persisted
  across process invocations; resumability comes from database matching and
  reconstructing maps on every rerun.
- A stage reads inputs through `Mapping` views and writes only its declared
  output.
- For a real write, compute/upsert inside `transaction.atomic()`, leave the
  block, and only then add the object to the output map and increment its
  created/updated/skipped counter.
- For dry-run, build the same fake ORM object with the current negative-ID rule,
  then publish it and count the same outcome. Do not create sites, stock rows,
  warranties, assignments, seats, tenants, or other database rows.
- A rolled-back or failed row never leaves a map entry. Downstream stages never
  receive a fabricated placeholder for a real run.
- If the remote row names a required upstream ID that is absent because its
  stage failed, record the dependent row as failed rather than silently dropping
  the relationship. For an optional relationship, persist the parent and record
  a warning. Stage tests must document which relation is required.
- An abort stops orchestration immediately, so no downstream stage observes the
  partially populated map from an aborted stage.

`tenant_for` becomes a pure helper in `common.py`:

```python
tenant_for(row, *, default_tenant, map_companies, tenants)
```

Only stages that can map a row's company receive the tenant map. With company
mapping disabled, the helper always returns `default_tenant`; with it enabled,
it returns a mapped company tenant when present and otherwise preserves the
current default-tenant fallback.

## Explicit dependency DAG

“Writes” means a cross-stage map published after commit. `custom_fields` is
keyed by Snipe-IT database-column string; the other maps use integer Snipe-IT
IDs.

| Stage (`result.key`) | Reads | Writes |
|---|---|---|
| `StatusLabelImporter` (`statuslabels`) | none | `status_labels` |
| `ManufacturerImporter` (`manufacturers`) | none | `manufacturers` |
| `CategoryImporter` (`categories`) | none | `categories` |
| `SupplierImporter` (`suppliers`) | none | `suppliers` |
| `CompanyImporter` (`companies`) | none | `tenants` |
| `LocationImporter` (`locations`) | `tenants` | `locations` |
| `UserImporter` (`users`) | `tenants` | `holders` |
| `CustomFieldImporter` (`fields`) | none | `custom_fields` |
| `FieldsetImporter` (`fieldsets`) | `custom_fields` | `fieldsets` |
| `AssetModelImporter` (`models`) | `manufacturers`, `categories`, `fieldsets` | `asset_models` |
| `HardwareImporter` (`assets`) | `status_labels`, `asset_models`, `tenants`, `suppliers`, `locations`, `custom_fields`, `holders`; `assets` read only in checkout phase | `assets` |
| `AccessoryImporter` (`accessories`) | `manufacturers`, `categories`, `suppliers`, `tenants`, `holders`, inventory-assignment gateway | none |
| `ConsumableImporter` (`consumables`) | `manufacturers`, `categories`, `suppliers`, `tenants` | none |
| `ComponentImporter` (`components`) | `manufacturers`, `categories`, `suppliers`, `tenants`, `assets`, inventory-assignment gateway | none |
| `LicenseImporter` (`licenses`) | `manufacturers`, `suppliers`, `tenants`, `holders`, `assets` | none; software cache is private to this stage |
| `MaintenanceImporter` (`maintenances`) | `assets`, `suppliers` | none |

The license-to-software map is not cross-stage state and therefore remains a
private `LicenseImporter` cache. It exists only to avoid duplicate software
lookups while processing one license collection.

## Representative stage sketches

The simple stage pattern publishes the map only after `atomic()` exits. Domain
model resolution is explicit and does not create a static architecture edge.

```python
class ManufacturerImporter:
    key = "manufacturers"

    def __init__(self, context, dependencies):
        self.context = context
        self.dependencies = dependencies

    def run(self):
        Manufacturer = apps.get_model("assets", "Manufacturer")
        result = StageResult(self.key)
        self.context.reporter.start(result)
        for row in self.context.client.get_all("/api/v1/manufacturers"):
            source_id = row["id"]
            try:
                with transaction.atomic():
                    obj, outcome = self._upsert(Manufacturer, row)
                self.dependencies.manufacturers[source_id] = obj
                result.counts.record(outcome)
            except Exception as exc:
                self.context.reporter.row_failure(result, "manufacturers.persist", exc)
        self.context.reporter.finish(result)
        return result
```

Hardware is intentionally split into persistence and checkout helpers so neither
method carries the monolith's current cyclomatic complexity.

```python
class HardwareImporter:
    key = "assets"

    def run(self):
        Asset = apps.get_model("assets", "Asset")
        result = StageResult(self.key)
        checkout_rows = []
        self.context.reporter.start(result)
        deployed_status = self._deployed_status()
        for row in self.context.client.get_all("/api/v1/hardware"):
            try:
                with transaction.atomic():
                    asset, outcome = self._upsert_asset_and_warranty(Asset, row)
                self.dependencies.assets[row["id"]] = asset
                result.counts.record(outcome)
                checkout_rows.append((row, asset))
            except Exception as exc:
                self.context.reporter.row_failure(result, "assets.persist", exc)
        if not self.context.dry_run:
            for row, asset in checkout_rows:
                self._checkout_if_needed(row, asset, deployed_status, result)
        self.context.reporter.finish(result)
        return result
```

`_checkout_if_needed` resolves the target from the injected holder/location/asset
maps, wraps the service call in the asset tenant's `TaskContext`, and first checks
whether the active assignment already names that exact target. An exact match is
a no-op; a different target is passed to the sanctioned checkout service so the
source of truth can reassign it. This avoids creating check-in/checkout history
on an unchanged rerun while retaining update behavior.

Accessory checkout and component allocation helpers retain explicit
`_base_manager.filter(..., deleted_at__isnull=True).exists()` checks before the
gateway call. License seats retain `get_or_create` with the current holder/asset
keys. Each child row has its own warning boundary so one bad child does not stop
later children for the same parent.

## Orchestrator

Recommendation: `SnipeITImporter` keeps only:

- the existing public constructor and its compatibility/default handling;
- construction of `StageReporter`, immutable `ImportContext`, fresh named maps,
  and service gateways;
- explicit run order and the six current skip gates;
- the ordered `stage_results` and rendered `counts` registries; and
- `run() -> dict[str, dict[str, int]]`.

Mapping, ORM queries, persistence, row exception handling, per-stage summary
formatting, and child-resource behavior move out. The private
`_record_failure`, `_counter`, `_finish`, `_tenant_for`, and 16 `_import_*`
methods disappear rather than delegating to stages. Tests of `_record_failure`
move to `StageReporter`; that private method is not part of the documented
public API and should not be retained as a shim.

The management command retains the outer import `TaskContext`. Target-tenant
context switching for inventory assignments and hardware checkouts belongs to
the gateways in `common.py`, because only they know the actual target tenant.
No stage changes the ambient tenant without restoring it in `finally`; the
location stage preserves the existing temporary unscoped lookup used for the
shared import site.

Pseudocode for `run()` is deliberately linear:

```python
run(StatusLabelImporter(context, StatusLabelDependencies(status_labels)))
run(ManufacturerImporter(context, ManufacturerDependencies(manufacturers)))
run(CategoryImporter(context, CategoryDependencies(categories)))
run(SupplierImporter(context, SupplierDependencies(suppliers)))
if map_companies:
    run(CompanyImporter(context, CompanyDependencies(tenants)))
run(LocationImporter(context, LocationDependencies(read(tenants), locations)))
run(UserImporter(context, UserDependencies(read(tenants), holders)))
run(CustomFieldImporter(context, CustomFieldDependencies(custom_fields)))
run(FieldsetImporter(context, FieldsetDependencies(read(custom_fields), fieldsets)))
run(AssetModelImporter(context, AssetModelDependencies(read(manufacturers), read(categories), read(fieldsets), asset_models)))
if "assets" not in skip:
    run(HardwareImporter(context, hardware_dependencies))
if "accessories" not in skip:
    run(AccessoryImporter(context, accessory_dependencies))
if "consumables" not in skip:
    run(ConsumableImporter(context, consumable_dependencies))
if "components" not in skip:
    run(ComponentImporter(context, component_dependencies))
if "licenses" not in skip:
    run(LicenseImporter(context, license_dependencies))
if "maintenances" not in skip:
    run(MaintenanceImporter(context, maintenance_dependencies))
return counts
```

`run(stage)` stores the returned result under `result.key` and stores
`result.counts.as_dict()` under the same key. Disabled stages have no counts key,
matching current behavior. Alternative: a class registry plus automatic
topological sort adds runtime dependency discovery to a fixed source API. A
short linear plan is easier to review and test, and adding a resource still
requires only one stage class and one plan entry.

## Resumability, idempotency, and dry-run parity

The extraction must preserve each stage's current primary match order:

1. Snipe-IT ID in `custom_field_data` where the model supports it;
2. the stage's current natural key fallback; and
3. create only when both miss.

Existing records still count as `skipped` unless `update=True`; output-producing
stages publish skipped objects so downstream stages can resolve them. Update
branches retain the current field list and custom-field merge behavior. No stage
should broaden its natural key as part of this refactor.

Row transactions remain independent. If stage N aborts after earlier stages or
rows committed, rerunning reconstructs every map from those rows and continues
without duplicates. Service-backed assignments and seats retain live-row
idempotency checks. Hardware adds the exact-active-target check described above,
which prevents an unchanged rerun from creating reassignment history.

Dry-run executes mapping and outcome selection, creates negative-ID in-memory
model instances, and returns non-zero created/updated counts exactly as today.
It performs no ORM write, site creation, stock creation, warranty write, tenant
creation, checkout, allocation, or seat assignment. Every stage test must run at
least one representative create path in both real and dry-run modes.

## Test plan

### Preserve and adapt the existing suites

Keep `itambox/core/tests/test_import_snipeit.py` as the end-to-end
characterization suite.
Its existing `SNIPE_*` payloads continue to drive the public importer through a
mocked client. Move reusable payload builders and the fake client to
`itambox/core/tests/snipeit_fixtures.py` so stage tests do not import another
test module.
The existing assertions continue to cover basic creation, custom fields,
location parents, rerun idempotency, update, checkout status, dry-run, company
mapping, skip behavior, pagination, required inventory-service injection, and
safe management-command failures.

Update `itambox/core/tests/test_snipeit_error_contracts.py` only at internal
seams:

- patch `core.importers.snipeit.client.time.sleep`;
- exercise `StageReporter.row_failure` instead of the deleted private importer
  method; and
- retain the assertions that exception text, asset identity, email, bearer text,
  and source object ID do not reach any sink.

Public imports from `core.importers.snipeit` and command monkeypatch targets do
not change.

### New independently runnable stage suites

Recommendation: add these files, sharing the fixture module but constructing
each stage and its dependency bundle directly:

- `itambox/core/tests/test_snipeit_stages_catalog.py`: status labels, manufacturers,
  categories, suppliers and contact creation, custom fields, fieldsets, and
  asset models; map outputs and missing optional mappings.
- `itambox/core/tests/test_snipeit_stages_hardware.py`: persistence match order, warranty
  create/update, custom fields, post-persistence checkout to all three target
  types, exact-target rerun, checkout warnings, map publication after rollback,
  dry-run negative IDs, and maintenance create/update/skip behavior.
- `itambox/core/tests/test_snipeit_stages_inventory.py`: accessory, consumable, and
  component records and stock; injected service calls; live-row idempotency;
  target-tenant `TaskContext`; and per-child warning isolation.
- `itambox/core/tests/test_snipeit_stages_licenses.py`: private software cache, license
  create/update/skip, holder and asset seats, seat idempotency, child warnings,
  and dry-run.
- `itambox/core/tests/test_snipeit_stages_orchestration.py`: exact 16-stage order,
  company and six skip gates, dependency-bundle wiring, four-key count rendering,
  warning summaries, stop-on-client-error, and fresh maps on a second `run()`.
- `itambox/core/tests/test_snipeit_stages_organization.py`: companies, tenant
  selection, root/child location passes, failed-parent handling, user holders,
  and import-site tenant-context restoration.

Every stage suite needs representative create, existing-without-update, update,
row-failure, and dry-run coverage where those branches exist. The location,
hardware, inventory, and license suites additionally cover their internal
passes. Tests should assert database state and map state, not merely line
execution.

Alternative: rely only on the current end-to-end fixture suite and split it by
test name. That suite leaves several resource payloads empty and cannot prove
constructor isolation, map publication after rollback, or the branch-aware
differential coverage of new modules, so direct stage suites are required.

### Boundary tests and security manifest

Update `itambox/core/tests/test_import_boundaries.py` in two places:

1. In `test_assignment_mutation_surfaces_call_canonical_services`, replace the
   deleted `itambox/core/importers/snipeit.py` path with
   `itambox/core/importers/snipeit/common.py`. The sanctioned call set remains
   `_checkout_inventory_item`, `_create_component_allocation`,
   `checkout_inventory_item`, and `create_component_allocation`. The file must
   contain AST-visible calls through the injected attributes and no raw grant or
   assignment write.
2. Keep `IMPORTER = "core.importers.snipeit"`, but make
   `test_importer_does_not_import_inventory_services` enumerate the package
   initializer and every Python module below it. Assert that none has either a
   module-top or function-body import of `inventory.services`. Checking only the
   new `__init__.py` would weaken the current boundary.

The command test continues to assert a module-top `inventory.services` edge. Add
an assertion that the command imports and passes `assets.services.checkout_asset`
as the production composition root for hardware mutation.

Register all six new `test_snipeit_stages_*.py` paths in
`scripts/resource_grant_test_manifest.json` under `changed_tests` in lexical
order. Add them to `mandatory_tests` immediately after the existing
`test_import_snipeit.py` entry, in the same lexical order shown above, and add
the identical block at the identical position in the mandatory selector in
`itambox/docs/development/tenant-resource-grant-security.md`. The manifest and document
must continue to produce the same ordered tuple. Update the manifest provenance
to name issue #85.

## CI-gate impact

### Ruff formatting

All new Python and changed policy/test files must pass the formatter, not merely
Ruff's import-order check:

```bash
uv run --locked --only-group dev ruff format itambox scripts
uv run --locked --only-group dev ruff format --check itambox scripts
```

### Local-import policy

Do not copy any of the old function-body imports into a new path. Resolve domain
models with `apps.get_model()`, inject services, and hoist legal Django/kernel
imports. Hoist `FieldError` for `_unique_slug`; remove the unused `timezone`
import. The expected baseline change is removal of every
`itambox/core/importers/snipeit.py` finding and no addition under the package.
The existing management-command findings remain unchanged.

On canonical Python 3.12, first run the gate without write mode. It should report
only stale old Snipe-IT identities. Any new identity must be fixed before write
mode, because write mode correctly refuses to absorb it:

```bash
env -u PYTHONPATH uv run --locked python scripts/check_local_imports.py
env -u PYTHONPATH uv run --locked python scripts/check_local_imports.py --write-baseline
env -u PYTHONPATH uv run --locked python scripts/check_local_imports.py
```

### Architecture policy

The current architecture baseline has eight Snipe-IT rows: seven integration-to-
domain-model destinations and one `assets.services` destination. The package
must remove all eight through `apps.get_model()` and service injection, not
rename their source to new stage modules. Regenerate only after a non-writing run
shows those rows as stale and no new violation:

```bash
env -u PYTHONPATH uv run --locked python scripts/check_architecture.py
env -u PYTHONPATH uv run --locked python scripts/check_architecture.py --write-baseline
env -u PYTHONPATH uv run --locked python scripts/check_architecture.py
```

### Flake8 baseline

The deleted file currently owns 14 accepted Flake8 findings, including seven
complexity findings and five unused-import findings. Split complex run methods
into focused persistence/sub-pass helpers and remove unused imports rather than
moving the findings. Avoid a top-level/local duplicate such as `ContentType` and
`ContentType as CT`, which would produce F811 after hoisting.

Run with the inherited Hermes `PYTHONPATH` unset. The first run must show only
stale old Snipe-IT identities; fix every new occurrence before regeneration:

```bash
env -u PYTHONPATH uv run --locked python scripts/check_flake8_baseline.py
env -u PYTHONPATH uv run --locked python scripts/check_flake8_baseline.py --write-baseline
env -u PYTHONPATH uv run --locked python scripts/check_flake8_baseline.py
```

### Coverage and test certification

The decomposition will be represented largely as deleted/added paths, so moved
code is still changed executable code for differential coverage. Every new
production module must be imported and measured, and at least 85% of its touched
executable lines must be executed without originating an untaken branch. The new
stage suites are required even where the old end-to-end test happens to execute
the happy path.

Run the stage suites and existing Snipe-IT/boundary suites first, then the
documented mandatory resource-grant selector, the current parallel and
`serial_only` CI lanes, and finally:

```bash
make coverage
make coverage-diff
```

Do not accept a coverage decline or add exclusions. If the complete run improves
the global line or branch rate, update `scripts/coverage_baseline.json` with the
repository's canonical Linux/Python 3.12 `make coverage-baseline` flow in the
same change. No schema regeneration or migration check is required because no
model or API surface changes.

## File-by-file implementation plan

1. Add `itambox/core/tests/snipeit_fixtures.py` by extracting the reusable
   payloads and fake client from `test_import_snipeit.py`; leave the end-to-end
   assertions unchanged.
2. Add `client.py` with `SnipeITClient`, retry handling, parsing, and the
   `SnipeITError = IntegrationError` alias. Preserve method signatures and HTTP
   error behavior byte-for-byte where practical.
3. Add `contracts.py` and its focused reporter tests first. Prove sink redaction,
   issue aggregation, count rendering, warning versus failure, and fatal error
   propagation.
4. Add `common.py` with the format/status/category maps, parsing helpers,
   `_clean_field_name`, `_unique_slug`, `tenant_for`, `IMPORT_NOTE`, and the two
   target-tenant service gateways. Use no domain imports.
5. Extract the four catalog stages, three organization stages, two custom-field
   stages, and asset-model stage. As each output stage moves, add its direct
   tests for commit-before-publication and dry-run negative objects.
6. Extract hardware, preserving asset/warranty transaction boundaries and
   implementing the explicit checkout sub-pass and exact-target idempotency
   guard. Add the hardware tests before moving on.
7. Extract accessory, consumable, component, license, and maintenance behavior.
   Preserve stock and seat match rules and move inventory service calls only to
   the gateway in `common.py`.
8. Add `orchestrator.py` with the unchanged public constructor parameters, the
   optional injected `checkout_asset`, explicit linear plan, fresh maps, result
   registry, and exact four-key return rendering. Add `__init__.py` compatibility
   exports and the orchestration suite.
9. Update `import_snipeit.py` to inject `checkout_asset`. Update internal patch
   targets and reporter tests while preserving all public imports and command
   output assertions.
10. Delete `itambox/core/importers/snipeit.py`. Do not keep forwarding methods or a
    second implementation.
11. Update `test_import_boundaries.py`, then add the six tests to the manifest's
    lexically ordered `changed_tests` and to `mandatory_tests` plus the documented
    selector at the same position. Run the boundary test immediately.
12. Run Ruff format/check. Run the local-import gate, review that it reports only
    stale deleted-file identities, regenerate, and rerun. Repeat the same
    inspect/write/rerun sequence for the architecture baseline and then the
    Flake8 baseline. Baseline diffs must be reductions, never relocated debt.
13. Run the focused Snipe-IT suites, the mandatory security selector, full test
    lanes, `make coverage`, and `make coverage-diff`. Record the unchanged counts
    shape and dry-run/idempotency evidence in the pull-request description.

Expected new files are the package tree, fixture module, and six stage test
files. Expected deleted production file is only
`itambox/core/importers/snipeit.py`. Expected modified existing files are:

- `itambox/core/management/commands/import_snipeit.py`;
- `itambox/core/tests/test_import_snipeit.py`;
- `itambox/core/tests/test_snipeit_error_contracts.py`;
- `itambox/core/tests/test_import_boundaries.py`;
- `scripts/local_import_baseline.json`;
- `scripts/architecture_baseline.json`;
- `scripts/flake8_baseline.json`;
- `scripts/resource_grant_test_manifest.json`;
- `itambox/docs/development/tenant-resource-grant-security.md`; and
- conditionally `scripts/coverage_baseline.json`, only for a measured
  improvement.

Recommendation: deliver the extraction as one reviewable pull request with
behavior-preserving commits if desired, but without an intermediate compatibility
implementation in the final tree. Alternative: a sequence of independently
merged pull requests would need `snipeit.py` to coexist as a forwarding facade
while stages move, prolonging two implementations and making baseline/coverage
accounting ambiguous. The atomic package replacement keeps public compatibility
through `__init__.py` and lets every policy baseline describe one final
architecture.

## Behavioral-equivalence evidence

The implementation is equivalent when all of the following are true:

- the existing end-to-end Snipe-IT fixture suite passes through the package
  re-exports without changing public imports;
- `inspect.signature(SnipeITImporter)` still accepts every existing positional
  and keyword argument, and the two inventory services remain required
  keyword-only arguments;
- every executed stage returns a dictionary with exactly
  `{created, updated, skipped, failed}` integer keys to the command;
- enabled/disabled stage keys and ordering match the current `run()` order;
- a second real run creates no duplicate domain objects, stock assignments, or
  seats, while `update=True` refreshes the same fields as before;
- a dry run leaves all relevant tables unchanged and still reports the same
  non-zero prospective outcomes with negative-ID map objects;
- forced transaction rollback leaves no output-map entry, and a downstream
  required dependency is failed rather than silently detached;
- stdout, job logs, and Python logs remain free of payloads, exception text,
  source IDs, URLs, and credentials; and
- the local-import, architecture, Flake8, resource-grant, full-test, global-
  coverage, and differential-coverage gates all pass without new debt.

## Risks and mitigations

1. **Behavior drift while moving complex branches.** Natural-key order, update
   field sets, counter timing, and dry-run fake objects differ subtly by
   resource. Mitigate with the existing end-to-end suite plus direct create,
   skip, update, failure, and dry-run characterization before deleting each old
   method.
2. **Mapping state diverges from committed database state.** Publishing inside
   `atomic()` can retain an object after rollback, and a missing parent can
   flatten hierarchy. Enforce publication after block exit, read-only input map
   views, required-dependency failures, and explicit rollback tests.
3. **Service-backed subresources lose idempotency or tenant context.** Hardware
   checkout, inventory assignment, component allocation, and license seats have
   different sanctioned paths. Keep separate gateways/checks, wrap the actual
   target tenant, and test unchanged reruns plus cross-tenant targets.
4. **Package conversion weakens static boundaries.** The present boundary test
   names one file and would otherwise inspect only the new initializer. Point
   the mutation surface at `common.py`, scan every package module for forbidden
   imports, and remove rather than relocate architecture/local-import baseline
   entries.
5. **Differential coverage treats extraction as new code.** The old happy-path
   integration fixture is insufficient for new helper branches. Land direct
   stage tests alongside each extraction and run branch-aware differential
   coverage before the final full suite.

## Repository observation

`AGENTS.md` and the current issue recon describe the parallel
`-m 'not serial_only'` lane plus a `serial_only` lane. `CLAUDE.md` still states
that xdist is disabled. This design treats `AGENTS.md` and the current CI policy
as authoritative. Correcting that unrelated guidance drift should be a separate
documentation change so the issue #85 implementation does not mix policy edits
with the importer refactor.
