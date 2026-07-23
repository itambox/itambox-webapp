# Migration verification

ITAMbox checks its historical and effective first-party migration graphs into
`scripts/migration_audit.json`. The artifact is an inventory and review aid, not
a replacement for Django's migration executor. `scripts/migration_audit.py`
parses migration source with Python's AST and never imports migration modules.

## Checked audit

Regenerate and check the inventory from the repository root:

```console
python scripts/migration_audit.py
python scripts/migration_audit.py --check
python -m unittest scripts.tests.test_migration_audit
```

The JSON records historical first-party graph node and edge counts, replacement
coverage, the effective replacement quotient, explicitly named global roots
and leaves, per-app roots and leaves computed from each app's local edges, and
per-migration
`RunPython`, `RunSQL`, `SeparateDatabaseAndState`, and `BtreeGistExtension`
counts. For `RunPython`
and `RunSQL`, the parser records only whether a reverse argument is absent, is
syntactically a known `noop`, or is present. Those are syntactic facts: neither
a function name nor a noop reverse determines the operation's purpose.

Every migration containing one of those custom operations must also appear
exactly once in the checked, human-reviewed `SEMANTIC_DISPOSITIONS` policy.
The allowed dispositions are `required-fresh`, `upgrade-only`,
`safely-replaced-by-final-schema`, and `review-blocker`. Policy coverage is
bidirectional: an unclassified custom-operation migration fails the audit, as
does a policy entry for a migration without custom operations. The historical
required fresh-install data migrations are
`assets.0003_seed_status_labels` and
`assets.0043_seed_depreciation_policies`. On the replacement path their
deterministic combined equivalent is
`assets.0100_issue88_shard_43_assets_seed`;
the historical and replacement asset-relation paths also require
`BtreeGistExtension` before the asset-reservation exclusion constraint;
`procurement.0004_setup_groups` is reviewed as upgrade-only.

`users.0010_user` is recorded as the special user-model bootstrap: its
`run_before` edge and all `AUTH_USER_MODEL` swappable dependents are included in
the first-party graph even though they are not ordinary tuple dependencies.

The syntactic inventory is generated; the semantic policy is reviewed source.
A clean audit proves their coverage and consistency, but does not prove that
SQL is portable, a reverse function is lossless, or a data operation preserves
production data.

## Coordinated transitional replacement

The issue #88 baseline keeps all 262 historical first-party migration files
unchanged and adds exactly 62 replacement shards. Their `replaces` lists form a
disjoint, complete partition of those historical nodes. One ordinary
post-transition migration follows the replacements. It removes the redundant
`core_webhookendpoint_name_9c6e0239_like` index that older installations retain
after the Core-to-Extras model move; the `DROP INDEX IF EXISTS` is a no-op on a
fresh database. The shards use the
reviewed graph-convex order recorded in their `issue88_shard_01` through
`issue88_shard_62` names and are explicitly linear: shard 1 depends on
`auth.0012_alter_user_first_name_max_length`, retains
`run_before = [('users', '0001_initial')]`, and every later shard depends on
the immediately preceding shard. Later shards also depend on their prior
same-app shard so Django sees one root per app. The shard 1 filename begins
with `0000` so Django resolves pre-replacement `users.__first__` dependencies
to the bootstrap rather than historical `users.0001_initial`.

Shard 1 creates only `users.User`. Final-state `CreateModel` operations are
placed in the documented early schema hosts, except for the remaining users
models, which wait until shard 49 after organization exists. Generated
deferred fields, indexes, and constraints are placed in the late relation
hosts. Required external `contenttypes` and `django_q` dependencies remain on
the shards whose operations use them.

Only shard 43 executes replacement-path application data. It consolidates the
status-label and depreciation-policy seeds. Every other historical custom
operation remains available for predecessor upgrades but is omitted from
replacement execution.

The post-transition migration is deliberately not part of `replaces`. Django
must execute it on a fully migrated predecessor as well as on a fresh database;
putting the cleanup inside a replacement shard would incorrectly mark it as
already applied on the predecessor.

## Known blockers

The audit records the current greenfield and upgrade-support blockers
explicitly: `organization.0026`, `organization.0027`, `organization.0034`,
`organization.0035`, `users.0008`, and
`users.0010_remove_usergroup_users_usergroup_unique_provider_name_active_and_more`.
The JSON uses each migration's full exact identifier. These blockers prevent a
blanket greenfield or historical-upgrade support claim; they are not inferred
from custom-operation syntax.

## Supported predecessor identity

An upgrade verification claim is valid only for a named predecessor snapshot.
Its identity is the combination of:

1. the immutable predecessor Git commit SHA;
2. the exact applied first-party rows from `django_migrations`;
3. the PostgreSQL major version;
4. a schema-only dump hash; and
5. a separately stored, sanitized fixture or backup hash used for parity tests.

All five values must be recorded in the release evidence. A branch name,
version label, mutable database, or schema hash alone is not a supported
predecessor identity. The sole intended predecessor for this transition is the
fully migrated commit `deef4c8bf3fe678edecacf2c523d7bd0dcb6f6ef`. All 262
historical first-party rows must be present in `django_migrations`; leaf-only
or partially migrated snapshots are unsupported. No arbitrary historical or
production snapshot is supported.

This documents the transition contract. A release may claim runtime support
only when its retained evidence shows both the fresh and exact-predecessor
PostgreSQL paths passing the checks below.

## Parity and evidence

Migration-sensitive pull requests run on PostgreSQL 16 with Python 3.12 and the
locked `uv` environment. CI checks audit drift, missing model migrations,
Django system checks, applies the graph to a fresh database, and runs the test
suite with `--create-db`.

Odin verification must retain logs proving:

- fresh install reaches every checked leaf and includes every required seed;
- predecessor upgrade reaches the same Django project state and semantically
  equivalent database schema as fresh install; schema comparison ignores
  physical constraint/index names retained by historical table moves but
  compares tables, columns, definitions, expressions, and multiplicity;
- protected application and media rows retain their hashes and counts;
  required seed rows must be present on both paths, while additional preserved
  predecessor rows are allowed;
- primary and foreign keys, constraints, indexes, permissions, content types,
  and PostgreSQL extensions match the declared parity expectations;
- upgrade-only and irreversible warnings have explicit disposition; and
- application checks and migration-sensitive tests pass on both paths.

Evidence must contain hashes and counts, never credentials, connection strings,
tokens, production row contents, or raw backups.

Execute and record these checks together with backup restore, protected-value
evidence, fresh-install parity, restore-first rollback, and re-upgrade by
following the operator-facing
[Recovery qualification drill](../operations/recovery-drill.md).

## Prerequisites and rollback

Local database verification requires Python 3.12, `uv` 0.11.31, the locked
dependency set, and an isolated PostgreSQL 16 database whose role may create
the pytest test database. No production or shared database is suitable.

Before an upgrade, take and verify an encrypted database backup using the
operator's normal secret store. Rollback means stopping writes, restoring that
verified predecessor backup into a clean database, deploying the exact
predecessor commit, and re-running its checks. Do not rely on reversing
migrations classified as upgrade-only or irreversible. Never place backup
locations, keys, passwords, or restore commands containing secrets in CI logs
or review evidence.
