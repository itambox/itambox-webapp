# Static typing policy

ITAMbox types its backend **gradually and monotonically**. There is no
repository-wide type check and no diagnostic baseline. Instead there is an
explicit, append-only list of whole modules and bounded top-level symbol
surfaces that are required to check clean, and a blocking gate that proves they
still do.

The gate is `scripts/check_typing_policy.py`. The record is
`scripts/typing_checked_modules.json`. The checker configuration is the
`[tool.mypy]` and `[tool.django-stubs]` sections of the root `pyproject.toml`.
All three are checked against each other on every run.

```bash
# From the repository root -- direct blocking-gate invocation:
uv run --locked --group dev python scripts/check_typing_policy.py

# Makefile entry point (clears developer-local PYTHONPATH):
make typecheck

# CI invokes this after `uv sync --locked --group dev`:
uv run --locked --no-sync python scripts/check_typing_policy.py

# What is the gate comparing against?
uv run --locked --group dev python scripts/check_typing_policy.py --list

# The behavioural suite CI runs by discovery:
uv run --locked --only-group dev python -m unittest scripts.tests.test_typing_policy
```

The pre-commit hook invokes the direct command with the full dev group. The
checker itself clears `PYTHONPATH` and `MYPYPATH`, so all three entry points use
the same import-resolution policy.

The full dev group is required, not `--only-group dev`: the django-stubs mypy
plugin imports the Django settings module, so the runtime dependencies have to
be installed. This makes the typing gate the one pre-commit hook that is not
AST-only, and therefore the one that is noticeably slower. That cost is
deliberate and documented rather than hidden.

## Why an allowlist and not a baseline

A diagnostic baseline exists to admit *dirty* modules and pay the debt down
later. This policy admits none. A whole module, or a symbol-scoped projection,
enters the checked list only when its admitted surface produces **zero**
diagnostics under the flags below — clean at admission. That removes an entire
class of failure where a surface is "typed" on paper while its suppressed
diagnostics quietly accumulate, and it means there is only one ratchet to keep
in step instead of two.

The consequence is that a module which cannot reach zero diagnostics without a
behaviour change is **withdrawn with a tombstone**, never forced in with
suppressions.

## The checker

| Distribution | Version | Why it is pinned exactly |
|---|---|---|
| `mypy` | `2.3.0` | What "clean" means is decided by the checker; a minor bump can add diagnostics. |
| `django-stubs` | `6.0.7` | Supplies the mypy plugin that resolves managers, querysets, and `settings.*`. |
| `djangorestframework-stubs` | `3.17.1` | DRF stubs; tracks the pinned DRF version. |

The three are resolved together through their `compatible-mypy` extras and
recorded in the policy record. Bumping any one of them without re-recording the
record fails the gate (`T-REC2`, `T-CFG5`).

Pyright was considered and not chosen. The deciding factor is that django-stubs
ships a **mypy plugin** — the mechanism by which manager/queryset generics and
settings attribute access resolve at all — plus this repository's single-lockfile
policy: a Node runtime would have to be pinned and verified outside `uv.lock`.
Pyright remains a credible alternative, not a rejected-as-weak one.

## Normative flags

The flag set is written out one flag at a time in `pyproject.toml` rather than
abbreviated to `strict = true`. `strict` is an alias whose membership changes
between mypy releases, so the shorthand would let an upgrade silently change
what "clean" means for every checked module.

`python_version = "3.12"`, `plugins = ["mypy_django_plugin.main"]`,
`follow_imports = "silent"`, `disallow_untyped_defs`,
`disallow_incomplete_defs`, `check_untyped_defs`, `no_implicit_optional`,
`warn_return_any`, `warn_unused_ignores`, `warn_redundant_casts`,
`strict_equality`, `disallow_any_generics`, `disallow_any_unimported`,
`disallow_untyped_decorators = false`, and
`enable_error_code = ["ignore-without-code", "possibly-undefined"]`.

`scripts/check_typing_policy.py` asserts these values against `pyproject.toml`
on every run (`T-CFG2`), so re-recording the fingerprint is not a way to relax
one.

Three deliberate omissions, each of which is a decision rather than an oversight:

- **`disallow_untyped_calls` is not in this first ratchet.** With the current
  annotation coverage it would pull the entire first-party callee closure of
  every checked module into scope, which is an unbounded expansion rather than a
  bounded slice. It is a named later ratchet step, to be taken once the checked
  set has enough typed callees. When it is taken, the remedy for an untyped
  callee is to annotate it — never to add a convenience `cast`.
- **`disallow_untyped_decorators` is off.** DRF, graphene, and django-q
  decorators are untyped, and requiring them to be typed would make third-party
  release timing decide whether this repository builds. A decorated function
  still has to carry its own annotations; `disallow_untyped_defs` enforces that.
- **`redundant-expr` is not enabled.** It judges against mypy's *inferred* type,
  which for a parser reading untrusted JSON is narrower than what arrives at
  runtime. On the SCIM PATCH parser it reports the `isinstance` guards as
  always-false, so enabling it would pressure a contributor into deleting
  defensive validation of external input to make a gate green. That is a worse
  trade than the bugs it would find here.

`follow_imports = "silent"` is what makes a bounded checked set possible:
unchecked modules are read for their types but never reported.

## Where the checker runs

mypy runs **from `itambox/`**, so that bare app imports (`core`,
`organization`, ...) and `core.settings.dev` resolve the way Django resolves
them. It is handed the root configuration explicitly with
`--config-file <repo-root>/pyproject.toml`; config discovery from a child
directory would silently find nothing and check with mypy's defaults.

The record stores **repository-relative** paths
(`itambox/users/api/scim/provider_patch.py`). The gate translates them to
working-directory-relative paths on the command line and derives the dotted
module name from the same string, so the record, the command line, and the
override patterns cannot disagree. Both translations are unit-tested.

The plugin's settings module is `core.settings.dev`, and the gate exports
`ITAMBOX_ENV=dev` rather than inheriting whatever a developer has exported.
Settings load performs no database, network, or filesystem-mutating work.

## The record: `scripts/typing_checked_modules.json`

Schema v2. Every top-level field is required and no others are permitted. The
gate still reads a legacy schema-v1 record as whole-module admissions, but every
new record and every narrowed admission uses schema v2 explicitly.

| Field | Meaning |
|---|---|
| `schema_version` | `2`. |
| `canonical_python` | `"3.12"`. The gate refuses any other interpreter. |
| `next_sequence` | One past the highest admission sequence issued. |
| `policy_sha256` | Fingerprint over the effective policy, module paths, scopes, and admitted symbols. |
| `config` | Mirror of the effective configuration: config file, working directory, settings module, platform authority, checker versions, mypy flags, and overrides. |
| `checked` | Whole modules or symbol-scoped surfaces that must produce zero diagnostics. Sorted by path. |
| `withdrawn` | Tombstones. Never deleted. |

A schema-v2 `checked` entry carries exactly `sequence`, `path`, `module`,
`issue`, `note`, `scope`, and `symbols`. `scope = "module"` requires
`symbols = []` and retains the whole-module behavior. `scope = "symbols"`
requires a non-empty, sorted list of existing top-level class or function
names; a selected class includes its methods. A schema-v1 entry omits the last
two fields and means `scope = "module"` for backwards compatibility. A
`withdrawn` entry carries exactly `sequence`, `path`, `module`, `issue`, and
`reason`, where the reason is at least 40 characters explaining what could not
be typed, and the issue is the follow-up (`#93`).

### Monotonicity

Sequences are issued once and shared by both lists. The gate requires the union
of `checked` and `withdrawn` sequences to be the contiguous run
`1..next_sequence-1` with no gaps and no duplicates. Moving a module from
`checked` to `withdrawn` keeps its sequence, so a withdrawal is free; deleting a
row leaves a hole the gate sees **without consulting git history**.

This is a ledger, not a cryptographic guarantee: a determined editor could
delete a row, decrement `next_sequence`, and re-record the fingerprint. The
point is that doing so takes three coordinated edits to a reviewed file, all of
them visible in the diff, instead of one silent deletion.

A checked path must exist on disk (`T-REC4`). A tombstone path need not — that
is a tombstone's entire purpose. A rename is therefore a tombstone plus a new
entry, never a silent edit.

### The fingerprint

`policy_sha256` covers the schema version, the canonical interpreter, the
recorded configuration (checker versions, every mypy flag, every override, the
working directory, the config file, the platform authority), the normative flag
set and required error codes, the top-level, override, and django-stubs
allowlists, the marker and suppression grammars, the checked and withdrawn path
lists, every checked scope and admitted symbol, the shadow-projection algorithm
version, and the path-to-module mapping.
It is verified twice: against the record's own `config` block, and against the
effective policy read from `pyproject.toml`.
The first catches a hand-edited record; the second catches configuration drift.

### There is no write mode

Deliberately, and for the same reason as `scripts/check_contract_policy.py`:
admitting a module is a review event. When the gate fails, the fix is to make
the module clean, to withdraw it with a tombstone, or to correct the
configuration — never to regenerate the record until it agrees with whatever
the tree happens to say.

To admit a module: make it check clean, add a `checked` entry with the next
sequence, bump `next_sequence`, and update `policy_sha256` (the gate prints the
expected value when it disagrees).

### Symbol-level admission and shadow projections

`scope = "symbols"` is the bounded escape hatch for a module whose surrounding
helpers are not ready for whole-module admission. The gate resolves each name
against the module's top-level `class`/`def` declarations, scans explicit
`Any` and suppression grammar only inside those definitions, and fails with
`T-SYM1` if a renamed or deleted contract is still recorded.

For mypy, the gate creates a temporary isolated input projection. It contains
the selected definitions, the imports and module-level aliases needed by their
signatures, and the selected method/function signatures with bodies replaced by
`raise NotImplementedError`. Individual import aliases are filtered, so an
admitted value object does not accidentally pull an unchecked helper into the
projection. The projection directory is deleted after the run and is never
part of the repository. Symbol projections are passed as direct checker inputs,
not through `--shadow-file`: an eager package initializer cannot pull unrelated
unchecked modules into the admission. The command uses `--no-incremental`, so a
previous projection cannot be reused for a different symbol selection.
Whole-module entries retain the original command line and behavior.

This is deliberately an **interface admission**, not a claim that the
surrounding module or the selected implementation bodies are fully typed. The
fields, decorators, bases, and method/function signatures are checked now;
implementation-body typing remains a later bounded slice. A selected symbol
also may not rely on an unselected local definition merely because that
definition happens to be present in the source file.

## Explicit `Any`

`Any` is not banned in a checked module. **Unexplained** `Any` is.

Every explicit `Any` **inside an admitted scope** — every `Name` or attribute
reference, not merely the import — must be covered by a marker:

```
# typing: <category>: <reason>
```

| Category | Admission test |
|---|---|
| `external-json` | An untrusted runtime value whose shape is validated before use. |
| `sentinel` | A sentinel whose absent/null/value semantics have no union form yet, with an identity-preserving test. |
| `third-party-untyped` | A dependency inspection showing no usable stubs. |
| `dynamic-identifier` | A producer inventory proving no narrower stable identifier type exists. |

A marker is resolved from the nearest of: a trailing comment on the annotation's
own line; the contiguous comment block directly above it; a trailing comment on
the enclosing `def`/`class` header; the comment block directly above the
enclosing definition (innermost scope first). If no nearer marker exists, an
annotation on the immediately following source line inherits the previous
resolved marker; this is the deliberately textual adjacency rule used by the
gate. A marker on one function never covers the next.

A marker carrying an **unrecognised category, or no reason, always fails and can
never be recorded away (`T-ANY2`). That is the same escape-proof rule the
inline-import gate applies; see [Python import policy](python-import-policy.md).

The gate resolves `Any` imported under an alias from `typing` or
`typing_extensions` as explicit `Any` too; `typing.Any` attribute references
are covered by the same rule. An import alone is not a use and needs no marker.

Implicit `Any` is not governed by this grammar at all — it is governed by
`disallow_untyped_defs`, `disallow_any_generics`, `disallow_any_unimported`, and
`warn_return_any`. A text scan is not a substitute for checker diagnostics, and
this one does not pretend to be.

### The SCIM sentinel, stated honestly

`UNSET = _Unset.UNSET` in `users/api/scim/provider_patch.py` distinguishes an
absent PATCH attribute from a validated value. The enum singleton preserves the
existing `value is UNSET` identity checks while giving the dataclass fields a
real union: string fields use `str | _Unset`, and `active` uses `bool | _Unset`.

The parser still rejects explicit `null` for add/replace operations and retains
the established empty-string semantics for removals. This is intentionally not
an `Optional[T]` claim: `None` is not a valid parsed update value on this
boundary. The typed sentinel and its identity semantics are covered by a
dedicated test; the separate `Any` on helpers that accept parsed JSON stays
`Any` and stays `external-json`, because narrowing genuinely arbitrary input to
a structural type would be a false claim about untrusted data.

## Suppressions

Bare `# type: ignore` is forbidden — by `enable_error_code =
["ignore-without-code"]` in the checker, and by the gate's own scan of admitted
scopes. The required form is a coded ignore plus a categorised reason:

```python
x = thing()  # type: ignore[attr-defined]  # typing: third-party-untyped: dependency ships no usable stubs
```

The reason may sit on the preceding line when the combined comment would exceed
the repository's 120-character limit. Categories: `third-party-untyped`,
`django-plugin-limit`, `external-json`.

Platform-specific missing modules are handled with a per-module override rather
than a source-level ignore, so Linux's `warn_unused_ignores` stays meaningful.

## Overrides

A `[[tool.mypy.overrides]]` block whose module pattern captures a checked module
may set **only** `ignore_missing_imports`. Any other key — `ignore_errors`, or
any relaxation of a normative flag — fails (`T-CFG3`), including through a
wildcard such as `pkg.*`. If `ignore_missing_imports` then introduces an `Any`
that trips `disallow_any_unimported`, the use site carries a
`third-party-untyped` marker and stays contained; if it cannot be contained, the
module is withdrawn rather than weakened.

## Platform and interpreter authority

**Linux on canonical Python 3.12 is the sole authority.** The gate refuses to
run on any other interpreter (exit 2). On any other platform it prints a
`NOT AUTHORITATIVE` banner and still reports the real exit code: `django-auth-ldap`
and `python-magic` are excluded on native Windows by platform marker, so the
import graph mypy sees there differs from CI's. A green local Windows run is
useful; it is not proof that CI is green.

## Failure classes

| Rule | Fails when |
|---|---|
| `T-CFG2` | A normative flag or required error code is missing or relaxed in `pyproject.toml`. |
| `T-CFG3` | An override relaxes anything for a checked module. |
| `T-CFG4` | An admitted source file contains a file-level `# mypy:` directive. |
| `T-CFG5` | The recorded `config` block no longer describes `pyproject.toml`. |
| `T-REC2` | `policy_sha256` does not match the record's own contents, or the effective policy. |
| `T-REC3` | Admission sequences have a gap or a duplicate, or `next_sequence` is wrong. |
| `T-REC4` | A checked path does not exist, is admitted twice, or the list is unsorted. |
| `T-REC5` | A tombstone has no follow-up issue or a reason shorter than 40 characters. |
| `T-REC6` | A recorded module name contradicts its path. |
| `T-SYM1` | A symbol-scoped entry names no existing top-level class or function. |
| `T-ANY1` | An explicit `Any` in an admitted scope has no marker. |
| `T-ANY2` | A marker has an unrecognised category or no reason. |
| `T-IGN1` | A suppression is uncoded, or has no categorised reason. |
| `T-IGN2` | A suppression's category is unrecognised. |
| `T-RUN1` | mypy reported diagnostics in a checked module. |

An unreadable record, an unknown schema version, an unpinned checker, a missing
`[tool.mypy]` section, or a non-canonical interpreter are not findings — they
exit 2, because the gate cannot produce a trustworthy result at all.

## Interaction with the other gates

- **Import policy.** A function-body import added for typing has no valid
  category under [Python import policy](python-import-policy.md) and can never
  be baselined. Typing uses module-top or `if TYPE_CHECKING:` imports only.
- **Architecture.** `if TYPE_CHECKING:` imports are in neither graph the
  [architecture gate](architecture-policy.md) builds, so a typing-only import
  can create a real cross-layer coupling that gate cannot see. Any
  `TYPE_CHECKING` import added for typing must satisfy the layer matrix as if it
  were a runtime import — the model-to-presentation rule especially, which has
  no baseline escape at any severity.
- **Runtime annotation evaluation.** On Python 3.12 annotations are evaluated at
  definition time unless the module opts into postponed evaluation, so a
  `TYPE_CHECKING`-only name must be quoted. `from __future__ import annotations`
  turns every annotation in the module into a string and can break dataclass
  introspection; choose deliberately and test the choice.
- **Flake8 baseline.** `scripts/flake8_baseline.json` is keyed partly by the
  source statement, so adding an annotation to a line carrying a baselined
  violation changes its identity. Expect to regenerate the baseline in the same
  reviewed change.
- **OpenAPI.** drf-spectacular infers `SerializerMethodField` component types
  from resolver return annotations. Annotating serializer methods is therefore
  expected to move `schema.yaml`; that is an external-contract change reviewed
  under the [compatibility policy](compatibility-policy.md), and the schema is
  never edited to match a bad annotation.

## Current scope

The checked list is the authority; this section is orientation only. Slice 0
admits `itambox/users/api/scim/provider_patch.py` as a whole module, the pure
SCIM PATCH parser. Slice 1 admits two top-level value-object interfaces without
claiming their surrounding modules:

- `SystemAuthorizationContext` in `itambox/core/context.py`;
- `ResourceAccessDecision` in `itambox/organization/access.py`.

Both Slice-1 entries use `scope = "symbols"`; their selected fields and method
signatures are clean under the recorded policy, while the surrounding request,
tenant, and RBAC helpers remain outside the checked set.

Slice 2 hardens the already-admitted SCIM parser without changing its registry
entry: the absent-field sentinel is now typed, while the surrounding external
JSON boundary remains explicitly categorised.

Slice 3 admits `TaskContext` in `itambox/core/tasks/context.py` as one symbol:
its constructor, context-manager entry/exit protocol, captured-context
attributes, principal/tenant resolution, and system-authorization issuing
signature. The contract uses small structural protocols for the resolved tenant
and principal, and resolves Django models through `apps.get_model()` at task
entry so the platform-service admission does not import a domain model graph at
module load. Task payloads, task functions, and the surrounding task package
remain outside this slice.

Slice 4 admits `_ReportOutput` in `itambox/core/tasks/reports.py` as one symbol:
the rendered email body plus the optional attachment content, filename, and MIME
type that a scheduled-report renderer hands to the archive and delivery steps.
It is the first existing named task result contract admitted — Slice 3 admitted
the scope manager that wraps a task, this admits a value a task produces. The
renderer and task functions that build and consume it, and the report-provider
architecture behind `compile_report_context`, remain outside this slice; no
annotation is added to a renderer function here, and report-provider ownership
remains #83.

Slice 5 adds `generate_scheduled_report_task` to the same entry, so
`itambox/core/tasks/reports.py` now admits two symbols. It is the scheduled-report
task entry envelope: a worker is handed a `ScheduledReport` id as `int` and the
task answers `bool | None` — `True` or `False` for a schedule that was processed
or failed, and `None` for one whose tenant scope could not be resolved, which is
refused rather than compiled across tenants. Only the entry point is annotated;
the renderer, archive, delivery, and scope helpers between it and `_ReportOutput`
keep their existing untyped signatures, and report-provider ownership remains
#83.

Slice 6 admits the independent SCIM boundary block:

- `_SCIMQuery` and every parser/helper function in
  `itambox/users/api/scim/filters.py`, covering the pure SCIM filter expression
  boundary without importing Django's `Q` implementation into the projection;
- `identifier_lookup` and `identifier_lookup_or_none` in
  `itambox/users/api/scim/identifiers.py`, while the queryset-consuming
  `get_scim_object_or_404` helper remains outside the admission;
- `_SCIMAuthenticatedPrincipal` plus `SCIMBearerTokenAuthentication` and
  `SCIMProviderBearerTokenAuthentication`, covering their DRF request/return
  authentication contracts without importing project model classes into the
  projections;
- `_SCIMUserResource`, `_SCIMMembershipResource`, `_SCIMGroupResource`,
  `SCIMUserSerializer`, and `SCIMGroupSerializer` in
  `itambox/users/api/scim/serializers.py`, covering the selected fields and
  `SerializerMethodField` return contracts for tenant- and provider-shared SCIM
  resources through structural protocols.

All Slice-6 entries use `scope = "symbols"`: their fields and signatures are
checked through reviewed projections, while surrounding ORM, authentication,
serializer context, and mutation helpers remain outside the checked set. The
runtime filter values remain Django `Q` objects, and the runtime serializers
still resolve their concrete models normally; the structural protocols keep
those implementation details out of the bounded mypy import graph. This slice
does not claim the complete SCIM operation surface, report-provider boundary
(#83), or generated OpenAPI compatibility work (#98).

Slice 7 adds two larger public-boundary families:

- the bulk check-in, checkout, disposal, and label task entry envelopes in
  `core.tasks.*`, together with their date/proceeds/measurement/chunking and
  barcode helpers. IDs and JSON task payloads are typed at the enqueue boundary;
  the existing task bodies, ORM lookups, service calls, and notification
  results remain outside the projection;
- the public access boundaries in `organization.access`: tenant-group topology
  walks, shared-resource selection, stock authorization and its facades,
  tenant-operation authorization, accessible-tenant memoization, and the tenant
  access report. Model instances are represented as `object`/`type[object]` at
  this boundary, while the already-admitted `ResourceAccessDecision` and
  `SystemAuthorizationContext` contracts remain concrete.

All Slice-7 entries are symbol-scoped. The task and access implementations are
not whole-module admissions: private ORM evidence, RBAC traversal, task
serialization, and renderer/PDF internals remain untyped until a later bounded
slice. This slice does not claim the remaining task families, complete SCIM
operation surface, report-provider boundary (#83), or generated OpenAPI
compatibility work (#98).

Subsequent slices of issue #93 extend the list one bounded surface at a time:
the remaining task payload/result families, organization service boundaries,
report-provider contracts, and serializer return annotations. Nothing is claimed
as checked until it appears in the record.
