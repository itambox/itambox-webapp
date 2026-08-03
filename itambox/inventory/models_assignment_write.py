"""Ephemeral capability for creating inventory assignments through sanctioned services.

Model-layer leaf (hence the ``models_`` prefix, as with ``forms_audit`` /
``views_scan``): ``AbstractAssignment.clean()`` consults it to enforce its own
write invariant, so it must sit at or below the model layer and import nothing.
"""

import contextlib
import contextvars

_assignment_write = contextvars.ContextVar("inventory_assignment_write", default=None)
_assignment_hard_purge = contextvars.ContextVar("inventory_assignment_hard_purge", default=None)
_VALIDATION_ONLY = object()


def _fingerprint(assignment):
    return (
        assignment._meta.label_lower,
        getattr(assignment, f"{assignment._item_attr}_id", None),
        assignment.from_location_id,
        assignment.assigned_holder_id,
        assignment.assigned_location_id,
        assignment.assigned_asset_id,
        assignment.resource_grant_id,
        assignment.qty,
    )


@contextlib.contextmanager
def authorized_assignment_write(assignment):
    """Permit one exact assignment instance shape during its service save."""
    permit = (id(assignment), _fingerprint(assignment))
    token = _assignment_write.set(permit)
    try:
        yield
    finally:
        _assignment_write.reset(token)


def assignment_write_is_permitted(assignment):
    """True only under an exact write permit for *this* instance shape.

    The validation-only permit deliberately does not satisfy this: it exists so
    a form can exercise model invariants, and it must never authorize the stock
    mutation a real create performs.
    """
    return _assignment_write.get() == (id(assignment), _fingerprint(assignment))


def assignment_write_is_authorized(assignment):
    authorization = _assignment_write.get()
    return authorization is _VALIDATION_ONLY or assignment_write_is_permitted(assignment)


@contextlib.contextmanager
def authorized_assignment_hard_purge(assignment):
    """Permit physical removal of one already soft-deleted assignment row."""

    permit = (id(assignment), assignment._meta.label_lower, assignment.pk, assignment.deleted_at)
    token = _assignment_hard_purge.set(permit)
    try:
        yield
    finally:
        _assignment_hard_purge.reset(token)


def assignment_hard_purge_is_permitted(assignment):
    return _assignment_hard_purge.get() == (
        id(assignment),
        assignment._meta.label_lower,
        assignment.pk,
        assignment.deleted_at,
    )


@contextlib.contextmanager
def authorized_assignment_validation():
    """Permit model invariant validation, but not any later save call."""
    token = _assignment_write.set(_VALIDATION_ONLY)
    try:
        yield
    finally:
        _assignment_write.reset(token)
