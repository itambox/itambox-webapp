"""U6: the shared deactivation harness for capability tests.

Every capability whose activation can go false needs the same proof: with the
capability inactive, nothing that renders, reports, or serialises raises, and
nothing leaks a probe-supplied value. Writing that per capability guarantees
drift, so it is written once here and parametrised over the registry.

The harness swaps the *probe* of an already-registered entry rather than the
entry's declared shape, so maturity, ownership, and the security-critical flag
stay exactly as the domain declared them. It reaches into the registry's
private index on purpose: production code carries no mutation seam, and a test
helper is the right place to pay for that.
"""

import contextlib
import dataclasses

from itambox.capabilities import ALWAYS_ON, STABLE, ActivationState, registry


def deactivatable_keys(source=None):
    """Keys whose activation can legitimately be observed false.

    Always-on entries are excluded by construction: a Stable capability has no
    probe, so there is nothing to force and no inactive state to prove safe.
    """
    source = registry if source is None else source
    return tuple(
        capability.key
        for capability in source.all()
        if capability.maturity != STABLE and capability.activation != ALWAYS_ON
    )


@contextlib.contextmanager
def _swapped_probe(key, probe, source=None):
    source = registry if source is None else source
    original = source.get(key)
    replacement = dataclasses.replace(original, activation_probe=probe)
    source._by_key[key] = replacement
    try:
        yield replacement
    finally:
        source._by_key[key] = original


def deactivated(key, source=None):
    """Force ``key`` to report inactive with nothing configured."""
    return _swapped_probe(key, lambda: ActivationState(active=False, value_present=False), source=source)


def activated(key, source=None):
    """Force ``key`` to report active from existing configuration."""
    return _swapped_probe(key, lambda: ActivationState(active=True, value_present=True), source=source)


@contextlib.contextmanager
def half_registered(app_label, source=None):
    """Stand in for a declaration that failed partway through.

    Drops everything an application declares except its first entry, which is
    the state a mid-declaration failure leaves behind, and puts the registry
    back exactly as it was afterwards. Yields the dropped keys.
    """
    # inline import: app-registry: the Django app registry is not populated when
    # this test helper module is imported.
    from django.apps import apps as app_registry

    source = registry if source is None else source
    declared = [capability.key for capability in app_registry.get_app_config(app_label)._capabilities()]
    dropped = {key: source.get(key) for key in declared[1:]}
    for key, capability in dropped.items():
        del source._by_key[key]
        for reference in capability.owns:
            source._owners.pop(reference, None)
    try:
        yield tuple(dropped)
    finally:
        for key, capability in dropped.items():
            source._by_key.pop(key, None)
            for reference in capability.owns:
                source._owners.pop(reference, None)
            source.register(capability)


def probe_failing(key, exception=None, source=None):
    """Force ``key``'s probe to raise, standing in for an unreachable database."""
    error = RuntimeError("probe unavailable: token=hunter2") if exception is None else exception

    def explode():
        raise error

    return _swapped_probe(key, explode, source=source)
