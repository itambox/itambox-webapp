# ADR 0001 — Architecture boundaries and layering

- **Status:** Accepted
- **Deciders:** ITAMbox maintainers
- **Applies to:** every first-party Python module under `itambox/`

## Context

ITAMbox is a single Django project: a platform substrate (`core/`, `itambox/`)
plus ten domain applications. Nothing recorded which layer a module belonged to
or which direction a dependency was allowed to run, so three kinds of coupling
accumulated without anyone deciding to accept them:

- import cycles, several of which are only visible once function-body imports
  are counted;
- platform code that names a domain application, which makes the substrate
  impossible to reuse and the domain impossible to remove;
- models that reach up into forms, tables, and views, which turns a data model
  into a rendering concern.

Reviewers cannot see any of this in a diff. A module-level import three
directories away is invisible at the point where it starts to matter.

## Decision

Classify every first-party module into exactly one layer, declare which
directions are permitted, and enforce both with a blocking gate
(`scripts/check_architecture.py`). Freeze the coupling that exists today as
reviewed debt with an owner and a removal direction; do not pay it down here.

### Layers

| Layer | What it is | Examples |
|---|---|---|
| `framework` | Domain-blind reusable machinery | `itambox.api.*`, `itambox.middleware`, `itambox.plugins.*`, `itambox.registry` |
| `kernel` | Domain-blind model and manager substrate | `core.models`, `core.managers`, `core.mixins`, `core.choices`, `core.context` |
| `platform-service` | Cross-cutting runtime services | `core.tasks.*`, `core.events`, `core.reports.*`, `core.schedules` |
| `integration` | Adapters for systems outside ITAMbox | `core.auth.*`, `core.importers.*`, `core.integrations.*` |
| `domain-model` | Persistent state of one application | `assets.models.*`, `organization.models`, `inventory.abstract_models` |
| `domain-service` | Behaviour over one application's models | `assets.services`, `assets.reports`, `inventory.stock`, `organization.access` |
| `presentation` | Forms, tables, filters, views, serializers, templatetags | `assets.forms.*`, `itambox.views.*`, `core.tables.*` |
| `composition` | Wiring: URLconfs, app configs, admin, settings, the ASGI/WSGI entry points | `core.urls`, `core.settings.*`, `assets.apps` |

`presentation` is split by origin. A domain application's presentation may name
its own domain; the platform's generic presentation (`itambox.views.*`,
`core.tables.*`) is held to the framework standard, because a generic view that
names one application stops being generic.

An `__init__.py` with no first-party imports of its own is an inert namespace
marker. It gets the `package-init` sentinel rather than a layer, and it never
appears as the endpoint of an edge.

Classification reads the dotted name and nothing else. Each segment is reduced
to at most three tokens — the whole segment, the text before its first
underscore, and the text after its last underscore — so `urls_audits` offers
`urls` and `provider_urls` offers `urls` alike. A last segment yielding `admin`,
`apps`, `asgi`, `urls`, or `wsgi` is `composition`; that single rule classifies
`assets.urls_audits` and `users.api.scim.provider_urls` correctly without a
hand-written entry for either, which is the point: an override map that absorbs
naming variants stops being reviewable. A middle token is not examined, and a
name matching no rule is never given a default — the gate exits 2 and asks for
a `MODULE_LAYER_OVERRIDES` entry. The cost of the rule is that a module named
for wiring is treated as wiring, so a helper called `admin_utils` would inherit
composition's freedom to import anything; nothing may import `composition`, so
it cannot launder an edge for another module, but the five words are worth
avoiding in a name that is not a composition root.
[Architecture policy](architecture-policy.md) works the rule through in full.

### Permitted directions

Read as *row imports column*.

| ↓ imports → | framework | kernel | platform-service | integration | domain-model | domain-service | presentation | composition |
|---|---|---|---|---|---|---|---|---|
| **framework** | ✅ | ✅ ¹ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **kernel** | ✅ ¹ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **platform-service** | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| **integration** | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| **domain-model** | ✅ | ✅ | ❌ | ❌ | ⚠️ ² | ❌ | 🚫 | ❌ |
| **domain-service** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| **presentation** (domain) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| **presentation** (platform) | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ |
| **composition** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

- ✅ allowed.
- ⚠️ allowed only when the `(source app, target app)` pair is declared in
  `CROSS_DOMAIN_MODEL_EDGES`; same-application model coupling is always allowed.
- ❌ forbidden. Instances that existed when the gate landed carry a baseline row
  with an owning area label, a removal issue, and a removal direction. New ones
  fail, and `--write-baseline` will not absorb them.
- 🚫 forbidden absolutely. There is no baseline representation at any severity.

¹ `framework` and `kernel` recurse into each other deliberately. Both directions
exist at module top and neither is going away: `itambox.api.viewsets` builds on
`core.managers`, and `core.models` registers itself with `itambox.registry`.
They are one mutually recursive platform substrate and keep separate names
because their diagnostics differ.

² The declared pairs today are `assets → extras`, `licenses → assets`,
`licenses → extras`, `licenses → organization`, `licenses → software`,
`software → extras`, and `subscriptions → extras`. Adding one moves the policy
fingerprint and is therefore a reviewed diff.

The single 🚫 cell is `domain-model → presentation`. A model that imports a
form, a table, a view, or a presentation helper cannot be recorded at any
severity: the gate fails on the edge itself and `--write-baseline` refuses to
write the row.

### Two graphs, both blocking

Direction is checked over a module-top graph and over an effective graph that
adds function-body imports. Both block from the first run. Moving an import into
a function defers *when* a coupling happens, not *whether* it exists, and the
largest cycle in this repository is invisible at module top. `if TYPE_CHECKING:`
imports are in neither graph: they never execute, and a typing-only back edge is
the sanctioned fix for a real cycle rather than a defect.

### The baseline is a work list

Every recorded row carries an `area:*` owner derived from a fingerprinted
module-prefix table, a `removal_issue`, and a `removal_direction` of at least
40 characters that says how the edge goes away. Placeholders (`TBD`, `TODO`,
`n/a`) are refused by the loader. The intent is that
`scripts/architecture_baseline.json` reads as the plan for removing the coupling
rather than as a list of suppressions.

## Consequences

### What this issue does not do

It removes no cycle and no cross-layer edge that already existed. Freezing the
debt and paying it down are separate pieces of work, and mixing them would make
both unreviewable. Two coupled decisions are deliberately deferred:

- whether the tenancy primitives (`Tenant`, `Membership`, `RoleGrant`) belong in
  `core` rather than in `organization`. That is what the twenty-module effective
  component is really about, and a boundary gate is the wrong place to decide it;
- whether typing-only coupling should be enforced at all. The rule identifier
  `R-C4` is reserved for it and inactive; the loader rejects any row citing it.

### No application code changed

The gate, its baseline, its tests, and this documentation are the whole change.
Nothing under `itambox/**/*.py` moved, and nothing needed to: the coupling that
exists today is recorded, not removed.

One classification deserves calling out, because it is the only place where a
single module could not be described honestly by either neighbouring layer.
`inventory/mixins.py` holds an abstract model mixin *and* two django-tables2
classes. Classifying it `presentation` makes
`inventory.abstract_models → inventory.mixins` a `domain-model → presentation`
edge; classifying it `domain-model` makes its own `core.tables` import one. That
rule has no baseline representation at any severity, so either choice would
leave the gate permanently red over a module that is simply doing two jobs.

It is therefore classified `domain-service`, which is true of the model-behaviour
half and leaves *both* crossings visible as ordinary recorded debt — `R-X1`
inbound and `R-V1` outbound — each carrying the same removal direction: split the
module, moving the two table classes into `inventory/tables.py`, which already
imports `core.tables` and is their only consumer. Recording both is strictly more
informative than a classification that hides one of them, and the split is a
behaviour-preserving move that belongs in its own reviewed change rather than in
the change that introduces the gate.

### What the gate cannot see

- **Dynamic imports.** `importlib.import_module` and `import_string` resolve
  names built at run time — `itambox/views/generic/table_config.py` reaches every
  application's `tables` module through an f-string, and
  `itambox/views/features.py` does the same for `filters`. No static heuristic
  over an f-string is sound, so the gate does not guess. It reports the count of
  such call sites in the substrate as information and records nothing.
  `apps.get_model()` is the sanctioned inversion and stays invisible on purpose.
- **Plugins.** The gate scans first-party code under `itambox/` only. It never
  reads `settings.PLUGINS`, never imports application or plugin code, and never
  walks `site-packages`. Nothing in its output says anything about a plugin.
- **Templates, TypeScript, and migrations.** Out of scope entirely.

The baseline therefore makes no completeness claim. It records what a static
first-party import graph can prove.

### Plugins and this policy

A plugin may import only the names ITAMbox documents in *Plugin Development →
API Reference* and *Extension Hooks*: `PluginConfig` from `itambox.plugins`;
`PluginModel` from `itambox.plugins.models`; `PluginTemplateContent` from
`itambox.plugins.views`; `PluginNavigationMenu` and `PluginNavigationItem` from
`itambox.plugins.navigation`; and the `registry` singleton from
`itambox.registry`, through its `register_plugin_template_content`,
`register_plugin_menu`, `register_plugin_menu_item`, and
`register_plugin_viewset` methods. `PluginConfig.graphql_schema` names a module
by dotted path in configuration rather than by import.

Everything else is internal: the rest of `itambox.*`, all of `core.*`, every
domain application, and every `Registry` method not listed above. Internal
modules may be renamed, merged, split, or deleted in any release without a
deprecation path, and this policy's own removal work will move some of them.

**Layer membership is a description, not a promise.** Classifying a module
`framework` or `kernel` says where it sits in ITAMbox's own dependency order. It
does not make the module importable by a plugin, and it confers no stability,
support, or compatibility commitment. Conversely, the documented plugin API
above is supported regardless of which layer its modules occupy.

## Alternatives considered

**An off-the-shelf import linter.** Rejected. The rules that turned out to
decide the outcome cannot be expressed by one: excluding `TYPE_CHECKING`
imports, separating module-top from function-body edges, suppressing the prefix
edge to an import-free package initialiser, and treating a module's own ancestor
packages as already imported. A third-party linter also has no baseline, no
owner field, and no removal-direction field, and it cannot run in the
dependency-free CI slot where the other gates run. Revisit when the first-party
module count passes roughly 1,500, or if the repository ever splits into
installable distributions.

**Merging `framework` and `kernel` into one `platform` layer.** Rejected. The
two names carry different diagnostics (`R-F*` versus `R-K*`), and merging them
would erase the distinction in every failure message. Marking the pair mutually
recursive gets the correctness without the loss.

**Classifying a package initialiser by its submodules.** Rejected as unstable:
adding one submodule would silently change a package's layer, its violations,
and the policy fingerprint. Initialisers inherit their parent package's
classification, and import-free ones are inert.

## References

- [Architecture policy](architecture-policy.md) — the operator's guide to the gate
- [Python import policy](python-import-policy.md) — import *placement*, and the
  annotation grammar this gate's `R-C3` rule reads
- [View patterns](view-patterns.md) — the `Membership`/`RoleGrant` write path and
  why the tenant boundary answers 404
- `scripts/architecture_policy.py` — the layers, matrix, rule registry, owner
  table, and fingerprint, as data
