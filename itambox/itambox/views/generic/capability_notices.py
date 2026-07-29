"""Registry-backed maturity markers for the generic presentation layer.

A list or detail page asks one question -- "is the thing on this page still
settling?" -- and the honest answer is a property of the capability that owns
the model, not of the Django app it happens to live in. ``extras`` holds a
Stable alert inbox next to a Beta report designer; grading either by app label
mislabels the other.

The notice is derived from the *declared contract*, never from activation
state. A Beta capability that a deployment has switched off is still Beta, and
a probe that cannot reach the database must not silently turn a banner off, so
nothing here consults :meth:`~itambox.capabilities.CapabilityRegistry.state`.
"""

from core.features import STABLE, module_maturity
from itambox.capabilities import registry

#: Only these keys reach a template. The probe, the owning area, and the owned
#: references are deliberately absent: a page renders a grade, not an inventory.
NOTICE_FIELDS = ("key", "title", "maturity", "activation", "docs_url", "limitations")


def capability_notice(model):
    """The maturity notice for ``model``, or ``None`` when nothing to say.

    Resolution is per model first, falling back to the deprecated app-level
    grade so a model no capability owns yet keeps whatever banner it has today.
    """
    if model is None:
        return None
    meta = getattr(model, "_meta", None)
    if meta is None:
        return None
    capability = registry.owner_of(f"{meta.app_label}.{model.__name__}")
    if capability is None:
        return _legacy_notice(meta.app_label)
    if capability.maturity == STABLE:
        return None
    return {
        "key": capability.key,
        "title": capability.title,
        "maturity": capability.maturity,
        "activation": capability.activation,
        "docs_url": capability.docs_url,
        "limitations": capability.limitations,
    }


def _legacy_notice(app_label):
    """The app-level answer, for models the registry does not own yet."""
    maturity = module_maturity(app_label)
    if maturity == STABLE:
        return None
    return {
        "key": app_label,
        "title": app_label.replace("_", " ").title(),
        "maturity": maturity,
        "activation": "",
        "docs_url": "development/capability-registry.md",
        "limitations": (),
    }
