"""Cross-instance invalidation for request-local authorization caches."""

import logging
from uuid import uuid4

from django.core.cache import cache
from django.db import transaction


logger = logging.getLogger(__name__)

_CACHE_KEY_PREFIX = 'itambox:authz-version:'
_TOPOLOGY_CACHE_KEY = 'itambox:authz-topology-version'
_LOCAL_VERSION_ATTR = '_authorization_cache_version'
_LOCAL_CACHE_PREFIXES = (
    '_perms_tenant_',
    '_group_scope_tenants_',
    '_all_accessible_scope_tenants',
    '_all_accessible_group_ids',
    '_accessible_tenant_ids',
    '_applicable_grants',
    '_all_accessible_perms',
    '_tenant_permissions_map',
)


def _cache_key(user_id):
    return f'{_CACHE_KEY_PREFIX}{user_id}'


def clear_local_authorization_cache(user):
    """Discard every authorization value cached on one User instance."""
    for attr in list(user.__dict__):
        if attr == _LOCAL_VERSION_ATTR or attr.startswith(_LOCAL_CACHE_PREFIXES):
            delattr(user, attr)


def _publish_user_version(user_id):
    try:
        cache.set(_cache_key(user_id), uuid4().hex, timeout=None)
    except Exception:
        # A cache outage must not turn an authorization write into a 500. Reads
        # fail closed to uncached resolution in synchronize_authorization_cache.
        logger.warning(
            'Could not publish authorization cache invalidation for user %s',
            user_id,
            exc_info=True,
        )


def _publish_topology_version():
    try:
        cache.set(_TOPOLOGY_CACHE_KEY, uuid4().hex, timeout=None)
    except Exception:
        logger.warning(
            'Could not publish authorization topology invalidation',
            exc_info=True,
        )


def _repeat_after_commit(callback, *, using=None):
    """Publish a final generation after transactional writes become visible.

    The immediate bump invalidates same-request and same-process caches. A
    second, commit-time bump closes the race where another request observes the
    first generation while the database still exposes the pre-write state and
    caches that state under what would otherwise be the final generation.
    """
    connection = transaction.get_connection(using)
    if connection.in_atomic_block:
        transaction.on_commit(callback, using=using)


def invalidate_user_authorization_cache(user, *, using=None):
    """Invalidate cached authorization for every instance of ``user``.

    ORM relations return distinct Python objects, so clearing only the User
    instance reached by a signal leaves another instance in the same request
    stale. The shared generation token lets every process detect the write on
    its next permission check. Production already requires a shared Valkey/
    Redis cache for security-sensitive state.
    """
    user_id = getattr(user, 'pk', user)
    if user_id is None:
        return
    if hasattr(user, '__dict__'):
        clear_local_authorization_cache(user)
    _publish_user_version(user_id)
    _repeat_after_commit(
        lambda: _publish_user_version(user_id),
        using=using,
    )


def invalidate_authorization_topology(*, using=None):
    """Invalidate permission caches affected by tenant/group topology changes."""
    _publish_topology_version()
    _repeat_after_commit(_publish_topology_version, using=using)


def synchronize_authorization_cache(user):
    """Clear local values when another model instance/process changed RBAC.

    Always re-reads the two-key shared generation pair and compares it against
    what this object last saw — there is no "already synced, skip" shortcut.
    Write-side signal handlers publish invalidation by user id only (see
    ``organization/signals.py``); they cannot reach back into a Python object
    already held by this process. A one-shot-per-object skip therefore made a
    long-lived ``user`` instance (a view that writes a RoleGrant/Membership/
    Role and immediately rechecks ``has_perm`` on the same object, or a test
    reusing ``self.user`` across a mutation) permanently blind to its own
    write once it had synced a single time — including under a cache outage,
    where every call must recompute rather than trust a local memo made
    before the outage started. The request-local memos this call gates
    (``_applicable_grants``, ``_tenant_permissions_map``, ``_perms_tenant_*``,
    ...) are what make repeated ``has_perm`` checks cheap; this is a two-key
    ``cache.get_many`` round trip, negligible next to the grant-walk queries
    those memos avoid.
    """
    try:
        keys = (_cache_key(user.pk), _TOPOLOGY_CACHE_KEY)
        versions = cache.get_many(keys)
        version = tuple(versions.get(key) for key in keys)
    except Exception:
        # A fresh value on every check prevents stale authorization while the
        # shared cache is unavailable, at the cost of temporarily re-querying.
        version = (uuid4().hex, uuid4().hex)

    if getattr(user, _LOCAL_VERSION_ATTR, object()) != version:
        clear_local_authorization_cache(user)
        setattr(user, _LOCAL_VERSION_ATTR, version)
