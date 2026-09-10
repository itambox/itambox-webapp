"""App-registered hard-purge handlers for generic recycle-bin callers."""

_PURGE_HANDLERS = {}


class TombstonePurgeBlocked(RuntimeError):
    """Raised when a permanent schema tombstone reaches a hard-purge caller."""


def register_purge_handler(model_label, handler):
    existing = _PURGE_HANDLERS.get(model_label)
    if existing is not None and existing is not handler:
        raise RuntimeError(f"A purge handler is already registered for {model_label}.")
    _PURGE_HANDLERS[model_label] = handler


def purge_object(obj):
    if getattr(obj.__class__, "preserve_tombstones", False):
        raise TombstonePurgeBlocked(f"Permanent tombstones cannot be purged: {obj._meta.label_lower}")
    handler = _PURGE_HANDLERS.get(obj._meta.label_lower)
    if handler is not None:
        return handler(obj)
    return obj.delete(force_hard_delete=True)
