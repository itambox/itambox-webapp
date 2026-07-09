"""Pure dict helpers with no Django model dependencies.

This lives here rather than in ``itambox.utils`` so it is safe to import at
settings-load time: ``itambox.plugins.utils.load_plugins`` runs before the app
registry is ready (see ``core/settings/base.py``), while ``itambox.utils``
imports ``users.models`` at module top — importing it that early would raise
``AppRegistryNotReady``.
"""
import copy


def deep_merge(dict1, dict2):
    """Recursively merge ``dict2`` into ``dict1`` and return a new dict.

    Values from ``dict2`` take precedence; nested dicts are merged recursively.
    Every value is deep-copied so neither input is mutated or aliased into the
    result — important for plugin ``default_settings``, which must stay pristine
    across merges.
    """
    result = copy.deepcopy(dict1)
    for key, value in dict2.items():
        if isinstance(value, dict) and key in result and isinstance(result[key], dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result
