# Python import policy

ITAMbox keeps imports at module top. A function-body ("local" or "inline")
import hides a dependency from the module's import graph, so it is allowed only
where a module-top import would actually break. Every remaining function-body
import is either annotated with a categorised reason or recorded as reviewed
debt in `scripts/local_import_baseline.json`.

`scripts/check_local_imports.py` enforces this. It parses source with Python's
AST and never imports application modules.

## The rule

Put imports at module top.

A function-body import is justified only when one of these four conditions
holds:

| Category | Meaning |
|---|---|
| `cycle` | A module-top import closes a real circular import. |
| `app-registry` | A module-top import raises `AppRegistryNotReady`, because the module is executed while Django is still loading apps. |
| `optional-dependency` | The dependency is absent in a supported environment (for example `python-magic` and `django-auth-ldap` on native Windows). |
| `heavy-import` | The import is expensive and the module is on a hot import path that usually does not need it. |

"It reads better here", "the module-level block is already crowded", "this keeps
the layers decoupled", and "the name is only used once" are not justifications.
Hoist those.

Reaching for `cycle` is a signal, not a solution: it records that two modules
depend on each other. Removing the cycle belongs to architecture-boundary work,
not to the change that adds the import.

## Annotating a justified import

Write the reason as a comment on the import, or on the line(s) immediately
above it:

```python
def filter_by_tenant(self):
    # inline import: cycle: core.managers <-> itambox.middleware at module load
    from itambox.middleware import get_current_user
```

The grammar is `# inline import: <category>: <reason>`. The plural
`# inline imports:` reads better when one comment covers a contiguous group of
imports, and the annotation applies to the whole group:

```python
def validate_role_grant(grant):
    # inline imports: cycle: core.auth <-> organization at load time
    from organization.models import RoleGrantScope, Tenant
    from organization.rbac import applicable_grants
```

A blank line ends the group. A comment that carries the `inline import:` marker
but no recognised category fails the gate — the marker is reserved for this
policy.

Name the modules involved. `# inline import: cycle: avoids a cycle` passes the
gate and tells a reviewer nothing.

## What the gate checks

```console
python scripts/check_local_imports.py
python scripts/check_local_imports.py --write-baseline
python -m unittest scripts.tests.test_local_imports
```

`itambox/` and `scripts/` are scanned. Migrations, vendored and generated trees,
and test modules are excluded. Tests are excluded deliberately: importing a
module inside a test under a patched environment is a legitimate isolation
technique, and test modules are not part of the application's import graph.

Only imports inside a function or method body are in scope. A class-body import
executes at module import time exactly like a module-top one, so it defers
nothing; Flake8 (`E402`) and review cover those.

The gate reports three things:

- **Unusable justification** — a comment carries the `inline import:` marker
  without a recognised `<category>: <reason>`. Always fails; never
  baselineable.
- **New function-body import** — an unannotated import that is not in the
  baseline. Fails.
- **Stale baseline** — a baselined import that is no longer present, because it
  was hoisted or annotated. Fails until the baseline is regenerated, so paid-down
  debt cannot silently become headroom for new debt.

Findings are identified by path, enclosing scope path, and the normalised import
statement. Physical line numbers are excluded on purpose: inserting a line above
existing debt must not read as a regression. Two identical statements in the
same scope are recorded as one identity with a count of two.

The baseline stores a SHA-256 fingerprint of the effective policy — schema
version, categories, marker grammar, scan targets, and exclusions. Loosening the
policy therefore invalidates the baseline instead of silently widening it.

The gate refuses to run on any interpreter other than canonical Python 3.12.
Statement normalisation is version-sensitive, so findings from another
interpreter are not comparable to the checked-in baseline. There are no
interpreter- or OS-specific exceptions.

### What the gate does not prove

An AST cannot prove that a claimed cycle is real or that a dependency is truly
heavy. The gate guarantees that every function-body import is either reviewed
pre-existing debt or carries an explicit, categorised justification. Whether the
justification is *true* is a review question, and a wrong one is a review defect
rather than a gate bypass — the annotation and the baseline diff are both
visible in the pull request.

The baseline also does not assert that its entries are unjustified, only that
they were never triaged. It is the inventory of work still to do.

## Adding or changing an import

1. Put the import at module top. If the application still loads and the tests
   pass, you are done.
2. If it breaks, decide which of the four categories applies, move the import
   back into the function, and annotate it with the module names involved.
3. If none of the categories applies, the import belongs at module top — solve
   the real problem instead of annotating around it.

## Paying down baselined debt

Hoist or annotate the import, then regenerate on canonical Python 3.12 from the
repository root and review the diff:

```console
python scripts/check_local_imports.py --write-baseline
```

`--write-baseline` accepts reductions only; it refuses to grandfather a new
function-body import into the baseline. Keep the cleanup and the regenerated
baseline in the same reviewed change.

Hoisting is a behavioural change to import order, so prove it before shipping:
read the target module's own module-level imports, then run `manage.py check`
and the test suite. Hoisting into a module whose imports are split around
executable code (pre-existing `E402` debt) also moves Flake8's baseline — do
that file's import-block cleanup as its own change.

## Current state

When this policy landed, production code held 821 function-body imports: 81
carrying a justification (50 `cycle`, 22 `app-registry`, 5 `heavy-import`,
4 `optional-dependency`) and 740 recorded as untriaged debt. Running the gate
prints the same breakdown for the current tree.

Two limits of that starting point are worth knowing:

- Annotations that predate this policy were normalised into the grammar with
  their original reasons intact; they were not re-adjudicated. Of the 50 `cycle`
  annotations, 24 correspond to a module-level back edge that static analysis
  can see; the other 26 are author assertions. Treat a `cycle` annotation as a
  claim to verify when you touch the code, not as proof.
- The baseline is an inventory of untriaged imports, not a verdict that they are
  unjustified. Some are genuinely justified and simply have not been annotated
  yet.

## Related gates

Four independent gates cover imports, and none replaces another:

- **Ruff** (`make format-check`) owns import *order* and formatting.
- **Flake8** (`scripts/check_flake8_baseline.py`) owns semantic lint, including
  `E402` and unused imports.
- **This gate** owns import *placement*: module top versus function body.
- **The architecture gate** (`scripts/check_architecture.py`) owns import
  *direction*: which layer may import which, and whether a `cycle` annotation's
  claim is supported by the measured graph. It reads this policy's annotation
  parser rather than reimplementing it, so a malformed `inline import:` comment
  stays this gate's finding. See
  [architecture-policy.md](architecture-policy.md).
