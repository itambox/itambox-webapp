# Python exception policy

ITAMbox treats exception handling as part of its security boundary. A broad handler can be appropriate at an external integration or batch boundary, but an unobservable fallback can turn failed authentication, authorization, tenant resolution, encryption, or mutation into apparent success.

The repository therefore uses two controls:

1. a no-growth identity baseline for existing broad and pass-only handlers; and
2. an unbaselinable hard rule for security-sensitive silent failures.

Run the policy locally from the repository root:

```bash
make exception-check
```

## Catch the exception you can name

Prefer the narrowest documented exception type. `except Exception` is acceptable only where the upstream exception set cannot be enumerated or where one failed unit must be isolated from a reviewed batch. `except BaseException` is not appropriate for normal application recovery because it also intercepts process-control exceptions.

Bare `except:` remains owned by Flake8 E722. The exception-policy gate cross-checks it so the two controls cannot drift apart.

A handler is **pass-only** when its body contains no observable or explicit action. This includes a literal `pass`; the gate also recognizes equivalent empty shapes. Pass-only handlers are tracked regardless of the exception type.

`contextlib.suppress(Exception)` and `contextlib.suppress(BaseException)` are treated as broad handlers rather than as an escape from this policy.

## Handler taxonomy

The gate reports each finding in one of these layers:

| Layer | Purpose | Expected disposition |
|---|---|---|
| Domain | Invariants and core business rules | Catch narrowly or propagate |
| Application service | Coordinates domain mutation | Roll back/propagate; isolate only reviewed secondary effects |
| Integration | LDAP, OIDC, mail, webhooks, remote APIs | Narrow where possible; otherwise log context and return a documented failure |
| Task | Background and batch execution | Record item failure with actor/tenant context; do not report false success |
| Presentation | Views, forms, tables, rendering | Return an explicit user-visible failure or a documented rendering degradation |
| Infrastructure | Caches, middleware, registries, framework adapters | Preserve the security property while degrading availability explicitly |

Typed failure contracts for LDAP, OIDC, import, mail, and other remote
boundaries are defined in the [external integration error
contract](integration-error-contracts.md). This policy does not introduce a
repository-wide `Result` abstraction.

## Security-sensitive scopes

The following domains are checked independently of the baseline:

- cryptography and key management;
- authentication;
- authorization and permission resolution;
- tenant resolution and tenant-scoping managers;
- configuration loading; and
- handlers lexically contained in `transaction.atomic()` or a function decorated with `@atomic` / `@transaction.atomic`.

Crypto, authorization, tenant resolution, and configuration loading must propagate failure after any cleanup. An annotation cannot unlock them.

Authentication and transactional code have two narrow, observable exceptions:

| Domain | Permitted category | Requirement |
|---|---|---|
| Authentication | `availability-tradeoff` | The failed cache/dependency cannot grant access; log and force safe recomputation |
| Authentication | `boundary-isolation` | Provider failure is logged and produces the documented non-authenticated result |
| Transactional | `task-isolation` | A failed item is logged/recorded while the reviewed batch continues |
| Transactional | `boundary-isolation` | A secondary external effect is logged and must not invalidate an already-correct mutation |

A permitted category never makes `pass`, a silent return, or a silent fallback acceptable. The handler must be classified as log-only; propagating handlers need no annotation. A `raise` counts as propagation only when it is unconditional at the top level of the handler body; a conditional re-raise alongside a return or fallback remains swallowing. The policy fingerprint includes this matrix, so changing it requires a reviewed baseline regeneration.

## Justified broad handlers

Use an in-place annotation immediately before the `except` clause:

```python
try:
    provider_call()
# broad except: boundary-isolation: provider exposes no stable exception hierarchy
except Exception:
    logger.exception(
        "Provider call failed for tenant_id=%s actor_id=%s",
        tenant.pk,
        request.user.pk,
    )
    return documented_failure
```

Recognized categories are:

- `cleanup-reraise` — restore state or release a resource, then re-raise;
- `boundary-isolation` — isolate an integration whose exception set is not enumerable;
- `task-isolation` — one item must not abort a reviewed batch;
- `render-degrade` — degrade one cell or widget instead of failing the page;
- `availability-tradeoff` — fail open only on a non-authorization dependency while preserving the authorization result.

The reason must explain the actual boundary, not restate the category. Unknown categories and malformed markers fail CI. An annotation is review evidence, not proof that the reason is true.

## Logging and failure contracts

Allowed broad catches must do one of the following:

- log and re-raise;
- log and return the boundary's documented failure value; or
- log/record an item failure and continue a reviewed batch.

Include non-secret identifiers that make the failure actionable: tenant ID, actor/user ID, object ID, task ID, and provider name where available. Do not log passwords, tokens, encryption keys, assertion bodies, raw configuration JSON, or encrypted/plaintext field values.

Management commands and tasks must not print a success summary or exit zero after a partial security-sensitive failure. Transactional commands should raise after detecting errors so successful partial writes are rolled back.

## Identity baseline

`scripts/exception_baseline.json` records:

- path;
- enclosing scope;
- normalized exception type;
- handler classification; and
- structural body fingerprint.

Line numbers are diagnostic only and are not identity. Inserting or removing unrelated lines therefore does not churn the baseline. Structural changes, moves, and renames are reported for review. Removed debt makes the baseline stale so paid-down findings cannot become headroom for unrelated new debt.

After deliberately removing or annotating existing debt, regenerate with canonical Python 3.12:

```bash
make exception-baseline
```

Write mode refuses new identities, malformed annotations, bare handlers, and security-sensitive silent failures. It cannot be used to approve new debt.

## Verification

The stdlib-only policy suite runs before dependency installation in CI:

```bash
uv run --locked --only-group dev python -m unittest discover \
  --start-directory scripts/tests --top-level-directory . --pattern 'test_*.py'
```

The lint job then runs the gate against the repository. Pre-commit runs the same command with Python 3.12. Runtime fixes require failure-path tests in the owning Django app; coverage alone is not evidence that authentication, tenant, rollback, or secret-handling behavior is correct.
