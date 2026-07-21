"""Cross-instance invalidation for request-local authorization caches."""

import contextvars
import logging
from uuid import uuid4

from django.core.cache import cache
from django.db import transaction


logger = logging.getLogger(__name__)

_CACHE_KEY_PREFIX = 'itambox:authz-version:'
_TOPOLOGY_CACHE_KEY = 'itambox:authz-topology-version'
_LOCAL_VERSION_ATTR = '_authorization_cache_version'
_LOCAL_SYNC_TOKEN_ATTR = '_authorization_cache_sync_token'
_request_invalidation_state = contextvars.ContextVar(
    'authorization_request_invalidation_state',
    default=None,
)
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


def begin_authorization_request(request_id):
    """Create isolated invalidation epochs and return their reset token."""
    return _request_invalidation_state.set((request_id, {}, 0))


def end_authorization_request(token=None):
    """Restore the enclosing authorization context at request completion."""
    if token is None:
        _request_invalidation_state.set(None)
    else:
        _request_invalidation_state.reset(token)


def _request_state():
    """Return invalidation epochs scoped to the active HTTP request."""
    from itambox.middleware import get_current_request_id

    request_id = get_current_request_id()
    if request_id is None:
        return None
    state = _request_invalidation_state.get()
    if state is None or state[0] != request_id:
        state = (request_id, {}, 0)
        _request_invalidation_state.set(state)
    return state


def _request_sync_token(user_id):
    state = _request_state()
    if state is None:
        return None
    request_id, user_epochs, topology_epoch = state
    return request_id, user_epochs.get(user_id, 0), topology_epoch


def _mark_request_user_invalidated(user_id):
    state = _request_state()
    if state is None:
        return
    request_id, user_epochs, topology_epoch = state
    user_epochs = dict(user_epochs)
    user_epochs[user_id] = user_epochs.get(user_id, 0) + 1
    _request_invalidation_state.set((request_id, user_epochs, topology_epoch))


def _mark_request_topology_invalidated():
    state = _request_state()
    if state is None:
        return
    request_id, user_epochs, topology_epoch = state
    _request_invalidation_state.set((request_id, user_epochs, topology_epoch + 1))


def clear_local_authorization_cache(user):
    """Discard every authorization value cached on one User instance."""
    for attr in list(user.__dict__):
        if attr in (_LOCAL_VERSION_ATTR, _LOCAL_SYNC_TOKEN_ATTR) or attr.startswith(
            _LOCAL_CACHE_PREFIXES
        ):
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
    _mark_request_user_invalidated(user_id)
    if hasattr(user, '__dict__'):
        clear_local_authorization_cache(user)
    _publish_user_version(user_id)
    _repeat_after_commit(
        lambda: _publish_user_version(user_id),
        using=using,
    )


def invalidate_authorization_topology(*, using=None):
    """Invalidate permission caches affected by tenant/group topology changes."""
    _mark_request_topology_invalidated()
    _publish_topology_version()
    _repeat_after_commit(_publish_topology_version, using=using)


def synchronize_authorization_cache(user):
    """Clear local values when another model instance/process changed RBAC.

    A successful shared-generation read is reused for the remainder of one
    HTTP request. Write-side invalidation advances request-local user/topology
    epochs, so a view that mutates RBAC and immediately rechecks permissions —
    even through another Python instance of the same user — performs a fresh
    shared read. Outside request middleware, and while the shared cache is
    unavailable, every call keeps the conservative recompute behaviour.
    """
    # ``CurrentUserMiddleware`` assigns a fresh request id and resets it on
    # response, so this shortcut cannot leak across requests or async contexts.
    # Non-request callers keep the conservative always-check behaviour.
    sync_token = _request_sync_token(user.pk)
    previous_sync_token = getattr(user, _LOCAL_SYNC_TOKEN_ATTR, None)
    if sync_token is not None and previous_sync_token == sync_token:
        return
    crossed_request_boundary = (
        sync_token is not None
        and previous_sync_token is not None
        and previous_sync_token[0] != sync_token[0]
    )
    request_local_invalidation = sync_token is not None and any(sync_token[1:])

    try:
        keys = (_cache_key(user.pk), _TOPOLOGY_CACHE_KEY)
        versions = cache.get_many(keys)
        version = tuple(versions.get(key) for key in keys)
        cache_available = True
    except Exception:
        # A fresh value on every check prevents stale authorization while the
        # shared cache is unavailable, at the cost of temporarily re-querying.
        version = (uuid4().hex, uuid4().hex)
        cache_available = False

    if (
        crossed_request_boundary
        or request_local_invalidation
        or getattr(user, _LOCAL_VERSION_ATTR, object()) != version
    ):
        clear_local_authorization_cache(user)
        setattr(user, _LOCAL_VERSION_ATTR, version)

    if cache_available and sync_token is not None:
        setattr(user, _LOCAL_SYNC_TOKEN_ATTR, sync_token)
