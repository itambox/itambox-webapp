"""Unit tests for the architecture boundary policy.

The gate answers one question -- may this module import that one -- and it has
to answer it identically on Windows and Linux, without importing a single line
of application code. Everything below is written against synthetic trees for
that reason; the handful of assertions that touch the real repository name a
module rather than a count, because a count tests the repository and this suite
tests the gate.
"""

import ast
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from scripts.architecture_policy import (
    AREA_LABELS,
    CANONICAL_PYTHON,
    DEFAULT_TARGETS,
    LAYERS,
    MODULE_LAYER_OVERRIDES,
    PACKAGE_INIT,
    RULE_REGISTRY,
    SOURCE_ROOT,
    PolicyError,
    app_of,
    build_graph,
    classify,
    compute_policy_fingerprint,
    discover_modules,
    is_allowed,
    layer_of,
    origin_of,
    owner_for_modules,
    resolve_import,
    source_module_for_path,
    strongly_connected_components,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def write(root, relative, body=""):
    """Create one source file under ``root``; mirrors ``test_local_imports``."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return path


class TreeTestCase(unittest.TestCase):
    """Base class that builds a throw-away source tree under ``itambox/``."""

    def build(self, files):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        for relative, body in files.items():
            write(root, relative, body)
        return root

    def graph(self, files):
        return build_graph(self.build(files), DEFAULT_TARGETS)


class DiscoveryTests(TreeTestCase):
    """Discovery is a path walk. It never imports and never probes for packages."""

    def test_module_names_anchor_at_the_source_root(self):
        root = self.build(
            {
                "itambox/core/models.py": "",
                "itambox/itambox/api/base.py": "",
                "itambox/assets/models/__init__.py": "",
            }
        )

        discovered = discover_modules(root, DEFAULT_TARGETS)

        self.assertEqual(
            sorted(discovered),
            ["assets.models.__init__", "core.models", "itambox.api.base"],
        )

    def test_package_initialisers_are_their_own_node(self):
        """Model A: ``pkg`` and ``pkg.__init__`` are never the same node."""
        root = self.build({"itambox/assets/forms/__init__.py": "", "itambox/assets/forms/asset_form.py": ""})

        discovered = discover_modules(root, DEFAULT_TARGETS)

        self.assertIn("assets.forms.__init__", discovered)
        self.assertNotIn("assets.forms", discovered)

    def test_namespace_packages_are_discovered(self):
        """``core/views/`` and ``users/api/`` carry no ``__init__.py``."""
        root = self.build(
            {
                "itambox/core/views/graphql.py": "",
                "itambox/users/api/scim/__init__.py": "",
                "itambox/users/api/scim/views.py": "",
            }
        )

        discovered = discover_modules(root, DEFAULT_TARGETS)

        self.assertIn("core.views.graphql", discovered)
        self.assertIn("users.api.scim.views", discovered)

    def test_generated_vendored_and_test_trees_are_invisible(self):
        root = self.build(
            {
                "itambox/assets/models.py": "",
                "itambox/assets/migrations/0001_initial.py": "",
                "itambox/assets/tests/test_models.py": "",
                "itambox/assets/tests.py": "",
                "itambox/assets/test_helpers.py": "",
                "itambox/conftest.py": "",
                "itambox/manage.py": "",
                "itambox/static/src/thing.py": "",
                "itambox/docs/example.py": "",
                "itambox/templates/example.py": "",
                "itambox/locale/example.py": "",
            }
        )

        self.assertEqual(sorted(discover_modules(root, DEFAULT_TARGETS)), ["assets.models"])

    def test_discovery_paths_are_posix_relative_to_the_repository_root(self):
        root = self.build({"itambox/core/models.py": ""})

        self.assertEqual(discover_modules(root, DEFAULT_TARGETS)["core.models"], "itambox/core/models.py")

    def test_a_target_outside_the_source_root_is_a_policy_error(self):
        root = self.build({"itambox/core/models.py": "", "scripts/thing.py": ""})

        with self.assertRaises(PolicyError):
            discover_modules(root, ("scripts",))

    def test_a_declared_target_that_does_not_exist_is_a_policy_error(self):
        """A mistyped target must not read as a target with nothing in it."""
        root = self.build({"itambox/core/models.py": ""})

        with self.assertRaises(PolicyError):
            discover_modules(root, ("itambox/nowhere",))

    def test_a_scan_that_finds_no_module_is_a_policy_error(self):
        """Zero modules is never a clean graph; it is a tree nobody scanned."""
        root = self.build({"itambox/README.md": "not python\n"})

        with self.assertRaises(PolicyError):
            discover_modules(root, DEFAULT_TARGETS)

    def test_code_outside_the_targets_is_never_discovered(self):
        """The gate scans first-party code only; a plugin tree is not in it."""
        root = self.build({"itambox/core/models.py": "", "plugin_package/models.py": ""})

        discovered = discover_modules(root, DEFAULT_TARGETS)

        self.assertEqual(sorted(discovered), ["core.models"])

    def test_an_unparseable_source_file_fails_closed(self):
        root = self.build({"itambox/core/models.py": "def broken(:\n"})

        with self.assertRaises(PolicyError):
            build_graph(root, DEFAULT_TARGETS)


class ClassificationTests(unittest.TestCase):
    """``layer_of`` is total over graph-relevant modules and never defaults."""

    def test_override_wins_over_every_rule(self):
        module = sorted(MODULE_LAYER_OVERRIDES)[0]

        self.assertEqual(layer_of(module), MODULE_LAYER_OVERRIDES[module])

    def test_composition_leaves_win_on_the_last_segment(self):
        for module in (
            "users.api.urls",
            "extras.dashboard.urls",
            "assets.urls_audits",
            "users.api.scim.provider_urls",
            "assets.apps",
            "core.admin",
            "core.wsgi",
            "core.asgi",
            "itambox.plugins.urls",
        ):
            with self.subTest(module=module):
                self.assertEqual(layer_of(module), "composition")

    def test_inventory_model_and_table_modules_have_native_layers(self):
        self.assertEqual(layer_of("inventory.models_mixins"), "domain-model")
        self.assertEqual(layer_of("inventory.models_stock"), "domain-model")
        self.assertEqual(layer_of("inventory.models_kit_checkout"), "domain-model")
        self.assertEqual(layer_of("inventory.tables"), "presentation")
        self.assertIsNone(is_allowed("inventory.abstract_models", "inventory.models_mixins").rule)
        self.assertIsNone(is_allowed("inventory.tables", "core.tables.base").rule)

    def test_suffix_named_siblings_classify_like_the_bare_name(self):
        self.assertEqual(layer_of("assets.views_scan"), "presentation")
        self.assertEqual(layer_of("compliance.forms_audit"), "presentation")
        self.assertEqual(layer_of("inventory.abstract_models"), "domain-model")

    def test_nested_segments_are_walked_left_to_right(self):
        self.assertEqual(layer_of("extras.dashboard.views"), "presentation")
        self.assertEqual(layer_of("assets.models.asset"), "domain-model")
        self.assertEqual(layer_of("assets.api.serializers"), "presentation")
        self.assertEqual(layer_of("organization.services.membership"), "domain-service")

    def test_platform_prefixes_match_on_dotted_boundaries(self):
        self.assertEqual(layer_of("core.models"), "kernel")
        self.assertEqual(layer_of("core.tasks.context"), "platform-service")
        self.assertEqual(layer_of("core.auth.saml"), "integration")
        self.assertEqual(layer_of("itambox.api.viewsets"), "framework")
        self.assertEqual(layer_of("itambox.plugins.models"), "framework")
        self.assertEqual(layer_of("itambox.views.generic.detail"), "presentation")
        self.assertEqual(layer_of("core.settings.prod"), "composition")

    def test_package_initialisers_inherit_their_parent_package(self):
        self.assertEqual(layer_of("assets.forms.__init__"), "presentation")
        self.assertEqual(layer_of("assets.models.__init__"), "domain-model")
        self.assertEqual(layer_of("itambox.views.generic.__init__"), "presentation")
        self.assertEqual(layer_of("core.settings.__init__"), "composition")

    def test_origin_separates_the_platform_from_the_domain(self):
        self.assertEqual(origin_of("itambox.views.generic.list_"), "platform")
        self.assertEqual(origin_of("core.tables.base"), "platform")
        self.assertEqual(origin_of("assets.views.asset_views"), "domain")
        self.assertEqual(app_of("assets.models.asset"), "assets")
        self.assertEqual(app_of("core.models"), "core")

    def test_an_unclassifiable_module_raises_rather_than_defaulting(self):
        with self.assertRaises(PolicyError):
            layer_of("assets.newthing")
        with self.assertRaises(PolicyError):
            origin_of("thirdparty.thing")

    def test_classify_returns_module_layer_origin_and_app(self):
        classification = classify("assets.models.asset")

        self.assertEqual(classification.module, "assets.models.asset")
        self.assertEqual(classification.layer, "domain-model")
        self.assertEqual(classification.origin, "domain")
        self.assertEqual(classification.app, "assets")

    def test_the_package_init_sentinel_is_not_a_layer(self):
        self.assertNotIn(PACKAGE_INIT, LAYERS)


class ResolutionTests(TreeTestCase):
    """Resolution answers only from the in-memory module set, exact-case."""

    def resolve(self, statement, source_module, files):
        root = self.build(files)
        modules = discover_modules(root, DEFAULT_TARGETS)
        node = ast.parse(textwrap.dedent(statement)).body[0]
        return resolve_import(node, source_module, modules)

    def test_from_package_import_submodule_prefers_the_submodule(self):
        files = {
            "itambox/assets/models/__init__.py": "from .asset import Asset\n",
            "itambox/assets/models/asset.py": "",
            "itambox/assets/views/list_.py": "",
        }

        self.assertEqual(
            self.resolve("from assets.models import Asset, asset", "assets.views.list_", files),
            ("assets.models.__init__", "assets.models.asset"),
        )

    def test_a_class_name_never_becomes_a_module_node(self):
        files = {"itambox/core/models.py": "", "itambox/assets/services.py": ""}

        self.assertEqual(
            self.resolve("from core.models import BaseModel", "assets.services", files),
            ("core.models",),
        )

    def test_dotted_imports_resolve_to_the_longest_discovered_prefix(self):
        files = {"itambox/assets/models/asset.py": "", "itambox/assets/services.py": ""}

        self.assertEqual(
            self.resolve("import assets.models.asset", "assets.services", files),
            ("assets.models.asset",),
        )

    def test_aliases_and_stars_do_not_change_the_target(self):
        files = {
            "itambox/assets/forms/__init__.py": "from .bulk_forms import *\n",
            "itambox/assets/forms/bulk_forms.py": "",
            "itambox/core/models.py": "",
            "itambox/assets/services.py": "",
        }

        self.assertEqual(
            self.resolve("import core.models as m", "assets.services", files),
            ("core.models",),
        )
        self.assertEqual(
            self.resolve("from .bulk_forms import *", "assets.forms.__init__", files),
            ("assets.forms.bulk_forms",),
        )

    def test_relative_imports_are_level_and_package_aware(self):
        files = {
            "itambox/assets/models/__init__.py": "from .asset import Asset\n",
            "itambox/assets/models/asset.py": "",
            "itambox/assets/forms/__init__.py": "from .bulk_forms import *\n",
            "itambox/assets/forms/bulk_forms.py": "",
            "itambox/assets/views/list_.py": "",
            "itambox/core/models.py": "",
        }

        self.assertEqual(
            self.resolve("from . import bulk_forms", "assets.forms.__init__", files),
            ("assets.forms.bulk_forms",),
        )
        self.assertEqual(
            self.resolve("from ..models import Asset", "assets.views.list_", files),
            ("assets.models.__init__",),
        )

    def test_a_relative_import_beyond_the_top_level_package_is_a_policy_error(self):
        files = {"itambox/assets/views/list_.py": ""}

        with self.assertRaises(PolicyError):
            self.resolve("from .... import x", "assets.views.list_", files)

    def test_third_party_and_unknown_first_party_targets_yield_no_edge(self):
        files = {"itambox/core/http.py": "", "itambox/assets/services.py": ""}

        for statement in (
            "from django.db import models",
            "import saml2",
            "import django_tables2 as tables",
            "from __future__ import annotations",
            "from unknown_thing.here import x",
        ):
            with self.subTest(statement=statement):
                self.assertEqual(self.resolve(statement, "assets.services", files), ())

    def test_the_resolver_is_absolute_first_and_never_shadows_the_standard_library(self):
        """``core.http`` exists; ``import http`` inside ``core`` is still stdlib."""
        files = {"itambox/core/http.py": "", "itambox/core/models.py": ""}

        self.assertEqual(self.resolve("import http", "core.models", files), ())

    def test_module_existence_is_exact_case_membership_only(self):
        """A case-insensitive filesystem must not change the resolved edge."""
        files = {
            "itambox/assets/models/__init__.py": "from .asset import Asset\n",
            "itambox/assets/models/asset.py": "",
            "itambox/assets/services.py": "",
        }

        self.assertEqual(
            self.resolve("from assets.models import Asset", "assets.services", files),
            ("assets.models.__init__",),
        )


class ScopeTests(TreeTestCase):
    """An import's kind is decided by its enclosing scope, then by typing."""

    def kinds(self, body):
        graph = self.graph({"itambox/core/target.py": "", "itambox/core/sample.py": body})
        return {
            (edge.source, edge.target, edge.kind, edge.scope) for edge in graph.evidence if edge.source == "core.sample"
        }

    def test_module_top_covers_if_try_with_and_class_bodies(self):
        kinds = self.kinds(
            """
            from core.target import a

            if True:
                from core.target import b

            try:
                from core.target import c
            except ImportError:
                c = None

            with open("x") as handle:
                from core.target import d


            class Thing:
                from core.target import e
            """
        )

        self.assertEqual({kind for _, _, kind, _ in kinds}, {"module-top"})

    def test_function_bodies_defer_at_any_nesting_depth(self):
        kinds = self.kinds(
            """
            def build():
                try:
                    from core.target import a
                except ImportError:
                    a = None


            async def handler():
                from core.target import b


            def outer():
                class Inner:
                    def method(self):
                        from core.target import c
            """
        )

        self.assertEqual({kind for _, _, kind, _ in kinds}, {"function-body"})
        self.assertIn(
            ("core.sample", "core.target", "function-body", "FunctionDef:outer/ClassDef:Inner/FunctionDef:method"),
            kinds,
        )

    def test_type_checking_blocks_are_neither_module_top_nor_function_body(self):
        graph = self.graph(
            {
                "itambox/core/target.py": "",
                "itambox/core/sample.py": """
                from typing import TYPE_CHECKING

                if TYPE_CHECKING:
                    from core.target import a
                """,
            }
        )

        self.assertEqual(graph.module_top, ())
        self.assertEqual(graph.function_body, ())
        self.assertEqual([(edge.source, edge.target) for edge in graph.typing_only], [("core.sample", "core.target")])

    def test_the_dotted_typing_spelling_is_recognised(self):
        graph = self.graph(
            {
                "itambox/core/target.py": "",
                "itambox/core/sample.py": """
                import typing

                if typing.TYPE_CHECKING:
                    from core.target import a
                """,
            }
        )

        self.assertEqual(graph.module_top, ())
        self.assertEqual([(edge.source, edge.target) for edge in graph.typing_only], [("core.sample", "core.target")])

    def test_an_unrecognised_guard_is_not_treated_as_typing_only(self):
        graph = self.graph(
            {
                "itambox/core/target.py": "",
                "itambox/core/sample.py": """
                from typing import TYPE_CHECKING

                if not TYPE_CHECKING:
                    from core.target import a
                """,
            }
        )

        self.assertEqual([(edge.source, edge.target) for edge in graph.module_top], [("core.sample", "core.target")])

    def test_only_typing_owns_the_dotted_spelling(self):
        """An attribute named ``TYPE_CHECKING`` on anything else is not the guard.

        Matching the attribute name alone would let ``if shim.TYPE_CHECKING:``
        delete an edge from both blocking graphs, including one the matrix
        forbids absolutely. The exclusion is the one place a widened match is
        silent, so it is spelled out rather than pattern-matched loosely.
        """
        for guard in ("compat.TYPE_CHECKING", "settings.TYPE_CHECKING", "compat.typing.TYPE_CHECKING"):
            with self.subTest(guard=guard):
                graph = self.graph(
                    {
                        "itambox/core/target.py": "",
                        "itambox/core/sample.py": f"""
                        import compat

                        if {guard}:
                            from core.target import a
                        """,
                    }
                )

                self.assertEqual(
                    [(edge.source, edge.target) for edge in graph.module_top],
                    [("core.sample", "core.target")],
                )
                self.assertEqual(graph.typing_only, ())

    def test_the_else_branch_of_a_type_checking_guard_still_executes(self):
        graph = self.graph(
            {
                "itambox/core/target.py": "",
                "itambox/core/other.py": "",
                "itambox/core/sample.py": """
                from typing import TYPE_CHECKING

                if TYPE_CHECKING:
                    from core.target import a
                else:
                    from core.other import b
                """,
            }
        )

        self.assertEqual([(edge.source, edge.target) for edge in graph.module_top], [("core.sample", "core.other")])

    def test_the_class_body_boundary_agrees_with_the_local_import_gate(self):
        """``check_local_imports`` requires a function in scope; so does this."""
        from scripts.check_local_imports import _ImportCollector

        source = textwrap.dedent(
            """
            class Thing:
                from core.target import e

                def method(self):
                    from core.target import f
            """
        ).lstrip()
        collector = _ImportCollector()
        collector.visit(ast.parse(source))
        deferred = {node.lineno for node, _ in collector.imports}

        graph = self.graph({"itambox/core/target.py": "", "itambox/core/sample.py": source})
        gate_deferred = {edge.line for edge in graph.evidence if edge.kind == "function-body"}

        self.assertEqual(deferred, gate_deferred)


class PrefixEdgeTests(TreeTestCase):
    """An import-free initialiser creates no coupling, so it creates no edge."""

    def test_an_empty_initialiser_emits_no_prefix_edge(self):
        graph = self.graph(
            {
                "itambox/assets/__init__.py": "",
                "itambox/assets/models/__init__.py": "",
                "itambox/assets/models/asset.py": "",
                "itambox/assets/services.py": "from assets.models.asset import Asset\n",
            }
        )

        self.assertEqual(
            [(edge.source, edge.target) for edge in graph.module_top],
            [("assets.services", "assets.models.asset")],
        )

    def test_a_re_exporting_initialiser_is_a_real_edge(self):
        graph = self.graph(
            {
                "itambox/assets/__init__.py": "",
                "itambox/assets/models/__init__.py": "from .asset import Asset\n",
                "itambox/assets/models/asset.py": "",
                "itambox/assets/services.py": "from assets.models.asset import Asset\n",
            }
        )

        self.assertEqual(
            sorted((edge.source, edge.target) for edge in graph.module_top),
            [
                ("assets.models.__init__", "assets.models.asset"),
                ("assets.services", "assets.models.__init__"),
                ("assets.services", "assets.models.asset"),
            ],
        )

    def test_an_import_free_initialiser_is_the_inert_sentinel(self):
        graph = self.graph({"itambox/assets/__init__.py": "", "itambox/assets/services.py": ""})

        self.assertEqual(graph.census["inert"], ("assets.__init__",))
        self.assertEqual(layer_of("assets.services"), "domain-service")


class ComponentTests(unittest.TestCase):
    """Tarjan, iteratively, with every emitted collection sorted."""

    def test_acyclic_graphs_have_no_components(self):
        self.assertEqual(strongly_connected_components({"a": ("b",), "b": ("c",), "c": ()}), ())

    def test_two_and_three_module_cycles_are_reported_sorted(self):
        self.assertEqual(strongly_connected_components({"b": ("a",), "a": ("b",)}), (("a", "b"),))
        self.assertEqual(
            strongly_connected_components({"a": ("b",), "b": ("c",), "c": ("a",)}),
            (("a", "b", "c"),),
        )

    def test_a_chord_produces_one_component_not_two_simple_cycles(self):
        adjacency = {"a": ("b",), "b": ("c", "a"), "c": ("a",)}

        self.assertEqual(strongly_connected_components(adjacency), (("a", "b", "c"),))

    def test_disjoint_components_are_ordered_by_identity(self):
        adjacency = {"y": ("z",), "z": ("y",), "a": ("b",), "b": ("a",)}

        self.assertEqual(strongly_connected_components(adjacency), (("a", "b"), ("y", "z")))

    def test_a_self_import_is_not_a_component(self):
        self.assertEqual(strongly_connected_components({"a": ("a",)}), ())

    def test_ordering_is_stable_under_insertion_order(self):
        forward = {"a": ("b",), "b": ("c",), "c": ("a",), "d": ("e",), "e": ("d",)}
        reverse = dict(reversed(list(forward.items())))

        self.assertEqual(strongly_connected_components(forward), strongly_connected_components(reverse))

    def test_a_long_chain_does_not_exhaust_the_recursion_limit(self):
        adjacency = {f"m{index}": (f"m{index + 1}",) for index in range(2000)}
        adjacency["m2000"] = ("m1999",)

        components = strongly_connected_components(adjacency)

        self.assertEqual(components, (("m1999", "m2000"),))


class PolicyShapeTests(unittest.TestCase):
    """The registry is closed, the owner table is total, the fingerprint binds."""

    def test_every_forbidden_cell_has_exactly_one_rule(self):
        from scripts.architecture_policy import MATRIX

        cells = {(row, target) for row, targets in MATRIX.items() for target, verdict in targets.items() if verdict}

        self.assertEqual(cells, set(RULE_REGISTRY))

    def test_owner_labels_are_repository_area_labels(self):
        from scripts.architecture_policy import OWNER_BY_MODULE_PREFIX

        self.assertTrue(AREA_LABELS)
        self.assertTrue(all(label.startswith("area:") for label in AREA_LABELS))
        self.assertLessEqual(set(OWNER_BY_MODULE_PREFIX.values()), AREA_LABELS)

    def test_owner_resolution_is_source_first_then_target(self):
        self.assertEqual(owner_for_modules(("itambox.api.nested_serializers", "assets.models")), "area:api")
        self.assertEqual(owner_for_modules(("core.auth.saml", "organization.models")), "area:auth-rbac")
        self.assertEqual(owner_for_modules(("software.models", "licenses.reconciliation")), "area:licenses")

    def test_a_cycle_claim_is_owned_by_its_source_not_by_what_it_names(self):
        """D3 is source-first: the module whose import must move owns the work.

        Every case below names ``organization`` modules as targets, so a
        target-first or basename-first derivation attributes all four to
        ``area:organization`` and nobody who can act on them ever sees them.
        """
        cases = {
            "itambox/core/auth/guards.py": "area:auth-rbac",
            "itambox/inventory/services.py": "area:inventory",
            "itambox/itambox/middleware.py": "area:auth-rbac",
            "itambox/extras/dashboard/widgets.py": "area:frontend",
        }
        targets = ("organization.access", "organization.models", "organization.rbac")
        for path, expected in cases.items():
            with self.subTest(path=path):
                source = source_module_for_path(path)
                self.assertEqual(owner_for_modules((source, *targets)), expected)
                self.assertNotEqual(owner_for_modules(targets), expected)

    def test_a_source_path_maps_to_exactly_one_dotted_module(self):
        self.assertEqual(source_module_for_path("itambox/core/auth/guards.py"), "core.auth.guards")
        self.assertEqual(source_module_for_path("itambox/itambox/middleware.py"), "itambox.middleware")
        self.assertEqual(source_module_for_path("itambox/assets/models/__init__.py"), "assets.models.__init__")
        for outside in ("scripts/check_architecture.py", "itambox/core/auth/guards.txt", "README.md"):
            with self.subTest(path=outside), self.assertRaises(PolicyError):
                source_module_for_path(outside)

    def test_an_unattributable_module_is_a_policy_error(self):
        with self.assertRaises(PolicyError):
            owner_for_modules(("nothing.here", "nothing.there"))

    def test_the_fingerprint_moves_when_the_policy_moves(self):
        baseline = compute_policy_fingerprint(DEFAULT_TARGETS)

        self.assertNotEqual(baseline, compute_policy_fingerprint(("itambox/assets",)))
        self.assertEqual(baseline, compute_policy_fingerprint(DEFAULT_TARGETS))

    def test_the_source_root_is_the_django_project_directory(self):
        self.assertEqual(SOURCE_ROOT, "itambox")
        self.assertEqual(tuple(DEFAULT_TARGETS), ("itambox",))


@unittest.skipUnless(
    sys.version_info[:2] == CANONICAL_PYTHON,
    "the architecture gate refuses non-canonical interpreters",
)
class RepositoryTests(unittest.TestCase):
    """A small number of named anchors in the real tree. No counts."""

    @classmethod
    def setUpClass(cls):
        cls.graph = build_graph(REPOSITORY_ROOT, DEFAULT_TARGETS)

    def test_every_participating_module_classifies(self):
        unclassified = sorted(
            module
            for module in self.graph.modules
            if not module.endswith(".__init__") and _layer_or_none(module) is None
        )

        self.assertEqual(unclassified, [])

    def test_namespace_package_modules_are_present(self):
        self.assertIn("core.views.graphql", self.graph.modules)
        self.assertIn("users.api.scim.views", self.graph.modules)

    def test_a_type_checking_back_edge_is_in_neither_blocking_graph(self):
        edge = ("licenses.reconciliation", "software.models")

        self.assertNotIn(edge, {(item.source, item.target) for item in self.graph.module_top})
        self.assertNotIn(edge, {(item.source, item.target) for item in self.graph.function_body})
        self.assertIn(edge, {(item.source, item.target) for item in self.graph.typing_only})

    def test_both_legs_of_a_module_level_settings_switch_are_emitted(self):
        """``ITAMBOX_ENV`` is never read: no condition is evaluated."""
        module_top = {(item.source, item.target) for item in self.graph.module_top}

        self.assertIn(("core.settings.__init__", "core.settings.dev"), module_top)
        self.assertIn(("core.settings.__init__", "core.settings.prod"), module_top)

    def test_the_inert_sentinel_is_exactly_the_import_free_initialisers(self):
        for module in self.graph.census["inert"]:
            with self.subTest(module=module):
                self.assertTrue(module.endswith(".__init__"))
                self.assertEqual([edge for edge in self.graph.evidence if edge.source == module], [])

    def test_no_edge_endpoint_is_an_inert_initialiser(self):
        inert = set(self.graph.census["inert"])
        endpoints = {edge.target for edge in self.graph.evidence}

        self.assertEqual(sorted(inert & endpoints), [])


def _layer_or_none(module):
    try:
        return layer_of(module)
    except PolicyError:
        return None


if __name__ == "__main__":
    unittest.main()
