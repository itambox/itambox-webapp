"""Registry-backed feature grading and the probe factories domains declare with.

Maturity used to be a literal ``{app_label: grade}`` map. It now derives from
:mod:`itambox.capabilities`, where each domain declares its own slices from
``AppConfig.ready``. Two things live here rather than in the substrate:

* :func:`module_maturity` and :func:`is_beta_module`, kept for one release so
  callers of the old app-level API keep working while surfaces move to
  per-model capability lookups. Both are adapters now -- they hold no data.
* The probe factories. They take dotted strings and setting names, so they stay
  domain-blind, but they know about Django's ORM and settings, which the
  framework-layer registry deliberately does not.

Every probe built here only ever reads. None of them changes what a deployment
does: a capability's declared activation is an observation of behaviour that
already exists, never a new gate in front of it.
"""

from django.apps import apps
from django.conf import settings
from django.core.exceptions import FieldDoesNotExist

from itambox.capabilities import BETA, EXPERIMENTAL, STABLE, ActivationState, registry

__all__ = [
    "BETA",
    "EXPERIMENTAL",
    "STABLE",
    "is_beta_module",
    "module_maturity",
    "object_enabled_probe",
    "settings_probe",
]

_MISSING = object()


def module_maturity(app_label):
    """Deprecated: the grade of a Django app as a whole.

    An application is graded only when a single capability owns *every* model
    in it. Anything finer -- ``extras`` holding both a Stable alert inbox and a
    Beta report designer -- grades ``stable`` here and is resolved per model by
    ``itambox.views.generic.capability_notices``. Grading such an app wholesale
    would put a Beta banner on Tags, which is exactly the imprecision the
    capability registry replaces.

    Scheduled for removal one release after #171; use
    ``registry.owner_of("<app_label>.<Model>")`` instead.
    """
    references = _model_references(app_label)
    if not references:
        return STABLE
    for capability in registry.all():
        if references <= set(capability.owns):
            return capability.maturity
    return STABLE


def is_beta_module(app_label):
    """Deprecated: True when :func:`module_maturity` grades the app non-Stable.

    Named for the boolean it replaces. Experimental counts as non-Stable, so a
    caller that only knows about Beta still warns rather than staying silent.
    """
    return module_maturity(app_label) != STABLE


def _model_references(app_label):
    try:
        app_config = apps.get_app_config(app_label)
    except LookupError:
        return frozenset()
    return frozenset(f"{app_label}.{model.__name__}" for model in app_config.get_models())


def object_enabled_probe(app_label, model_name, flag_field="enabled"):
    """Observe whether a deployment has any live, enabled row of a model.

    ``value_present`` reports that the operator created rows at all;
    ``active`` reports that at least one of them is switched on.

    Two things narrow the count, both so the answer describes the *deployment*
    rather than a request. Reads run through ``_base_manager``, because the
    tenant-scoped default manager returns nothing outside a tenant context and
    would make a configured deployment look unconfigured to a management
    command. That manager is also soft-delete-blind, so rows are then restricted
    to the undeleted ones: an event rule in the recycle bin is not something a
    deployment has switched on, and counting it would keep a capability
    reporting active after its last real row was deleted. Models with no
    ``deleted_at`` field are counted whole.

    An unreachable database surfaces as a fail-closed probe error rather than a
    broken page.
    """

    def probe():
        model = apps.get_model(app_label, model_name)
        rows = model._base_manager.all()
        if _has_field(model, "deleted_at"):
            rows = rows.filter(deleted_at__isnull=True)
        return ActivationState(
            active=rows.filter(**{flag_field: True}).exists(),
            value_present=rows.exists(),
        )

    return probe


def _has_field(model, field_name):
    try:
        model._meta.get_field(field_name)
    except FieldDoesNotExist:
        return False
    return True


def settings_probe(setting_name, default=False):
    """Observe a configuration or operator-flag setting without reading it out.

    ``value_present`` is true only when the setting exists *and* carries
    something; a declared-but-empty setting reads the same as an absent one,
    which is what an operator means by "not configured". ``default`` is the
    behaviour that applies when nobody set it, so a probe over a setting the
    code already reads with a fallback reports the fallback honestly.

    The value itself never leaves this closure: only the two booleans do.
    """

    def probe():
        value = getattr(settings, setting_name, _MISSING)
        if value is _MISSING:
            return ActivationState(active=bool(default), value_present=False)
        return ActivationState(active=bool(value), value_present=bool(value))

    return probe
