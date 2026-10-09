"""App-registered archive/restore handlers for the model-agnostic callers.

Soft delete is not always a single-row flag flip. An *aggregate root* (an asset
holder, an asset, a kit) owns child rows whose lifecycle has to move with it:
some must refuse the archive while they are still active, others must be
detached so that no live row keeps pointing at an archived parent, and the rows
touched need their own audit entries. Those decisions belong to the aggregate's
own domain service, never to a generic view.

The single delete view, the bulk delete view, the REST API delete path and the
recycle-bin restore views all resolve their model from the URL and therefore
cannot import a domain service. An app registers its aggregate service here
instead, exactly like ``core.purge_handlers`` does for hard purges. Every model
WITHOUT a registration keeps the plain leaf semantics (``obj.delete()`` /
``obj.restore()``), so a registration is opt-in and changes nothing else.

A registered handler is called as ``handler(obj, actor=..., request=...)`` and
returns whatever its own result type is; the generic callers only distinguish
success from a refused operation.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.core.exceptions import ValidationError

#: ``handler(obj, *, actor, request)`` — ``actor``/``request`` are ``None`` for a
#: programmatic caller (a management command, a background task).
ArchiveHandler = Callable[..., Any]
RestoreHandler = Callable[..., Any]

_ARCHIVE_HANDLERS: dict[str, ArchiveHandler] = {}
_RESTORE_HANDLERS: dict[str, RestoreHandler] = {}


class AggregateArchiveBlocked(ValidationError):
    """A typed, user-facing refusal from an aggregate archive/restore service.

    Subclasses Django's ``ValidationError`` on purpose: the REST API maps that to
    HTTP 400 with the message list through
    ``itambox.api.exceptions.itambox_exception_handler`` (never a 500), and a view
    can render ``messages`` without parsing strings. It is also the base every
    domain refusal inherits, so the model-agnostic callers catch one class
    without importing a domain service.
    """

    @property
    def user_message(self) -> str:
        """The one message to show a human.

        ``str()`` on a Django ``ValidationError`` renders the repr of the message
        list; a view that relays a refusal must use this instead.
        """
        messages = self.messages
        return str(messages[0]) if messages else ""


def register_archive_handler(
    model_label: str,
    *,
    archive: ArchiveHandler,
    restore: RestoreHandler | None = None,
) -> None:
    """Register the aggregate service of ``model_label`` (``app.Model``).

    Re-registering the same callable is a no-op (app ``ready()`` may run more than
    once in a process); a different callable for the same label is a wiring bug
    and fails loudly rather than silently changing which service runs.
    """
    registered = _ARCHIVE_HANDLERS.get(model_label)
    if registered is not None and registered is not archive:
        raise RuntimeError(f"An archive handler is already registered for {model_label}.")
    _ARCHIVE_HANDLERS[model_label] = archive

    if restore is None:
        return
    registered_restore = _RESTORE_HANDLERS.get(model_label)
    if registered_restore is not None and registered_restore is not restore:
        raise RuntimeError(f"A restore handler is already registered for {model_label}.")
    _RESTORE_HANDLERS[model_label] = restore


def archive_object(obj, *, actor=None, request=None):
    """Archive ``obj`` through its aggregate service, else soft-delete it."""
    handler = _ARCHIVE_HANDLERS.get(obj._meta.label_lower)
    if handler is None:
        return obj.delete()
    return handler(obj, actor=actor, request=request)


def restore_object(obj, *, actor=None, request=None):
    """Restore ``obj`` through its aggregate service, else flip the leaf flag."""
    handler = _RESTORE_HANDLERS.get(obj._meta.label_lower)
    if handler is None:
        return obj.restore()
    return handler(obj, actor=actor, request=request)
