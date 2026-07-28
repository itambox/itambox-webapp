# Architecture policy

ITAMbox declares which layer every first-party module belongs to and which
direction a dependency may run. `scripts/check_architecture.py` enforces it. The
rules themselves — layers, matrix, and the reasoning behind them — are in
[ADR 0001](adr-0001-architecture-boundaries-and-layering.md); this page is the
operator's guide to the gate.

The gate is deterministic and AST-based. It never imports application modules,
never touches the network, and never probes the filesystem while resolving an
import. It scans first-party code under `itambox/` only.

## The rule

Imports run downwards and sideways, never upwards, and never in a circle.

- Nothing imports `composition`. URLconfs, `apps.py`, `admin.py`, settings, and
  the GraphQL root are wired *into*, never *from*.
- Nothing below `presentation` imports `presentation`. A model may not depend on
  a form, a table, a view, or a presentation helper — and unlike every other
  rule, that one has no baseline representation at any severity.
- `framework` and `kernel` are domain-blind. The substrate may recurse within
  itself; it may never name a domain application.
- Generic (`itambox.views.*`, `core.tables.*`) presentation is held to the
  framework standard, not the domain standard.
- Cross-application model coupling is allowed only for the pairs declared in
  `CROSS_DOMAIN_MODEL_EDGES`.

## What the gate checks

```console
# The gate, exactly as CI runs it (from the repository root):
uv run --locked --only-group dev python scripts/check_architecture.py

# Or through Make:
make architecture-check

# Why are these two modules coupled?
uv run --locked --only-group dev python scripts/check_architecture.py --explain core.managers organization.access

# Sizing and triage output. Never a pass, and never wired into CI:
uv run --locked --only-group dev python scripts/check_architecture.py --report-only --format json

# Normalise the baseline after a reviewed cleanup, canonical Python 3.12 only:
make architecture-baseline

python -m unittest scripts.tests.test_architecture_policy scripts.tests.test_check_architecture
```

Migrations, vendored and generated trees, and test modules are excluded, mirroring
`scripts/check_local_imports.py`. Discovery is a pure path walk: a directory is a
package because it contains a discovered file, never because it carries an
`__init__.py`. `core/views/` and `users/api/` are implicit namespace packages,
and a discovery rule that required an initialiser would silently drop the GraphQL
endpoint and the whole SCIM surface.

### How a name decides a layer

A module's layer comes from its dotted name and nothing else — never from the
filesystem, never from an import. An exact entry in `MODULE_LAYER_OVERRIDES`
wins; otherwise the name is read segment by segment, and each segment is reduced
to at most three tokens: **the whole segment, the text before its first
underscore, and the text after its last underscore**.

So `urls_audits` yields `urls_audits`, `urls`, `audits`, and `provider_urls`
yields `provider_urls`, `provider`, `urls`. If any token of the **last** segment
is `admin`, `apps`, `asgi`, `urls`, or `wsgi`, the module is `composition`. That
is what makes `assets.urls_audits` and `users.api.scim.provider_urls` both read
as URL configuration, whichever side of the underscore the word lands on —
without either needing a hand-written override. Failing that, a domain
application's segments after the application name are walked left to right and
the first token naming a layer keyword (`models`, `forms`, `views`, `api`,
`services`, `tables`, and the rest) decides; under `core` and `itambox` the
longest matching dotted prefix decides.

A *middle* token is not examined: `stock_admin_report` yields
`stock_admin_report`, `stock`, and `report`, and is not composition. A name that
matches nothing gets no default — the gate exits 2 and asks for an override.

One consequence is worth knowing when naming a module. A module named for wiring
*is* wiring as far as the gate is concerned, and `composition` may import
anything, so a helper called `admin_utils` or `base_urls` would sit outside the
direction rules its neighbours obey. It cannot become a route around the matrix
for anything else, because nothing may import `composition` — but if a module is
not a composition root, keep those five words out of its first and last token.

### Two graphs

| Graph | Contents | Blocks |
|---|---|---|
| module-top | Imports that run when the module is imported, including class bodies, module-level `if`/`try`/`with` blocks, and re-exporting package initialisers | `R-C1` plus every matrix rule |
| effective | module-top plus function-body imports | `R-CE1` plus every matrix rule, plus `R-C3` |
| typing-only | `if TYPE_CHECKING:` bodies | nothing; counted and printed |

Both blocking graphs block from the first run. Moving an import into a function
defers *when* a coupling happens, not *whether* it exists.

An import's kind is decided by its enclosing scope and by nothing else. A
class-body import executes at class-definition time, so it is module-top — the
same boundary `scripts/check_local_imports.py` draws, and a shared-behaviour test
pins the two gates together. A `try:`/`except ImportError:` at module top is
still module-top: guarding an import does not defer it. No condition is ever
evaluated, so both legs of `core/settings/__init__.py`'s `from .prod import *` /
`from .dev import *` are edges and `ITAMBOX_ENV` is not read. Only a literal
`if TYPE_CHECKING:` or `if typing.TYPE_CHECKING:` is special-cased; any other
spelling yields a blocking edge rather than a silent exclusion.

### Package initialisers

`pkg/__init__.py` is its own node, `pkg.__init__`, so a package name and its
initialiser are never confused. Importing `a.b.c` executes `a/__init__.py` and
`a/b/__init__.py`, so the gate emits an edge to those initialisers — but only
when the initialiser has first-party imports of its own, and never for the
importing module's own ancestor packages. By the time a module's body runs every
package above it is already imported, so `assets.forms.asset_form` importing
`assets.forms.fields` creates no coupling to `assets.forms.__init__`. Counting it
would report every ordinary re-exporting package as a cycle.

An import-free initialiser is the inert `package-init` sentinel: it is reported
in the census, it is guaranteed never to be the endpoint of an edge, and it has
no layer. The moment anybody adds a first-party import to one, it loses the
sentinel and the gate demands a classification.

## The findings

| Rule | Meaning |
|---|---|
| `R-F*` / `R-K*` | The domain-blind substrate named a domain application, a platform service, or an integration |
| `R-S*` / `R-I*` | A platform service or an integration reached into a domain or into presentation |
| `R-M1` | A domain model imported presentation. **Never baselineable** |
| `R-M2`, `R-X1`, `R-X2`, `R-X3` | A domain model reached sideways or upwards, or coupled to an undeclared application |
| `R-V*` / `R-P*` | A domain service or generic presentation reached into presentation, composition, or a domain |
| `R-C1` / `R-CE1` | A new import cycle in the module-top / effective graph |
| `R-C2` | A `cycle` annotation the graph supports, whose component is not recorded in the baseline |
| `R-C3` | A `cycle` annotation the measured graph does not support |
| `R-C4` | Reserved for typing-only coupling. Inactive; the loader rejects any row citing it |
| `R-DOC1` | A relative markdown link in the policy documents does not resolve on disk |

### `R-C3` and the inline-import gate

`scripts/check_local_imports.py` owns the annotation grammar and the four
categories. This gate reads its parser rather than reimplementing it, so a
malformed `# inline import:` comment is that gate's finding and never appears
here.

What this gate adds is whether a `cycle` claim is *true*. A claim is supported
when the annotated module and the module it names lie in the same strongly
connected component — of the module-top graph, or failing that of the effective
graph. That is deliberately weaker than "the target imports the source back":
most deferrals around the tenancy kernel are defensive, the cycle genuinely
exists in the component but not on the specific pair, and reporting those as
lies would demand a refactor this policy explicitly defers. It is still strong
enough that a `cycle` annotation on an acyclic import fails.

Findings are reported per annotation group, keyed on `(path, scope, statement)`
exactly as `local_import_baseline.json` keys them — no line numbers, so inserting
a line above existing debt is not a regression. One comment covering a contiguous
run of imports is one finding, naming every unsupported target under it.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | The graph matches the checked-in baseline |
| 1 | A policy regression: a new cycle, a new forbidden edge, an unsupported cycle claim, a broken documentation link, or a stale baseline |
| 2 | A result nobody should trust: a malformed baseline, a drifted policy fingerprint, an unclassifiable module, an unparseable source file, or a non-canonical interpreter |

Findings go to stdout; gate failures go to stderr.

`--report-only` is an inventory mode for triage. It prints
`REPORT ONLY — NOT A PASS` on stderr before and after the findings and always
exits 0, which is exactly why it must never appear in CI or pre-commit wiring —
`scripts/tests/test_ci_workflow_policy.py` asserts that it does not.

`--format json` gives stdout to exactly one JSON document and nothing else. The
findings are all in the payload, so the human narrative is not suppressed; it
moves to stderr, along with the report-only banners. Exit codes are unchanged,
so `check_architecture.py --format json | jq` is safe on a failing tree.

## The baseline

`scripts/architecture_baseline.json` records the coupling that existed when the
gate landed, in three sections: `cycles`, `layer_exceptions`, and
`unsupported_cycle_claims`. Every row carries an owner, a removal issue, and a
removal direction, so the file is a work list rather than a suppression list.

| Field | Rule |
|---|---|
| `id` | Derived from the identity, never authored. A hand-edited `id` fails |
| `owner` | An `area:*` label from `AREA_LABELS`, **and** equal to what the policy's prefix table derives. Refresh the label set with `gh label list --json name -q '.[].name'` |
| `removal_issue` | A positive integer. `0` is the bootstrap sentinel and is refused |
| `removal_direction` | At least 40 characters, and not `TBD`, `TODO`, `n/a`, or another placeholder |
| `accepted_reason` | Non-empty |
| `notes` | Required on any cycle spanning three or more modules: a module list alone does not tell anyone which edge to delete |
| `count` | Positive integer, exact. An increase is a regression; a decrease makes the baseline stale |
| `policy_sha256` | The fingerprint of the effective policy — layers, matrix, rule registry, overrides, cross-domain allowlist, area labels, owner table, targets, exclusions, and the annotation grammar |

Owners are derived rather than typed: a longest dotted-prefix match on the source
module, then on the target, then a hard failure. Deriving them keeps a
hundred-row baseline reviewable and stable across regeneration, and the derived
value is written into every row so it stays visible in the diff.

Source-first is the whole point: the source is the module whose import has to
move, so its area does the work. A cycle-claim row therefore records the dotted
`source` module alongside its `path`, and the loader recomputes the module the
path denotes and refuses the row if the two disagree — an owner derived from an
unchecked `source` would be worse than no owner at all. Without it, every claim
naming an `organization` module would be attributed to `area:organization`
regardless of which application actually wrote the deferred import.

Loosening the policy invalidates the baseline instead of silently widening it.

### What can never be recorded

- Any `domain-model → presentation` edge (`R-M1`), at any severity, in either
  graph, including through `--write-baseline`.
- Any newly observed identity. New debt is hand-reviewed into the file first.
- Any row on the typing-only graph, or citing the reserved `R-C4`.
- Any row whose rule is not in the closed matrix registry.
- A broken documentation link (`R-DOC1`) — it is fixed, not accepted.
- An unclassifiable module. The answer is a `MODULE_LAYER_OVERRIDES` entry, and
  the gate exits 2 rather than defaulting.

## Adding or changing an import

1. Write the import at module top and run `make architecture-check`.
2. If it fails, read the rule. The message names the source layer, the target
   layer, and the file and line, and `--explain <source> <target>` prints the
   shortest chain in each graph.
3. Fix the direction. The usual moves are `apps.get_model()` for a model the
   substrate needs, a registry hook the platform publishes and the domain
   registers with, or moving the shared helper down a layer.
4. Do not move the import into a function to hide it. Both graphs block, so the
   only thing that changes is which rule identifier fails.

## Paying down baselined debt

Remove the edge or the cycle, then regenerate on canonical Python 3.12 from the
repository root and review the diff:

```console
make architecture-baseline
```

`--write-baseline` accepts reductions only. It drops rows that are no longer
observed, carries every human-authored field forward verbatim, re-derives the
owner, and refuses to add a newly observed identity — so the only path to a new
accepted exception runs through a diff a reviewer sees. Keep the cleanup and the
regenerated baseline in the same reviewed change.

A reviewed *policy* edit — a new override, a matrix change, another declared
cross-application pair — moves the fingerprint, which invalidates the baseline
for a normal check. `--write-baseline` is the only mode that accepts a drifted
fingerprint, and it says so on stdout when it does. It still validates every
other field and still runs the ratchet against the recorded rows, so changing
the policy never becomes an amnesty for debt added in the same commit.

Bootstrapping a baseline that does not exist writes every observed row with
`removal_issue: 0` and `removal_direction: "TODO"` and exits 1. The scaffold is
structurally incapable of passing: those sentinels are exactly what the loader
refuses.

## Limitations

The gate reports what a static first-party import graph can prove, and no more.

- **Dynamic imports are not guessed.** `importlib.import_module` and
  `import_string` build names at run time —
  `itambox/views/generic/table_config.py` reaches every application's `tables`
  module through an f-string, and `itambox/views/features.py` does the same for
  `filters`. Any heuristic over an f-string is unsound, so the gate reports the
  number of such call sites in the substrate as information and records nothing.
  `apps.get_model()` is the sanctioned inversion and stays invisible.
- **Re-exports are not chased.** Importing a name from `itambox.views.generic`
  is an edge to that package's initialiser, not to the module the name came from.
- **`scripts/check_architecture.py` scans first-party code under `itambox/`
  only.** It never loads, inspects, or reports on installed plugins, and a plugin
  importing an internal module will not appear in any finding or baseline row.
  Nothing in this gate's output should be read as evidence that a plugin's
  imports are supported; the supported surface is enumerated in
  [ADR 0001](adr-0001-architecture-boundaries-and-layering.md).

## Related gates

Four independent gates cover imports, and none replaces another:

- **Ruff** (`make format-check`) owns import *order* and formatting.
- **Flake8** (`scripts/check_flake8_baseline.py`) owns semantic lint, including
  `E402` and unused imports.
- **The import-placement gate** (`scripts/check_local_imports.py`, documented in
  [python-import-policy.md](python-import-policy.md)) owns module top versus
  function body, and owns the annotation grammar.
- **This gate** owns import *direction*: which layer may import which, whether
  the graph has a cycle, and whether a `cycle` annotation's claim is supported by
  the measured graph.

There is a pleasing symmetry worth stating: the architecture gate is itself
subject to the import-placement gate it cross-checks. `scripts/` is in that
gate's scan targets, so neither of these two scripts may contain a function-body
import.

Neighbouring policies: [exception policy](exception-policy.md),
[test coverage policy](test-coverage-policy.md).
