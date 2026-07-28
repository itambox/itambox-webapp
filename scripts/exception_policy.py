"""Declared policy for broad, bare, and pass-only exception handlers.

This module holds the policy itself -- categories, layers, prohibited scopes,
and the identity type -- separately from the gate that enforces it, so both can
be unit tested without touching the filesystem.

Standard library only. Repository gate suites import this before project
dependencies are installed in CI.

Three ideas carry the policy:

* **Classification is the ratchet.** An identity records the *shape* of a
  handler, not just its existence, so a handler that quietly stops logging is a
  regression even though the handler count never moved.
* **Layer is derived, never typed.** A reviewer cannot mis-tag what the gate
  computes from the path.
* **Prohibited scopes are not unlockable.** Crypto, authentication,
  authorization, tenant resolution, configuration load, and lexically
  transactional code admit exactly one shape: catch, clean up, re-raise.
  Neither an annotation nor a baseline entry is permission -- that is the
  difference between this gate and a style check.

Bare handlers (``except:``) are deliberately *not* this gate's job. Flake8 E722
is selected in ``setup.cfg`` and ``scripts/flake8_baseline.json`` records zero
of them, so the existing lint ratchet already refuses new ones. This module
recognises them only so the gate can cross-check that the division still holds.
"""

import ast
import collections
import hashlib
import json
import re

SCHEMA_VERSION = 2
CANONICAL_PYTHON = (3, 12)
DEFAULT_TARGETS = ("itambox", "scripts")


class PolicyError(Exception):
    """Raised when the gate cannot produce a trustworthy result."""


class IdentityError(ValueError):
    """Raised when a handler cannot be converted to a stable identity."""


# --------------------------------------------------------------------------
# Shapes
# --------------------------------------------------------------------------

# Ordered from most to least constrained.
CLASSIFICATIONS = (
    "cleanup-reraise",
    "log-and-raise",
    "raise",
    "log-only",
    "silent",
    "pass-only",
)

# The shapes that still tell the caller the operation failed -- by re-raising
# what was caught, or by raising a documented typed failure in its place. These
# are the shapes a prohibited scope admits.
PROPAGATING_CLASSIFICATIONS = ("cleanup-reraise", "log-and-raise", "raise")

# The shapes that end the failure here. ``log-only`` belongs in this group: a
# log line is observability, not a return value, and the caller carries on as
# though the operation succeeded. That is precisely the silent failure this
# policy exists to stop, so logging never buys an exemption in a prohibited
# scope -- it only makes the resulting silence easier to investigate.
SWALLOWING_CLASSIFICATIONS = ("log-only", "silent", "pass-only")

BARE_HANDLER_TYPE = "<bare>"
SUPPRESS_PREFIX = "suppress("
BROAD_EXCEPTION_NAMES = frozenset({"Exception", "BaseException"})
SUPPRESS_NAME = "suppress"
ATOMIC_NAME = "atomic"

# Method names that mean "an operator will see this". Deliberately excludes
# ``write`` (management-command stdout/stderr) and anything on ``messages``
# (Django user messaging) -- neither reaches a log aggregator.
LOGGING_METHOD_NAMES = frozenset({"critical", "debug", "error", "exception", "info", "log", "warn", "warning"})
# Receiver names that identify a logger. ``getLogger`` covers the
# ``logging.getLogger(__name__).warning(...)`` form settings modules use.
LOGGER_RECEIVER_NAMES = frozenset({"_logger", "log", "logger", "logging"})
LOGGER_FACTORY_NAMES = frozenset({"getLogger"})


# --------------------------------------------------------------------------
# Justification grammar
# --------------------------------------------------------------------------

# The five reasons a broad or pass-only handler may remain. Anything else must
# be narrowed to the exceptions the call can actually raise.
POLICY_CATEGORIES = {
    "cleanup-reraise": "restores state or releases a resource, then re-raises",
    "boundary-isolation": "an integration call whose exception set is not enumerable",
    "task-isolation": "one item must not abort the batch",
    "render-degrade": "degrades one cell or widget instead of failing the page",
    "availability-tradeoff": "a deliberate fail-open on a non-authorization dependency",
}

# A security-sensitive scope may swallow only when the failure is observable
# and the category describes a reviewed safe boundary. This is intentionally
# narrower than the general annotation grammar: crypto, authorization, tenant
# resolution, and configuration load have no swallowing exemption at all.
# Authentication permits provider isolation and fail-open-to-recompute cache
# availability; a lexical transaction permits explicit per-item isolation.
ALLOWED_SWALLOWING_CATEGORIES_BY_DOMAIN = {
    "authentication": frozenset({"availability-tradeoff", "boundary-isolation"}),
    "transactional": frozenset({"boundary-isolation", "task-isolation"}),
}

# ``# broad except: <category>: <reason>`` (plural form accepted for symmetry
# with the inline-import policy's ``# inline imports:``).
MARKER_PATTERN = r"\bbroad\s+excepts?\s*:"
ANNOTATION_PATTERN = r"\bbroad\s+excepts?\s*:\s*(?P<category>[a-z][a-z0-9-]*)\s*:\s*(?P<reason>\S.*?)\s*$"
MARKER_RE = re.compile(MARKER_PATTERN, re.IGNORECASE)
ANNOTATION_RE = re.compile(ANNOTATION_PATTERN, re.IGNORECASE)


# --------------------------------------------------------------------------
# Scope of the scan
# --------------------------------------------------------------------------

# Generated, vendored, and test paths only -- never hand-written production
# code. ``tests`` is excluded deliberately: a broad catch in a test is often the
# point of the test (asserting that nothing escapes), and test modules are not
# part of the running application.
EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".artifacts",
        ".git",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "docs",
        "htmlcov",
        "migrations",
        "node_modules",
        "static",
        "tests",
        "venv",
    }
)
EXCLUDED_FILE_NAMES = frozenset({"conftest.py", "tests.py"})
EXCLUDED_FILE_PREFIXES = ("test_",)


# --------------------------------------------------------------------------
# Layers -- derived from the path, reported but never enforced
# --------------------------------------------------------------------------

LAYER_RULES = (
    (
        "domain",
        (
            "*/models.py",
            "*/models/*",
            "*/services.py",
            "*/services/*",
            "itambox/core/managers.py",
            "itambox/core/crypto.py",
        ),
    ),
    (
        "task",
        (
            "itambox/core/tasks/*",
            "itambox/core/events.py",
            "itambox/core/schedules.py",
            "*/tasks.py",
            "*/signals.py",
        ),
    ),
    (
        "integration",
        (
            "itambox/core/auth/*",
            "itambox/core/importers/*",
            "itambox/core/management/commands/*",
            "*/api/*",
        ),
    ),
    (
        "presentation",
        (
            "itambox/core/tables/*",
            "*/dashboard/*",
            "*/forms.py",
            "*/forms/*",
            "*/search.py",
            "*/tables.py",
            "*/templatetags/*",
        ),
    ),
    (
        "application-service",
        (
            "*/filters.py",
            "*/schema.py",
            "*/views.py",
            "*/views/*",
        ),
    ),
)
DEFAULT_LAYER = "infrastructure"
LAYERS = tuple(layer for layer, _ in LAYER_RULES) + (DEFAULT_LAYER,)


# --------------------------------------------------------------------------
# Prohibited scopes -- no annotation and no baseline entry unlocks these
# --------------------------------------------------------------------------

PROHIBITED_PATH_RULES = {
    "crypto": (
        "itambox/core/crypto.py",
        "itambox/core/management/commands/rotate_encryption_keys.py",
    ),
    "authentication": (
        "itambox/core/auth/",
        "itambox/itambox/api/authentication.py",
        "itambox/users/api/scim/authentication.py",
        "itambox/users/api/scim/provider_authentication.py",
    ),
    "authorization": (
        "itambox/core/auth/guards.py",
        "itambox/itambox/api/permissions.py",
    ),
    "tenant-resolution": (
        "itambox/core/context.py",
        "itambox/core/managers.py",
        "itambox/itambox/middleware.py",
    ),
    "config-load": ("itambox/core/settings/",),
}

# Enclosing function names that carry a security decision wherever they live.
# A view in any app that swallows inside ``has_permission`` is making an
# authorization decision, so the rule follows the name, not the directory.
PROHIBITED_SCOPE_NAMES = {
    "authentication": ("authenticate",),
    "authorization": ("has_object_permission", "has_perm", "has_permission"),
    "tenant-resolution": ("filter_by_tenant", "get_queryset", "process_request"),
}

# Lexical containment only: the handler sits *inside* ``with transaction.atomic()``,
# so swallowing leaves a live transaction that will commit partial work. Catching
# *around* an atomic block runs after the rollback and is the correct pattern, so
# it is deliberately not matched.
TRANSACTIONAL_DOMAIN = "transactional"

PROHIBITED_DOMAINS = tuple(sorted(set(PROHIBITED_PATH_RULES) | set(PROHIBITED_SCOPE_NAMES) | {TRANSACTIONAL_DOMAIN}))


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------

_LINE_NUMBER_RE = re.compile(r":\d+(?::\d+)?$")
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")


_HandlerIdentityBase = collections.namedtuple("HandlerIdentity", "path scope handler_type classification body_sha256")


class HandlerIdentity(_HandlerIdentityBase):
    """A handler's stable identity: deliberately free of line numbers.

    Inserting a line above existing debt must not read as new debt. What *does*
    change the identity is exactly what warrants re-review: the file, the
    enclosing scope, the exception type, or the shape of the body.

    A named tuple rather than a dataclass so a baseline row, a scan result, and
    a plain tuple all compare and hash identically. ``collections.namedtuple``
    rather than ``typing.NamedTuple`` because the latter refuses a custom
    ``__new__``, and validation belongs at construction.
    """

    __slots__ = ()

    def __new__(cls, path, scope, handler_type, classification, body_sha256):
        values = (path, scope, handler_type, classification, body_sha256)
        for name, value in zip(cls._fields, values, strict=True):
            if not isinstance(value, str):
                raise IdentityError(f"handler {name} must be a string")
        _validate_identity_path(path)
        if classification not in CLASSIFICATIONS:
            raise IdentityError(f"unknown handler classification {classification!r}")
        if not handler_type:
            raise IdentityError("handler type must not be empty")
        if not re.fullmatch(r"[0-9a-f]{64}", body_sha256):
            raise IdentityError("handler body_sha256 must be a lowercase SHA-256 digest")
        return super().__new__(cls, path, scope, handler_type, classification, body_sha256)

    def as_dict(self):
        return dict(zip(self._fields, self, strict=True))


def _validate_identity_path(path):
    if not path:
        raise IdentityError("handler path must not be empty")
    if "\\" in path or _WINDOWS_ABSOLUTE_RE.match(path):
        raise IdentityError("handler path must be a POSIX repository-relative path")
    if path.startswith("/"):
        raise IdentityError("handler path must be repository-relative")
    if ".." in path.split("/"):
        raise IdentityError("handler path must not traverse outside the repository")
    if _LINE_NUMBER_RE.search(path):
        raise IdentityError("handler path must not carry a line number")


def structural_body_sha256(statements):
    """Hash handler structure without line/column attributes or formatting."""
    module = ast.Module(body=list(statements), type_ignores=[])
    payload = ast.dump(module, annotate_fields=True, include_attributes=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Type normalisation and gate scope
# --------------------------------------------------------------------------


def normalise_handler_type(node):
    """Render an ``except`` clause's type as a stable, order-free string."""
    if node is None:
        return BARE_HANDLER_TYPE
    items = node.elts if isinstance(node, ast.Tuple) else [node]
    names = sorted(ast.unparse(item) for item in items)
    if len(names) == 1:
        return names[0]
    return "(" + ", ".join(names) + ")"


def normalise_suppress_type(call):
    """Render ``contextlib.suppress(...)`` in the same order-free form."""
    names = sorted(ast.unparse(argument) for argument in call.args)
    return f"{SUPPRESS_PREFIX}{', '.join(names)})"


def handler_type_components(handler_type):
    text = handler_type
    if text == BARE_HANDLER_TYPE:
        return ()
    if text.startswith(SUPPRESS_PREFIX) and text.endswith(")"):
        text = text[len(SUPPRESS_PREFIX) : -1]
    elif text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    return tuple(part.strip() for part in text.split(",") if part.strip())


def is_broad(handler_type):
    """True when the clause catches ``Exception`` or ``BaseException``."""
    for component in handler_type_components(handler_type):
        leaf = component.rsplit(".", 1)[-1]
        if leaf in BROAD_EXCEPTION_NAMES:
            return True
    return False


def is_propagating(classification):
    """True when the caller still learns the operation failed.

    This is the security property the prohibited scopes turn on: not whether
    the failure was recorded, but whether it was *reported*.
    """
    return classification in PROPAGATING_CLASSIFICATIONS


def is_prohibited_violation(domains, classification, category):
    """Whether a security-domain handler violates the hard policy.

    Propagating handlers are always safe. A swallowing handler is admissible
    only when it logs and carries a category explicitly allowed by every
    prohibited domain it inhabits. ``pass-only`` and ``silent`` therefore can
    never be annotated into compliance.
    """
    if not domains or is_propagating(classification):
        return False
    if classification != "log-only" or category is None:
        return True
    return any(category not in ALLOWED_SWALLOWING_CATEGORIES_BY_DOMAIN.get(domain, ()) for domain in domains)


def is_in_gate_scope(handler_type, classification):
    """Broad handlers, bare handlers, and pass-only handlers of any type.

    A narrow handler that actually does something is out of scope on purpose:
    naming the exception you handle is the behaviour this policy wants.
    """
    if classification == "pass-only":
        return True
    if handler_type == BARE_HANDLER_TYPE:
        return True
    return is_broad(handler_type)


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


def _effective_body(statements):
    """Drop a lone docstring; ``"nothing to do"`` is a comment, not behaviour."""
    return [
        statement
        for statement in statements
        if not (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        )
    ]


def _iter_executed_nodes(statements):
    """Walk the handler body without entering a nested callable.

    A ``raise`` inside a closure defined by the handler does not run when the
    handler runs, so it must not make the handler look like it re-raises.
    """
    stack = list(statements)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _is_logger_receiver(node):
    if isinstance(node, ast.Name):
        return node.id.lower() in LOGGER_RECEIVER_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr.lower() in LOGGER_RECEIVER_NAMES
    if isinstance(node, ast.Call):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        return name in LOGGER_FACTORY_NAMES
    return False


def _is_logging_call(node):
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr not in LOGGING_METHOD_NAMES:
        return False
    return _is_logger_receiver(node.func.value)


def _is_reraise(node, bound_name):
    if not isinstance(node, ast.Raise):
        return False
    if node.exc is None:
        return True
    return bool(bound_name) and isinstance(node.exc, ast.Name) and node.exc.id == bound_name


_FALLTHROUGH = "fallthrough"
_RERAISE = "reraise"
_RAISE = "raise"
_RETURN = "return"
_BREAK = "break"
_CONTINUE = "continue"
_PROPAGATING_OUTCOMES = {_RERAISE, _RAISE}
_SWALLOWING_OUTCOMES = {_FALLTHROUGH, _RETURN, _BREAK, _CONTINUE}


def _statement_logs_unconditionally(statement):
    """Whether executing this non-compound statement necessarily logs."""
    return any(_is_logging_call(node) for node in _iter_executed_nodes([statement]))


def _analyse_block(statements, initial_paths, bound_name):
    """Conservatively track every reachable (outcome, logged) handler path."""
    paths = set(initial_paths)
    for statement in _effective_body(statements):
        next_paths = set()
        for outcome, logged in paths:
            if outcome != _FALLTHROUGH:
                next_paths.add((outcome, logged))
                continue
            next_paths.update(_analyse_statement(statement, logged, bound_name))
        paths = next_paths
    return paths


def _analyse_if(statement, logged, bound_name):
    constant = statement.test.value if isinstance(statement.test, ast.Constant) else None
    if constant is True:
        branches = (statement.body,)
    elif constant is False:
        branches = (statement.orelse,)
    else:
        branches = (statement.body, statement.orelse)
    return set().union(*(_analyse_block(branch, {(_FALLTHROUGH, logged)}, bound_name) for branch in branches))


def _analyse_loop(statement, logged, bound_name):
    # A loop may execute zero times. Normal exhaustion (including a continue)
    # reaches ``else``; break bypasses it and proceeds after the loop.
    normal_paths = {(_FALLTHROUGH, logged)}
    break_paths = set()
    terminal_paths = set()
    body_paths = _analyse_block(statement.body, {(_FALLTHROUGH, logged)}, bound_name)
    for outcome, path_logged in body_paths:
        if outcome == _BREAK:
            break_paths.add((_FALLTHROUGH, path_logged))
        elif outcome in {_FALLTHROUGH, _CONTINUE}:
            normal_paths.add((_FALLTHROUGH, path_logged))
        else:
            terminal_paths.add((outcome, path_logged))
    if statement.orelse:
        normal_paths = _analyse_block(statement.orelse, normal_paths, bound_name)
    return terminal_paths | break_paths | normal_paths


def _apply_finally(paths, finalbody, bound_name):
    if not finalbody:
        return paths
    result = set()
    for prior_outcome, logged in paths:
        final_paths = _analyse_block(finalbody, {(_FALLTHROUGH, logged)}, bound_name)
        for final_outcome, final_logged in final_paths:
            # A terminating finally statement overrides the pending outcome;
            # otherwise the try/handler outcome continues unchanged.
            outcome = prior_outcome if final_outcome == _FALLTHROUGH else final_outcome
            result.add((outcome, final_logged))
    return result


def _analyse_try(statement, logged, bound_name):
    body_paths = _analyse_block(statement.body, {(_FALLTHROUGH, logged)}, bound_name)
    normal = {path for path in body_paths if path[0] == _FALLTHROUGH}
    paths = body_paths - normal
    paths |= _analyse_block(statement.orelse, normal, bound_name) if statement.orelse else normal

    # Any protected statement may fail before a preceding log. Every nested
    # handler is therefore an independent reachable path from the entry state.
    for handler in statement.handlers:
        paths |= _analyse_block(handler.body, {(_FALLTHROUGH, logged)}, handler.name or bound_name)
    if not statement.handlers:
        paths.add((_RAISE, logged))
    return _apply_finally(paths, statement.finalbody, bound_name)


def _analyse_match(statement, logged, bound_name):
    paths = set()
    has_catch_all = False
    for case in statement.cases:
        if isinstance(case.pattern, ast.MatchAs) and case.pattern.pattern is None and case.guard is None:
            has_catch_all = True
        paths |= _analyse_block(case.body, {(_FALLTHROUGH, logged)}, bound_name)
    if not has_catch_all:
        paths.add((_FALLTHROUGH, logged))
    return paths


def _analyse_statement(statement, logged, bound_name):
    if isinstance(statement, ast.Raise):
        return {(_RERAISE if _is_reraise(statement, bound_name) else _RAISE, logged)}
    if isinstance(statement, ast.Return):
        return {(_RETURN, logged)}
    if isinstance(statement, ast.Break):
        return {(_BREAK, logged)}
    if isinstance(statement, ast.Continue):
        return {(_CONTINUE, logged)}
    if isinstance(statement, ast.If):
        return _analyse_if(statement, logged, bound_name)
    if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
        return _analyse_loop(statement, logged, bound_name)
    if isinstance(statement, (ast.With, ast.AsyncWith)):
        return _analyse_block(statement.body, {(_FALLTHROUGH, logged)}, bound_name)
    if isinstance(statement, (ast.Try, ast.TryStar)):
        return _analyse_try(statement, logged, bound_name)
    if isinstance(statement, ast.Match):
        return _analyse_match(statement, logged, bound_name)
    return {(_FALLTHROUGH, logged or _statement_logs_unconditionally(statement))}


def classify_body(statements, bound_name=None):
    """Reduce a handler body to one of ``CLASSIFICATIONS``."""
    body = _effective_body(statements)
    if not body or all(isinstance(statement, ast.Pass) for statement in body):
        return "pass-only"

    paths = _analyse_block(body, {(_FALLTHROUGH, False)}, bound_name)
    outcomes = {outcome for outcome, _ in paths}
    if outcomes and outcomes <= _PROPAGATING_OUTCOMES:
        if outcomes == {_RERAISE}:
            return "cleanup-reraise"
        return "log-and-raise" if all(logged for _, logged in paths) else "raise"

    swallowing_paths = [(outcome, logged) for outcome, logged in paths if outcome in _SWALLOWING_OUTCOMES]
    return "log-only" if swallowing_paths and all(logged for _, logged in swallowing_paths) else "silent"


def classify_handler(handler):
    """Classify one ``ast.ExceptHandler``."""
    return classify_body(handler.body, handler.name)


# --------------------------------------------------------------------------
# Layer and prohibition resolution
# --------------------------------------------------------------------------


def _matches(path, pattern):
    """Match a repository-relative POSIX path against a ``*``-prefixed rule.

    Intentionally simpler than ``fnmatch``: the only forms used are a literal
    prefix, ``*/name.py``, and ``prefix/*``.
    """
    if pattern.startswith("*/"):
        suffix = pattern[1:]
        if pattern.endswith("/*"):
            return suffix[:-1] in f"/{path}"
        return f"/{path}".endswith(suffix)
    if pattern.endswith("/*"):
        return path.startswith(pattern[:-1])
    return path == pattern


def resolve_layer(path):
    """Which architectural layer a file belongs to. Total by construction."""
    for layer, patterns in LAYER_RULES:
        if any(_matches(path, pattern) for pattern in patterns):
            return layer
    return DEFAULT_LAYER


def resolve_prohibited_domains(path, scope_names, transactional):
    """Every prohibited domain a handler falls under, sorted and de-duplicated."""
    domains = set()
    for domain, prefixes in PROHIBITED_PATH_RULES.items():
        if any(path == prefix or path.startswith(prefix) for prefix in prefixes):
            domains.add(domain)
    names = set(scope_names)
    for domain, reserved in PROHIBITED_SCOPE_NAMES.items():
        if names & set(reserved):
            domains.add(domain)
    if transactional:
        domains.add(TRANSACTIONAL_DOMAIN)
    return tuple(sorted(domains))


# --------------------------------------------------------------------------
# Policy fingerprint
# --------------------------------------------------------------------------


def compute_policy_fingerprint(targets):
    """Bind a baseline to the exact policy that produced it.

    Widening a prohibited scope or renaming a category invalidates every
    existing baseline rather than silently shrinking what the gate covers.
    """
    payload = {
        "schema_version": SCHEMA_VERSION,
        "canonical_python": f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}",
        "categories": sorted(POLICY_CATEGORIES),
        "allowed_swallowing_categories_by_domain": {
            domain: sorted(categories) for domain, categories in sorted(ALLOWED_SWALLOWING_CATEGORIES_BY_DOMAIN.items())
        },
        "classifications": list(CLASSIFICATIONS),
        "propagating_classifications": list(PROPAGATING_CLASSIFICATIONS),
        "swallowing_classifications": list(SWALLOWING_CLASSIFICATIONS),
        "marker_pattern": MARKER_PATTERN,
        "annotation_pattern": ANNOTATION_PATTERN,
        "broad_exception_names": sorted(BROAD_EXCEPTION_NAMES),
        "logging_method_names": sorted(LOGGING_METHOD_NAMES),
        "logger_receiver_names": sorted(LOGGER_RECEIVER_NAMES),
        "logger_factory_names": sorted(LOGGER_FACTORY_NAMES),
        "suppress_name": SUPPRESS_NAME,
        "atomic_name": ATOMIC_NAME,
        "layer_rules": [[layer, list(patterns)] for layer, patterns in LAYER_RULES],
        "default_layer": DEFAULT_LAYER,
        "prohibited_path_rules": {domain: list(paths) for domain, paths in sorted(PROHIBITED_PATH_RULES.items())},
        "prohibited_scope_names": {domain: list(names) for domain, names in sorted(PROHIBITED_SCOPE_NAMES.items())},
        "transactional_domain": TRANSACTIONAL_DOMAIN,
        "excluded_directory_names": sorted(EXCLUDED_DIRECTORY_NAMES),
        "excluded_file_names": sorted(EXCLUDED_FILE_NAMES),
        "excluded_file_prefixes": sorted(EXCLUDED_FILE_PREFIXES),
        "targets": list(targets),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
