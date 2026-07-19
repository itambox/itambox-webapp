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
    """Clear local values when another model instance/process changed RBAC."""
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
