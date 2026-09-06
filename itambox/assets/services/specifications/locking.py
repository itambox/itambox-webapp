"""PostgreSQL transaction locks shared by specification commands.

The catalogue lock is deliberately one coarse lock.  Value and Asset type
value-switch commands take it in shared mode; future definition, composition,
and import commands must take the same key exclusively.  The literal pair is
stable across processes and deployments: PostgreSQL advisory locks are not
based on Python's process-randomized hash implementation.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from django.db import DEFAULT_DB_ALIAS, connections
from django.db.utils import NotSupportedError

# "ITAM SPEC" encoded as two signed PostgreSQL int4 arguments.  Do not derive
# this with hash(); every command in every process must address this same lock.
SPECIFICATION_CATALOGUE_LOCK_KEY: tuple[int, int] = (0x4954414D, 0x53504543)


@contextmanager
def catalogue_transaction_lock(
    *,
    exclusive: bool = False,
    using: str = DEFAULT_DB_ALIAS,
) -> Iterator[None]:
    """Hold the specification-catalogue advisory lock until transaction end.

    Callers must already be inside ``transaction.atomic(using=...)``.  A
    transaction-scoped advisory lock cannot coordinate an autocommit statement,
    and silently accepting that shape would make the command appear protected
    while releasing the lock immediately.
    """
    if type(exclusive) is not bool:
        raise TypeError("exclusive must be a bool")
    database = connections[using]
    if database.vendor != "postgresql":
        raise NotSupportedError("specification catalogue locking requires PostgreSQL")
    if not database.in_atomic_block:
        raise RuntimeError("catalogue_transaction_lock requires an active transaction")

    function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    with database.cursor() as cursor:
        cursor.execute(
            f"SELECT {function}(%s, %s)",
            SPECIFICATION_CATALOGUE_LOCK_KEY,
        )
    yield


__all__ = ["SPECIFICATION_CATALOGUE_LOCK_KEY", "catalogue_transaction_lock"]
