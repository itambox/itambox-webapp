# Migration verification

ITAMbox checks its unsquashed first-party migration graph into
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

The JSON records first-party graph node and edge counts, explicitly named
global roots and leaves, per-app roots and leaves computed from each app's
local edges, and per-migration
`RunPython`, `RunSQL`, and `SeparateDatabaseAndState` counts. For `RunPython`
and `RunSQL`, the parser records only whether a reverse argument is absent, is
syntactically a known `noop`, or is present. Those are syntactic facts: neither
a function name nor a noop reverse determines the operation's purpose.

Every migration containing one of those custom operations must also appear
exactly once in the checked, human-reviewed `SEMANTIC_DISPOSITIONS` policy.
The allowed dispositions are `required-fresh`, `upgrade-only`,
`safely-replaced-by-final-schema`, and `review-blocker`. Policy coverage is
bidirectional: an unclassified custom-operation migration fails the audit, as
does a policy entry for a migration without custom operations. The only
required fresh-install data migrations are
`assets.0003_seed_status_labels` and
`assets.0043_seed_depreciation_policies`;
`procurement.0004_setup_groups` is reviewed as upgrade-only.

`users.0010_user` is recorded as the special user-model bootstrap: its
`run_before` edge and all `AUTH_USER_MODEL` swappable dependents are included in
the first-party graph even though they are not ordinary tuple dependencies.

The syntactic inventory is generated; the semantic policy is reviewed source.
A clean audit proves their coverage and consistency, but does not prove that
SQL is portable, a reverse function is lossless, or a data operation preserves
production data.

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
predecessor identity. This harness establishes the current unsquashed graph
baseline. No arbitrary historical or production snapshot is supported. A
specific predecessor may be claimed only after that complete identity is
supplied, its blockers are resolved or explicitly accepted for that
predecessor, and both paths are tested.

## Parity and evidence

Migration-sensitive pull requests run on PostgreSQL 16 with Python 3.12 and the
locked `uv` environment. CI checks audit drift, missing model migrations,
Django system checks, applies the graph to a fresh database, and runs the test
suite with `--create-db`.

Before a future squash or replacement can be accepted, retain logs proving:

- fresh install reaches every checked leaf and includes every required seed;
- predecessor upgrade reaches the same Django project state and database
  schema as fresh install;
- row counts, primary and foreign keys, constraints, indexes, permissions, and
  content types match the declared parity expectations;
- upgrade-only and irreversible warnings have explicit disposition; and
- application checks and migration-sensitive tests pass on both paths.

Evidence must contain hashes and counts, never credentials, connection strings,
tokens, production row contents, or raw backups.

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
