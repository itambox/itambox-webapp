# Playwright qualification ownership

The repository-owned Playwright suite is organized by behavioral owner under
`itambox/tests/e2e/spec/`. `spec/apps/<name>/` owns first-party application
journeys; `spec/contracts/<name>/` owns cross-application contracts; and
`smoke`, `legacy-smoke`, `regressions`, `layout`, `accessibility`, and `external`
are separate qualification categories. The private per-app catalog remains
planning input; its layer accounting is kept in the ignored implementation
ledger rather than making CI depend on the private repository.

## Roles and safety

`playwright.config.ts` has independent setup projects for admin, operator,
viewer, and the retained aggregate operator, plus anonymous and reviewed
`remote-smoke` projects. Authentication-destructive tests create a fresh
context/session. The automatic fixture records page errors, console errors,
HTTP 5xx responses, an attested active tenant, retry-specific identity, and
reverse-order cleanup failures.

Destructive non-loopback execution requires all three values to agree:

```text
E2E_ALLOW_DESTRUCTIVE=1
E2E_TENANT_SLUG=<dedicated disposable tenant>
E2E_DESTRUCTIVE_TENANT_SLUG=<same dedicated disposable tenant>
```

Known shared targets remain blocked. The current implementation has no
server-side disposable-instance attestation; the client-side boundary is a
residual risk and is not a production-safety claim. Remote smoke is a separate
`@non-destructive` project and does not authorize mutation.

## Selection and certification

`scripts/e2e_scope_map.yaml` is strict JSON syntax and is parsed with the
standard library. `scripts/select_e2e_scopes.py` verifies exact Git objects,
merge-base, NUL-delimited rename-aware identities, canonical SHA-256 path
fingerprints, map drift, and safe path boundaries. Shared, unknown, or
ambiguous changes escalate to `full`; only an explicit safe-ignore-only change
can produce `none`.

`run-selected.mjs` revalidates the canonical selection and invokes the local
Playwright CLI through `spawn` with `shell: false` and discrete arguments.
`certify_e2e_run.py` compares discovery, execution, identities, setup projects,
statuses, retries, cleanup, and the complete on-disk spec tree. The stable
`E2E / Gate` job applies `check_e2e_gate.py` with `if: always()`; conditional
execution is never itself a green verdict.

## Workflow topology

The E2E workflow has three stable jobs:

```text
detect-e2e-scope -> e2e-selected (selected/full only)
                -> E2E / Gate (always)
```

Pull requests use the selector. Pushes to `main`, the retained `master` push,
schedule, manual dispatch, and reusable release qualification are authoritative
full events. Release preparation awaits the reusable E2E workflow at job level.
The initial run remains one worker and one disposable database.

## Force-full rollback

If selective classification or artifact identity becomes unreliable, make one
reviewed policy change in `scripts/e2e_scope_map.yaml`:

1. Set `rollback.force_full_pr_selection` from `false` to `true`.
2. Commit and push that single policy change.
3. Verify the detector emits `mode: full` for every PR and that `E2E / Gate`
   remains required and green only after full execution/certification.
4. Keep the selector output as diagnostic/report-only evidence while every PR
   runs the complete `spec` root.
5. After the incident is resolved and a fresh exact-head review is complete,
   revert only that boolean in a reviewed change and re-run the negative-control
   matrix before restoring selective PR execution.

Never remove the scope map, selector tests, certifier, app-owned assertions, or
aggregate gate as a rollback mechanism.
