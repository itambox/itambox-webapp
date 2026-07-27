"""Architecture boundary tests for resolved import cycles (issue #87, phase D).

Each cycle listed here was previously broken by a function-body import annotated
``# inline import: cycle: ...``. A deferred import hides a cycle; it does not
remove one. These tests pin the *resolved* direction so the cycle cannot be
reintroduced by a later change quietly re-adding the back edge.

The checks are AST-based and read the source tree directly, so they hold
regardless of import order at runtime and need no database.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase

# ``itambox/`` -- the Django project root that holds the app packages.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _module_path(dotted):
    """Return the file backing a first-party dotted module path."""
    candidate = PROJECT_ROOT.joinpath(*dotted.split("."))
    if candidate.with_suffix(".py").is_file():
        return candidate.with_suffix(".py")
    package_init = candidate / "__init__.py"
    if package_init.is_file():
        return package_init
    raise AssertionError(f"No source file found for module {dotted!r} under {PROJECT_ROOT}")


def _package_of(dotted):
    """Return the package a module's relative imports resolve against."""
    path = _module_path(dotted)
    return dotted if path.name == "__init__.py" else dotted.rpartition(".")[0]


def _imported_modules(node, package):
    """Yield the dotted module names a single import statement can bind.

    ``package`` anchors relative imports, so ``from .services import x`` inside
    ``inventory.models`` is reported as ``inventory.services`` -- a relative
    import closes a cycle exactly as an absolute one does.
    """
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield alias.name
        return
    if not isinstance(node, ast.ImportFrom):
        return
    base = node.module or ""
    if node.level:
        anchor = package.split(".") if package else []
        anchor = anchor[: len(anchor) - (node.level - 1)] if node.level > 1 else anchor
        base = ".".join([*anchor, base]) if base else ".".join(anchor)
    if not base:
        return
    yield base
    for alias in node.names:
        yield f"{base}.{alias.name}"


def _is_type_checking_guard(node):
    """True for ``if TYPE_CHECKING:`` -- its body never executes at runtime."""
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _runtime_nodes(tree):
    """Walk every node except the bodies of ``if TYPE_CHECKING:`` guards."""
    stack = list(ast.iter_child_nodes(tree))
    while stack:
        node = stack.pop()
        if _is_type_checking_guard(node):
            stack.extend(node.orelse)
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _edges(dotted, top_level_only):
    """Return the set of dotted module names ``dotted`` imports at runtime.

    ``top_level_only`` restricts the scan to module-scope statements (the
    imports that actually execute at import time); otherwise every scope is
    scanned, which is what makes a *deferred* cycle visible. Typing-only
    imports are excluded either way -- they create no runtime edge.
    """
    tree = ast.parse(_module_path(dotted).read_text(encoding="utf-8"))
    package = _package_of(dotted)
    nodes = tree.body if top_level_only else _runtime_nodes(tree)
    found = set()
    for node in nodes:
        if top_level_only and _is_type_checking_guard(node):
            continue
        found.update(_imported_modules(node, package))
        if top_level_only and isinstance(node, ast.Try):
            # Optional-dependency guards still execute at import time.
            for child in node.body:
                found.update(_imported_modules(child, package))
    return found


def _deferred_imports(importer, imported):
    """True when ``importer`` imports ``imported`` from inside a function body."""
    prefix = f"{imported}."
    deferred = _edges(importer, top_level_only=False) - _edges(importer, top_level_only=True)
    return any(name == imported or name.startswith(prefix) for name in deferred)


def _imports(importer, imported, top_level_only=False):
    """True when ``importer`` imports ``imported`` (or a submodule of it)."""
    prefix = f"{imported}."
    return any(name == imported or name.startswith(prefix) for name in _edges(importer, top_level_only))


def _first_party_top_level_names():
    """Top-level module/package names that resolve inside the project root."""
    names = set()
    for entry in PROJECT_ROOT.iterdir():
        if entry.is_dir() and (entry / "__init__.py").is_file():
            names.add(entry.name)
        elif entry.suffix == ".py":
            names.add(entry.stem)
    return names


class RequestContextLeafTests(SimpleTestCase):
    """``core.context`` -- the leaf that owns the request-scoped contextvars.

    ``core.managers`` used to own the tenant vars and ``itambox.middleware`` the
    user/request-id vars, while each half read the other's: managers reached into
    the middleware for ``get_current_user`` and the middleware imported the
    managers' tenant setters; ``core.auth`` read the managers while the managers
    read ``core.auth.cache``. Those were real cycles that function-body imports
    only hid. The contextvars now live in ``core.context``, which imports nothing
    first-party, so every layer depends on it and none depends on another.
    """

    def test_context_module_imports_nothing_first_party(self):
        first_party = _first_party_top_level_names()
        self.assertIn("core", first_party, "sanity: the project root must be discoverable")
        offenders = sorted(
            name for name in _edges("core.context", top_level_only=False) if name.split(".")[0] in first_party
        )
        self.assertEqual(
            offenders,
            [],
            "core.context must stay a first-party leaf -- any first-party import here "
            f"re-creates the cycle it exists to break (found: {offenders})",
        )

    def test_managers_and_middleware_no_longer_import_each_other(self):
        self.assertFalse(
            _imports("core.managers", "itambox.middleware"),
            "core.managers must not import itambox.middleware, at module scope or deferred",
        )
        self.assertFalse(
            _imports("itambox.middleware", "core.managers"),
            "itambox.middleware must not import core.managers, at module scope or deferred",
        )

    def test_both_sides_read_the_context_leaf_at_module_scope(self):
        for module in ("core.managers", "itambox.middleware", "core.auth", "core.tasks.context", "core.tasks.alerts"):
            with self.subTest(module=module):
                self.assertTrue(
                    _imports(module, "core.context", top_level_only=True),
                    f"{module} must import core.context at module scope, proving the cycle is gone",
                )

    def test_auth_no_longer_imports_managers_or_middleware(self):
        for importer in ("core.auth", "core.auth.cache", "core.auth.provisioning"):
            for target in ("core.managers", "itambox.middleware"):
                with self.subTest(importer=importer, target=target):
                    self.assertFalse(
                        _imports(importer, target),
                        f"{importer} must not import {target}, at module scope or deferred",
                    )

    def test_managers_keeps_only_the_app_registry_deferred_auth_edge(self):
        """``core.managers`` -> ``core.auth.cache`` stays deferred, and only for
        the app registry: ``core.auth.__init__`` calls ``get_user_model()`` at
        module scope, so a model-layer module cannot import it at load time.
        That is not the cycle this phase removed -- the back edge was
        ``core.auth`` -> ``core.managers``, and it is gone (asserted above)."""
        self.assertFalse(
            _imports("core.managers", "core.auth", top_level_only=True),
            "core.managers must not import core.auth at module scope (AppRegistryNotReady)",
        )
        # The remaining ``cycle:`` annotations in core.managers are for the
        # separate core.managers <-> organization edge, which this phase does not
        # touch. None of them may name the middleware or the auth package again.
        offending = [
            line.strip()
            for line in _module_path("core.managers").read_text(encoding="utf-8").splitlines()
            if "inline import" in line and "cycle" in line and ("middleware" in line or "core.auth" in line)
        ]
        self.assertEqual(
            offending,
            [],
            "no inline import in core.managers may still be justified as a cycle with the "
            f"middleware or the auth package -- those cycles are resolved (found: {offending})",
        )

    def test_mfa_is_a_leaf_of_the_provisioning_edge(self):
        self.assertFalse(
            _imports("core.mfa", "core.auth"),
            "core.mfa must not import core.auth, at module scope or deferred -- the privilege "
            "classification lives on the policy side of the edge",
        )
        self.assertTrue(
            _imports("core.auth.provisioning", "core.mfa", top_level_only=True),
            "core.auth.provisioning -> core.mfa is the load-bearing direction",
        )

    def test_privileged_role_names_has_a_single_home(self):
        import core.auth.provisioning as provisioning
        import core.mfa as mfa

        self.assertIs(
            provisioning.PRIVILEGED_ROLE_NAMES,
            mfa.PRIVILEGED_ROLE_NAMES,
            "core.auth.provisioning must consume the constant from core.mfa, not redefine it",
        )
        self.assertEqual(mfa.PRIVILEGED_ROLE_NAMES, {"Admin", "Manager"}, "the classification must be unchanged")


class RequestContextReExportTests(SimpleTestCase):
    """The re-exports must be the *same objects*, not copies.

    ``core.managers`` and ``itambox.middleware`` keep publishing the names their
    long-standing import sites use. Identity is what makes that behaviour-
    preserving: a duplicated ``ContextVar`` would give each importer a private
    slot, so a write through one path would be invisible through another and a
    ``Token`` from one would not reset the other.
    """

    MANAGER_NAMES = (
        "_current_all_accessible",
        "_current_membership",
        "_current_tenant",
        "_current_tenant_group",
        "_descendant_group_ids_cache",
        "get_current_all_accessible",
        "get_current_membership",
        "get_current_scope_conflict",
        "get_current_tenant",
        "get_current_tenant_group",
        "get_current_user",
        "set_current_all_accessible",
        "set_current_membership",
        "set_current_tenant",
        "set_current_tenant_group",
    )
    MIDDLEWARE_NAMES = (
        "_current_user",
        "_request_id",
        "get_current_all_accessible",
        "get_current_membership",
        "get_current_request_id",
        "get_current_tenant",
        "get_current_tenant_group",
        "get_current_user",
        "set_current_all_accessible",
        "set_current_membership",
        "set_current_tenant",
        "set_current_tenant_group",
        "set_current_user",
    )

    def test_managers_re_exports_are_identical_objects(self):
        import core.context as context
        import core.managers as managers

        for name in self.MANAGER_NAMES:
            with self.subTest(name=name):
                self.assertIs(getattr(managers, name), getattr(context, name))

    def test_middleware_re_exports_are_identical_objects(self):
        import core.context as context
        import itambox.middleware as middleware

        for name in self.MIDDLEWARE_NAMES:
            with self.subTest(name=name):
                self.assertIs(getattr(middleware, name), getattr(context, name))

    def test_writes_are_visible_through_every_import_path(self):
        import core.context as context
        import core.managers as managers
        import itambox.middleware as middleware

        sentinel = object()
        managers.set_current_tenant(sentinel)
        try:
            self.assertIs(context.get_current_tenant(), sentinel)
            self.assertIs(middleware.get_current_tenant(), sentinel)
        finally:
            managers.set_current_tenant(None)

        middleware.set_current_user(sentinel)
        try:
            self.assertIs(context.get_current_user(), sentinel)
            self.assertIs(managers.get_current_user(), sentinel)
        finally:
            middleware._current_user.set(None)

    def test_tokens_reset_across_import_paths(self):
        """A ``Token`` is bound to one ``ContextVar`` instance; resetting it via a
        different import path only works because they are the same object. This
        is exactly what ``CurrentUserMiddleware.process_response`` relies on."""
        import core.context as context
        import itambox.middleware as middleware

        outer = object()
        context._current_user.set(outer)
        token = middleware._current_user.set(object())
        try:
            self.assertIsNot(context.get_current_user(), outer)
            context._current_user.reset(token)
            self.assertIs(middleware.get_current_user(), outer)
        finally:
            context._current_user.set(None)

    def test_setters_still_invalidate_the_descendant_group_cache(self):
        """Every scope setter clears the memoised descendant-group ids. Losing
        that during the move would leak a previous scope's group set into the
        next one."""
        import core.context as context
        import core.managers as managers

        setters = (
            (managers.set_current_tenant, None),
            (managers.set_current_tenant_group, None),
            (managers.set_current_membership, None),
            (managers.set_current_all_accessible, False),
        )
        for setter, value in setters:
            with self.subTest(setter=setter.__name__):
                context._descendant_group_ids_cache.set({1, 2, 3})
                setter(value)
                self.assertIsNone(managers._descendant_group_ids_cache.get())


class ScopeConflictBehaviourTests(SimpleTestCase):
    """``get_current_scope_conflict`` moved module but must classify identically."""

    @staticmethod
    def _User(authenticated=True, superuser=False):
        """A stand-in principal -- the classifier only reads these two flags."""
        return SimpleNamespace(is_authenticated=authenticated, is_superuser=superuser)

    def tearDown(self):
        import core.context as context

        context.set_current_tenant(None)
        context.set_current_tenant_group(None)
        context.set_current_all_accessible(False)

    def test_two_active_scopes_conflict_for_a_plain_user(self):
        import core.managers as managers

        managers.set_current_tenant(object())
        managers.set_current_all_accessible(True)
        self.assertTrue(managers.get_current_scope_conflict(self._User()))

    def test_single_scope_anonymous_and_superuser_never_conflict(self):
        import core.managers as managers

        managers.set_current_tenant(object())
        self.assertFalse(managers.get_current_scope_conflict(self._User()))

        managers.set_current_all_accessible(True)
        self.assertFalse(managers.get_current_scope_conflict(self._User(superuser=True)))
        self.assertFalse(managers.get_current_scope_conflict(self._User(authenticated=False)))
        self.assertFalse(managers.get_current_scope_conflict(None))


class ApiSerializerBoundaryTests(SimpleTestCase):
    """``itambox.api.base`` <-> ``itambox.api.utils``.

    ``base`` needed one pure lookup helper out of ``utils``; ``utils`` needs the
    serializer base class for an ``isinstance`` check. The helper now lives in
    the leaf module ``itambox.api.related``, so the edge runs one way only:
    ``utils -> base -> related``.
    """

    def test_related_helper_module_is_a_leaf(self):
        self.assertFalse(
            _imports("itambox.api.related", "itambox.api.base"),
            "itambox.api.related must not depend on itambox.api.base",
        )
        self.assertFalse(
            _imports("itambox.api.related", "itambox.api.utils"),
            "itambox.api.related must not depend on itambox.api.utils",
        )

    def test_base_does_not_import_utils_in_any_scope(self):
        self.assertFalse(
            _imports("itambox.api.base", "itambox.api.utils"),
            "itambox.api.base must not import itambox.api.utils, at module scope or deferred",
        )

    def test_utils_imports_base_at_module_scope(self):
        self.assertTrue(
            _imports("itambox.api.utils", "itambox.api.base", top_level_only=True),
            "itambox.api.utils must import itambox.api.base at module scope, proving the cycle is gone",
        )


class LicensingBoundaryTests(SimpleTestCase):
    """``software.models`` <-> ``licenses.models`` / ``licenses.reconciliation``.

    ``licenses`` owns the FK onto ``software``, so that is the load-bearing
    direction. ``Software.license_count`` and ``Software.reconcile()`` are the
    two back edges; both now resolve their models through the app registry, and
    ``licenses.reconciliation`` is a runtime leaf that ``software.models`` may
    import at module scope without closing a loop.
    """

    def test_licenses_models_imports_software_models_at_module_scope(self):
        self.assertTrue(
            _imports("licenses.models", "software.models", top_level_only=True),
            "licenses.models owns the FK onto software.models; that edge is the load-bearing direction",
        )

    def test_software_models_does_not_import_licenses_models(self):
        self.assertFalse(
            _imports("software.models", "licenses.models"),
            "software.models must not import licenses.models, at module scope or deferred",
        )

    def test_reconciliation_is_a_runtime_leaf(self):
        for target in ("licenses.models", "software.models"):
            with self.subTest(target=target):
                self.assertFalse(
                    _imports("licenses.reconciliation", target),
                    f"licenses.reconciliation must not import {target} at runtime; it resolves models "
                    "through the app registry so software.models can depend on it",
                )

    def test_software_models_has_no_deferred_licenses_import(self):
        self.assertFalse(
            _deferred_imports("software.models", "licenses"),
            "software.models must not defer a licenses import into a function body",
        )


class InventoryStockBoundaryTests(SimpleTestCase):
    """``inventory.models`` <-> ``inventory.services``.

    Six ``save()``/``delete()`` overrides on the assignment models needed
    ``adjust_inventory_stock`` out of the service layer, while the service layer
    imports the models at module scope for the checkout/check-in flows -- so that
    is the load-bearing direction. The bookkeeping helper now lives in the leaf
    module ``inventory.stock``, which resolves the stock model through the app
    registry via the ``_stock_model_label`` hook already on
    ``AbstractAssignment``; ``inventory.services`` keeps re-exporting it.
    """

    def test_stock_module_is_a_leaf(self):
        for target in ("inventory.models", "inventory.services", "inventory.abstract_models", "assets.services"):
            with self.subTest(target=target):
                self.assertFalse(
                    _imports("inventory.stock", target),
                    f"inventory.stock must not import {target}; it is the leaf that breaks the cycle, "
                    "resolving the stock model through the app registry instead",
                )

    def test_inventory_services_imports_models_at_module_scope(self):
        self.assertTrue(
            _imports("inventory.services", "inventory.models", top_level_only=True),
            "inventory.services -> inventory.models is the load-bearing direction",
        )

    def test_models_do_not_import_inventory_services(self):
        self.assertFalse(
            _imports("inventory.models", "inventory.services"),
            "inventory.models must not import inventory.services, at module scope or deferred",
        )

    def test_inventory_models_has_no_deferred_first_party_inventory_import(self):
        for target in ("inventory.services", "inventory.stock", "inventory.kit_checkout"):
            with self.subTest(target=target):
                self.assertFalse(
                    _deferred_imports("inventory.models", target),
                    f"inventory.models must import {target} at module scope, not from a function body",
                )

    def test_services_still_exposes_adjust_inventory_stock(self):
        self.assertTrue(
            _imports("inventory.services", "inventory.stock", top_level_only=True),
            "inventory.services must keep re-exporting adjust_inventory_stock from inventory.stock",
        )

        from inventory.services import adjust_inventory_stock as via_services
        from inventory.stock import adjust_inventory_stock as via_leaf

        self.assertIs(via_services, via_leaf, "the published inventory.services call path must stay valid")


class MembershipServiceLayerTests(SimpleTestCase):
    """``organization.services.*`` -> ``organization.forms``.

    The membership/RBAC services (issue #86) exist so the domain decisions no
    longer live in ``MembershipForm``. The form imports the services; the
    services must never import the form, at module scope or deferred, or the
    extraction has only added an indirection to the same cycle.

    ``organization.services.__init__`` additionally keeps its re-export surface
    narrow: ``itambox.views.features`` imports it at module scope, so pulling
    ``membership``/``rolegrants`` (and through them ``core.auth``) into the
    package ``__init__`` would widen that edge for no benefit.
    """

    SERVICE_MODULES = ("organization.services.membership", "organization.services.rolegrants")

    def test_services_do_not_import_the_form_layer(self):
        for module in self.SERVICE_MODULES:
            with self.subTest(module=module):
                self.assertFalse(
                    _imports(module, "organization.forms"),
                    f"{module} must not import organization.forms, at module scope or deferred -- "
                    "the form depends on the service, never the reverse",
                )

    def test_form_imports_the_services_at_module_scope(self):
        self.assertTrue(
            _imports("organization.forms.membership_form", "organization.services", top_level_only=True),
            "organization.forms.membership_form -> organization.services is the load-bearing direction",
        )

    def test_package_init_does_not_pull_in_the_membership_services(self):
        for module in self.SERVICE_MODULES:
            with self.subTest(module=module):
                self.assertFalse(
                    _imports("organization.services", module),
                    f"organization.services.__init__ must not import {module}: itambox.views.features "
                    "imports the package at module scope and would gain an edge to core.auth",
                )

    def test_resource_access_helpers_stay_importable_from_the_package(self):
        """The package conversion must keep every published name byte-identical
        for the existing importers (``itambox.views.features``,
        ``organization.views.membership_views``, ``inventory.services``)."""
        import organization.services as services
        import organization.services.resource_access as resource_access

        for name in (
            "visible_to_containers",
            "is_container_scoped_unfiltered",
            "resolve_stock_access",
            "ResourceAccessDecision",
            "REASON_SAME_TENANT",
            "REASON_DIRECT_GRANT",
            "REASON_GROUP_GRANT",
            "DENIED_NO_ACTIVE_TENANT",
            "DENIED_OWNER_UNRESOLVABLE",
            "DENIED_NO_GRANT",
            "DENIED_INSUFFICIENT_LEVEL",
            "DENIED_RBAC",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(services, name), getattr(resource_access, name))


class KitCheckoutBoundaryTests(SimpleTestCase):
    """``inventory.models`` -> ``assets.services``.

    ``assets.services`` imports ``inventory.models`` and ``inventory.services``
    at module scope, so the kit checkout implementation may keep living there.
    ``Kit.checkout_to_holder`` was the back edge; it now goes through the leaf
    ``inventory.kit_checkout``, whose implementation ``AssetsConfig.ready()``
    registers once the app registry is populated.
    """

    def test_kit_checkout_module_is_a_leaf(self):
        for target in ("assets", "inventory.models", "inventory.services"):
            with self.subTest(target=target):
                self.assertFalse(
                    _imports("inventory.kit_checkout", target),
                    f"inventory.kit_checkout must not import {target}; it is the seam that keeps "
                    "inventory.models independent of assets",
                )

    def test_assets_services_imports_inventory_at_module_scope(self):
        self.assertTrue(
            _imports("assets.services", "inventory.models", top_level_only=True),
            "assets.services -> inventory.models is the load-bearing direction",
        )

    def test_inventory_models_does_not_import_assets(self):
        self.assertFalse(
            _imports("inventory.models", "assets"),
            "inventory.models must not import anything from assets, at module scope or deferred",
        )

    def test_assets_app_config_registers_the_implementation(self):
        from assets.services import checkout_kit
        from inventory.kit_checkout import get_kit_checkout

        self.assertIs(
            get_kit_checkout(),
            checkout_kit,
            "AssetsConfig.ready() must register assets.services.checkout_kit as the kit checkout "
            "implementation, otherwise Kit.checkout_to_holder has no backing callable",
        )
