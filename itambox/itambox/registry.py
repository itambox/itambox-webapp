# This file is adapted from NetBox (https://github.com/netbox-community/netbox).
# Copyright (c) DigitalOcean, LLC.
# Licensed under the Apache License, Version 2.0.

from collections import defaultdict


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
        self._counter_fields = {}
        self._denormalized_fields = defaultdict(list)
        self._lazy_handlers = []
        # Plugin registries
        self._plugin_template_contents = defaultdict(list)
        self._plugin_menus = []
        self._plugin_menu_items = []
        self._plugin_viewsets = defaultdict(list)

    def defer_init(self, callback):
        self._lazy_handlers.append(callback)

    def execute_deferred(self):
        for handler in self._lazy_handlers:
            handler()
        self._lazy_handlers.clear()

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
            rules = [r for r in rules if r['model'] == model]
        if action is not None:
            rules = [r for r in rules if action in r.get('events', [])]
        return rules

    def register_webhook(self, webhook_config):
        self._webhooks.append(webhook_config)

    def get_webhooks(self):
        return list(self._webhooks)

    def register_export_template(self, model, template):
        self._export_templates[model].append(template)

    def get_export_templates(self, model):
        return self._export_templates.get(model, [])

    def register_counter_field(self, model, field_name, source_model, source_field=None):
        self._counter_fields[model] = {
            'field_name': field_name,
            'source_model': source_model,
            'source_field': source_field,
        }

    def register_denormalized_field(self, model, field_name, source_path):
        self._denormalized_fields[model].append({
            'field_name': field_name,
            'source_path': source_path,
        })

    def register_plugin_template_content(self, model, content_class):
        self._plugin_template_contents[model].append(content_class)

    def get_plugin_template_contents(self, model):
        return self._plugin_template_contents.get(model, [])

    def register_plugin_menu(self, menu_cls):
        self._plugin_menus.append(menu_cls)

    def get_plugin_menus(self):
        return self._plugin_menus

    def register_plugin_menu_item(self, item_cls):
        self._plugin_menu_items.append(item_cls)

    def get_plugin_menu_items(self):
        return self._plugin_menu_items

    def register_plugin_viewset(self, plugin_name, prefix, viewset, basename=None):
        self._plugin_viewsets[plugin_name].append((prefix, viewset, basename))

    def get_plugin_viewsets(self):
        return self._plugin_viewsets

    def clear(self):
        """Reset all registrations. Use only in tests."""
        self._model_features.clear()
        self._search_indexes.clear()
        self._filter_sets.clear()
        self._table_classes.clear()
        self._event_rules.clear()
        self._webhooks.clear()
        self._export_templates.clear()
        self._counter_fields.clear()
        self._denormalized_fields.clear()
        self._plugin_template_contents.clear()
        self._plugin_menus.clear()
        self._plugin_menu_items.clear()
        self._plugin_viewsets.clear()


registry = Registry()
