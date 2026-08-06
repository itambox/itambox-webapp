# This file is adapted from NetBox (https://github.com/netbox-community/netbox).
# Copyright (c) DigitalOcean, LLC.
# Licensed under the Apache License, Version 2.0.

from collections import defaultdict
from contextlib import contextmanager


class Registry:
    """
    In-memory registry that centralizes metadata about models and features.

    Follows NetBox's extras.registry pattern — a single source of truth
    for which models support which features, search indexes, filter sets,
    table classes, event rules, webhooks, and export templates.
    """

    def __init__(self):
        self._model_features = defaultdict(set)
        self._search_indexes = defaultdict(list)
        self._filter_sets = {}
        self._table_classes = {}
        self._event_rules = []
        self._webhooks = []
        self._export_templates = defaultdict(list)
        # Plugin registries
        self._plugin_template_contents = defaultdict(list)
        self._plugin_template_content_sources = defaultdict(list)
        self._plugin_menus = []
        self._plugin_menu_sources = []
        self._plugin_menu_items = []
        self._plugin_menu_item_sources = []
        self._plugin_viewsets = defaultdict(list)
        self._plugin_viewset_sources = defaultdict(list)
        self._registration_plugin = None

    @property
    def model_features(self):
        return dict(self._model_features)

    @property
    def search_indexes(self):
        return dict(self._search_indexes)

    @property
    def filter_sets(self):
        return self._filter_sets

    @property
    def table_classes(self):
        return self._table_classes

    @property
    def event_rules(self):
        return list(self._event_rules)

    @property
    def webhooks(self):
        return list(self._webhooks)

    @property
    def export_templates(self):
        return dict(self._export_templates)

    def register_feature(self, model, feature_name):
        """Register that a model supports a named feature (e.g., 'bookmarkable', 'taggable')."""
        self._model_features[model].add(feature_name)

    def unregister_feature(self, model, feature_name):
        self._model_features[model].discard(feature_name)

    def model_has_feature(self, model, feature_name):
        return feature_name in self._model_features.get(model, set())

    def get_models_with_feature(self, feature_name):
        return [m for m, features in self._model_features.items() if feature_name in features]

    def register_search_index(self, model, index_instance):
        self._search_indexes[model].append(index_instance)

    def register_filter_set(self, model, filter_set_class):
        self._filter_sets[model] = filter_set_class

    def get_filter_set(self, model):
        return self._filter_sets.get(model)

    def register_table_class(self, model, table_class):
        self._table_classes[model] = table_class

    def get_table_class(self, model):
        return self._table_classes.get(model)

    def register_event_rule(self, rule):
        self._event_rules.append(rule)

    def get_event_rules(self, model=None, action=None):
        rules = self._event_rules
        if model is not None:
            rules = [r for r in rules if r["model"] == model]
        if action is not None:
            rules = [r for r in rules if action in r.get("events", [])]
        return rules

    def register_webhook(self, webhook_config):
        self._webhooks.append(webhook_config)

    def get_webhooks(self):
        return list(self._webhooks)

    def register_export_template(self, model, template):
        self._export_templates[model].append(template)

    def get_export_templates(self, model):
        return self._export_templates.get(model, [])

    @contextmanager
    def plugin_registration(self, plugin_name):
        """Attribute extension registrations made during one plugin's ``ready``.

        This is an internal composition helper. It lets startup remove only the
        registrations made by a plugin whose initialization later fails; it is
        not part of the plugin API.
        """
        previous = self._registration_plugin
        self._registration_plugin = plugin_name
        try:
            yield
        finally:
            self._registration_plugin = previous

    def _current_registration_plugin(self, fallback=None):
        return self._registration_plugin or fallback

    def register_plugin_template_content(self, model, content_class):
        if not isinstance(model, str) or not model.strip():
            raise TypeError("plugin template content model must be a non-empty string")
        if not isinstance(content_class, type):
            raise TypeError("plugin template content must be a class")
        self._plugin_template_contents[model].append(content_class)
        self._plugin_template_content_sources[model].append(self._current_registration_plugin())

    def get_plugin_template_contents(self, model):
        return self._plugin_template_contents.get(model, [])

    def register_plugin_menu(self, menu_cls):
        if not isinstance(menu_cls, type):
            raise TypeError("plugin menu must be a class")
        self._plugin_menus.append(menu_cls)
        self._plugin_menu_sources.append(self._current_registration_plugin())

    def get_plugin_menus(self):
        return self._plugin_menus

    def register_plugin_menu_item(self, item_cls):
        if not isinstance(item_cls, type):
            raise TypeError("plugin menu item must be a class")
        self._plugin_menu_items.append(item_cls)
        self._plugin_menu_item_sources.append(self._current_registration_plugin())

    def get_plugin_menu_items(self):
        return self._plugin_menu_items

    def register_plugin_viewset(self, plugin_name, prefix, viewset, basename=None):
        if not isinstance(plugin_name, str) or not plugin_name.strip():
            raise TypeError("plugin viewset plugin_name must be a non-empty string")
        if not isinstance(prefix, str):
            raise TypeError("plugin viewset prefix must be a string")
        if not isinstance(viewset, str) and not isinstance(viewset, type):
            raise TypeError("plugin viewset must be a class or dotted import path")
        self._plugin_viewsets[plugin_name].append((prefix, viewset, basename))
        self._plugin_viewset_sources[plugin_name].append(self._current_registration_plugin(plugin_name))

    def get_plugin_viewsets(self):
        return self._plugin_viewsets

    def clear_plugin(self, plugin_name):
        """Remove all extension registrations attributed to ``plugin_name``.

        Called by startup isolation after a plugin fails. This deliberately
        leaves core registry entries and registrations belonging to other
        plugins untouched.
        """
        for model, entries in list(self._plugin_template_contents.items()):
            sources = self._plugin_template_content_sources[model]
            kept = [(entry, source) for entry, source in zip(entries, sources, strict=True) if source != plugin_name]
            if kept:
                self._plugin_template_contents[model] = [entry for entry, _ in kept]
                self._plugin_template_content_sources[model] = [source for _, source in kept]
            else:
                self._plugin_template_contents.pop(model, None)
                self._plugin_template_content_sources.pop(model, None)

        kept_menus = [
            (entry, source)
            for entry, source in zip(self._plugin_menus, self._plugin_menu_sources, strict=True)
            if source != plugin_name
        ]
        self._plugin_menus = [entry for entry, _ in kept_menus]
        self._plugin_menu_sources = [source for _, source in kept_menus]

        kept_items = [
            (entry, source)
            for entry, source in zip(self._plugin_menu_items, self._plugin_menu_item_sources, strict=True)
            if source != plugin_name
        ]
        self._plugin_menu_items = [entry for entry, _ in kept_items]
        self._plugin_menu_item_sources = [source for _, source in kept_items]

        for registered_name, entries in list(self._plugin_viewsets.items()):
            sources = self._plugin_viewset_sources[registered_name]
            kept = [(entry, source) for entry, source in zip(entries, sources, strict=True) if source != plugin_name]
            if kept:
                self._plugin_viewsets[registered_name] = [entry for entry, _ in kept]
                self._plugin_viewset_sources[registered_name] = [source for _, source in kept]
            else:
                self._plugin_viewsets.pop(registered_name, None)
                self._plugin_viewset_sources.pop(registered_name, None)

    def clear(self):
        """Reset all registrations. Use only in tests."""
        self._model_features.clear()
        self._search_indexes.clear()
        self._filter_sets.clear()
        self._table_classes.clear()
        self._event_rules.clear()
        self._webhooks.clear()
        self._export_templates.clear()
        self._plugin_template_contents.clear()
        self._plugin_template_content_sources.clear()
        self._plugin_menus.clear()
        self._plugin_menu_sources.clear()
        self._plugin_menu_items.clear()
        self._plugin_menu_item_sources.clear()
        self._plugin_viewsets.clear()
        self._plugin_viewset_sources.clear()
        self._registration_plugin = None


registry = Registry()
