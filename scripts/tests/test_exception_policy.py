"""Tests for the exception-handling policy gate.

The gate answers three separate questions, and this suite keeps them separate:

* **What shape is this handler?** Classification is the whole ratchet. A handler
  that stops logging is a regression even though the handler count is unchanged,
  so the classifier is tested far harder than the file walker.
* **Is this handler allowed here at all?** Prohibited scopes (crypto,
  authentication, authorization, tenant resolution, configuration load, and
  lexically transactional code) admit exactly one shape. Neither an annotation
  nor a baseline entry may unlock them -- both are asserted explicitly, because
  an unlock is the one bug that would make the gate worthless.
* **Is this handler new?** Identity is (path, scope, type, classification,
  structural body SHA-256) and deliberately excludes line numbers.

Standard library only: CI runs this suite on the bare interpreter before any
project dependency is installed.
"""

import ast
import io
import json
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scripts.check_exception_policy import (
    collect_handlers,
    compare_baseline,
    load_baseline,
    main,
    pair_moved_identities,
    write_baseline,
)
from scripts.exception_policy import (
    CANONICAL_PYTHON,
    CLASSIFICATIONS,
    POLICY_CATEGORIES,
    PROPAGATING_CLASSIFICATIONS,
    SCHEMA_VERSION,
    SWALLOWING_CLASSIFICATIONS,
    HandlerIdentity,
    IdentityError,
    PolicyError,
    classify_handler,
    compute_policy_fingerprint,
    is_in_gate_scope,
    is_prohibited_violation,
    is_propagating,
    normalise_handler_type,
    resolve_layer,
    resolve_prohibited_domains,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BODY_A = "a" * 64
BODY_B = "b" * 64
BODY_C = "c" * 64


def write(root, relative, body):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return path


def scan(body, relative="itambox/core/sample.py", targets=("itambox",)):
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        write(root, relative, body)
        return collect_handlers(root, list(targets))


def finding_prefixes(result):
    """Return behavioural identity fields while ignoring the tested digest value."""
    return {tuple(identity[:4]): count for identity, count in result.findings.items()}


def only_handler(body, relative="itambox/core/sample.py"):
    """Parse a snippet and classify its single except handler."""
    tree = ast.parse(textwrap.dedent(body).lstrip())
    handlers = [node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)]
    if len(handlers) != 1:
        raise AssertionError(f"expected exactly one handler, found {len(handlers)}")
    return classify_handler(handlers[0])


class ClassificationTests(unittest.TestCase):
    """Classification is the ratchet; a wrong shape silently retires debt."""

    def test_pass_only_body(self):
        self.assertEqual(
            only_handler(
                """
                try:
                    work()
                except Exception:
                    pass
                """
            ),
            "pass-only",
        )

    def test_docstring_only_body_is_pass_only(self):
        self.assertEqual(
            only_handler(
                """
                try:
                    work()
                except Exception:
                    "nothing to do"
                """
            ),
            "pass-only",
        )

    def test_bare_raise_is_cleanup_reraise(self):
        self.assertEqual(
            only_handler(
                """
                try:
                    work()
                except Exception:
                    restore()
                    raise
                """
            ),
            "cleanup-reraise",
        )

    def test_raise_of_the_bound_name_is_cleanup_reraise(self):
        """`raise exc` re-raises what was caught; only the spelling differs."""
        self.assertEqual(
            only_handler(
                """
                try:
                    work()
                except Exception as exc:
                    restore()
                    raise exc
                """
            ),
            "cleanup-reraise",
        )

    def test_conditional_reraise_with_a_fallback_is_swallowing(self):
        self.assertEqual(
            only_handler(
                """
                try:
                    work()
                except Exception:
                    if debug_enabled():
                        raise
                    return fallback()
                """
            ),
            "silent",
        )

    def test_early_return_before_top_level_reraise_is_swallowing(self):
        self.assertEqual(
            only_handler(
                """
                try:
                    work()
                except Exception:
                    if fail_open:
                        return True
                    raise
                """
            ),
            "silent",
        )

    def test_conditional_unreachable_logging_does_not_make_fallback_observable(self):
        self.assertEqual(
            only_handler(
                """
                try:
                    work()
                except Exception:
                    if False:
                        logger.error('failed')
                    return None
                """
            ),
            "silent",
        )

    def test_loop_break_does_not_receive_logging_from_loop_else(self):
        self.assertEqual(
            only_handler(
                """
                try:
                    work()
                except Exception:
                    for item in items:
                        if stop(item):
                            break
                    else:
                        logger.error('failed')
                    return None
                """
            ),
            "silent",
        )

    def test_raise_of_a_different_name_is_not_cleanup_reraise(self):
        self.assertEqual(
            only_handler(
                """
                try:
                    work()
                except Exception as exc:
                    raise ValueError('converted') from exc
                """
            ),
            "raise",
        )

    def test_raise_of_an_unrelated_bound_name_is_not_cleanup_reraise(self):
        self.assertEqual(
            only_handler(
                """
                try:
                    work()
                except Exception as exc:
                    other = ValueError('converted')
                    raise other
                """
            ),
            "raise",
        )

    def test_logging_without_raising(self):
        self.assertEqual(
            only_handler(
                """
                try:
                    work()
                except Exception:
                    logger.warning('failed')
                """
            ),
            "log-only",
        )

    def test_logging_and_raising_a_new_exception(self):
        self.assertEqual(
            only_handler(
                """
                try:
                    work()
                except Exception as exc:
                    logger.exception('failed')
                    raise ValueError('converted') from exc
                """
            ),
            "log-and-raise",
        )

    def test_cleanup_reraise_wins_over_logging(self):
        """Re-raising is the property the prohibited scopes care about."""
        self.assertEqual(
            only_handler(
                """
                try:
                    work()
                except Exception:
                    logger.exception('failed')
                    raise
                """
            ),
            "cleanup-reraise",
        )

    def test_silent_body_that_is_neither_pass_nor_log_nor_raise(self):
        self.assertEqual(
            only_handler(
                """
                try:
                    work()
                except Exception:
                    value = None
                """
            ),
            "silent",
        )

    def test_get_logger_chain_counts_as_logging(self):
        """`logging.getLogger(__name__).warning(...)` is how settings modules log."""
        self.assertEqual(
            only_handler(
                """
                try:
                    work()
                except Exception as exc:
                    logging.getLogger(__name__).warning('failed: %s', exc)
                """
            ),
            "log-only",
        )

    def test_user_facing_messages_are_not_logging(self):
        """`messages.error(request, ...)` reaches a browser, not an operator."""
        self.assertEqual(
            only_handler(
                """
                try:
                    work()
                except Exception:
                    messages.error(request, 'failed')
                """
            ),
            "silent",
        )

    def test_command_stderr_writes_are_not_logging(self):
        """A management command's stderr is not the application log."""
        self.assertEqual(
            only_handler(
                """
                try:
                    work()
                except Exception as exc:
                    self.stderr.write(self.style.ERROR(f'failed: {exc}'))
                """
            ),
            "silent",
        )

    def test_nested_function_bodies_do_not_classify_the_handler(self):
        """A raise that only runs later is not this handler's behaviour."""
        self.assertEqual(
            only_handler(
                """
                try:
                    work()
                except Exception:
                    def retry():
                        logger.warning('later')
                        raise RuntimeError('later')

                    register(retry)
                """
            ),
            "silent",
        )

    def test_every_classification_is_declared_exactly_once(self):
        self.assertEqual(len(set(CLASSIFICATIONS)), len(CLASSIFICATIONS))

    def test_classifications_partition_into_propagating_and_swallowing(self):
        """Every shape must be decidable; an unclassified shape is an unlocked door."""
        self.assertEqual(
            set(PROPAGATING_CLASSIFICATIONS) | set(SWALLOWING_CLASSIFICATIONS),
            set(CLASSIFICATIONS),
            "a shape in neither group would be silently admitted everywhere",
        )
        self.assertEqual(set(PROPAGATING_CLASSIFICATIONS) & set(SWALLOWING_CLASSIFICATIONS), set())

    def test_only_shapes_that_reach_the_caller_are_propagating(self):
        for classification in ("cleanup-reraise", "raise", "log-and-raise"):
            with self.subTest(classification=classification):
                self.assertTrue(is_propagating(classification))

    def test_logging_alone_does_not_propagate(self):
        """A log line is observability, not a return value; the caller proceeds."""
        for classification in ("log-only", "silent", "pass-only"):
            with self.subTest(classification=classification):
                self.assertFalse(is_propagating(classification))


class HandlerTypeTests(unittest.TestCase):
    """Type normalisation decides both gate scope and identity."""

    def parse_type(self, source):
        tree = ast.parse(textwrap.dedent(source).lstrip())
        handler = next(node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler))
        return normalise_handler_type(handler.type)

    def test_bare_handler(self):
        self.assertEqual(self.parse_type("try:\n    work()\nexcept:\n    pass\n"), "<bare>")

    def test_single_type(self):
        self.assertEqual(self.parse_type("try:\n    work()\nexcept ValueError:\n    pass\n"), "ValueError")

    def test_dotted_type(self):
        self.assertEqual(
            self.parse_type("try:\n    work()\nexcept Tenant.DoesNotExist:\n    pass\n"),
            "Tenant.DoesNotExist",
        )

    def test_tuple_members_are_sorted_so_reordering_is_not_a_diff(self):
        self.assertEqual(
            self.parse_type("try:\n    work()\nexcept (TypeError, ValueError):\n    pass\n"),
            self.parse_type("try:\n    work()\nexcept (ValueError, TypeError):\n    pass\n"),
        )
        self.assertEqual(
            self.parse_type("try:\n    work()\nexcept (ValueError, TypeError):\n    pass\n"),
            "(TypeError, ValueError)",
        )

    def test_gate_scope_covers_broad_bare_and_pass_only(self):
        self.assertTrue(is_in_gate_scope("Exception", "log-only"))
        self.assertTrue(is_in_gate_scope("BaseException", "log-only"))
        self.assertTrue(is_in_gate_scope("<bare>", "log-only"))
        self.assertTrue(is_in_gate_scope("ValueError", "pass-only"))
        self.assertTrue(is_in_gate_scope("(TypeError, ValueError)", "pass-only"))

    def test_gate_scope_excludes_narrow_handlers_that_do_something(self):
        self.assertFalse(is_in_gate_scope("ValueError", "log-only"))
        self.assertFalse(is_in_gate_scope("Tenant.DoesNotExist", "silent"))

    def test_a_tuple_containing_a_broad_name_is_broad(self):
        self.assertTrue(is_in_gate_scope("(Exception, ValueError)", "log-only"))

    def test_qualified_broad_names_are_broad(self):
        self.assertTrue(is_in_gate_scope("builtins.Exception", "log-only"))


class SuppressTests(unittest.TestCase):
    """`contextlib.suppress(Exception)` is a pass-only handler by another name."""

    def test_broad_suppress_is_collected_as_pass_only(self):
        result = scan(
            """
            from contextlib import suppress


            def work():
                with suppress(Exception):
                    risky()
            """
        )
        self.assertEqual(
            finding_prefixes(result),
            {("itambox/core/sample.py", "FunctionDef:work", "suppress(Exception)", "pass-only"): 1},
        )

    def test_dotted_suppress_is_collected(self):
        result = scan(
            """
            import contextlib


            def work():
                with contextlib.suppress(BaseException):
                    risky()
            """
        )
        self.assertEqual(
            finding_prefixes(result),
            {("itambox/core/sample.py", "FunctionDef:work", "suppress(BaseException)", "pass-only"): 1},
        )

    def test_narrow_suppress_is_still_pass_only_and_tracked(self):
        result = scan(
            """
            from contextlib import suppress


            def work():
                with suppress(ValueError):
                    risky()
            """
        )
        self.assertEqual(
            finding_prefixes(result),
            {("itambox/core/sample.py", "FunctionDef:work", "suppress(ValueError)", "pass-only"): 1},
        )

    def test_suppress_arguments_are_sorted(self):
        result = scan(
            """
            from contextlib import suppress


            def work():
                with suppress(ValueError, KeyError):
                    risky()
            """
        )
        self.assertEqual(
            finding_prefixes(result),
            {("itambox/core/sample.py", "FunctionDef:work", "suppress(KeyError, ValueError)", "pass-only"): 1},
        )

    def test_suppress_in_a_prohibited_scope_cannot_hide_there(self):
        result = scan(
            """
            from contextlib import suppress


            def get_fernet():
                with suppress(Exception):
                    risky()
            """,
            relative="itambox/core/crypto.py",
        )
        self.assertEqual([entry.domains for entry in result.prohibited], [("crypto",)])

    def test_unrelated_context_managers_are_ignored(self):
        result = scan(
            """
            def work():
                with open('f') as handle:
                    handle.read()
            """
        )
        self.assertEqual(finding_prefixes(result), {})


class ScanTests(unittest.TestCase):
    """The scanner records scope context and ignores out-of-scope handlers."""

    def test_body_fingerprint_ignores_formatting_but_tracks_structure(self):
        compact = scan(
            """
            def work():
                try:
                    risky()
                except Exception:
                    return None
            """
        )
        reformatted = scan(
            """


            def work():
                try:
                    risky()
                except Exception:
                    # Moving lines and comments must not create baseline churn.
                    return None
            """
        )
        changed = scan(
            """
            def work():
                try:
                    risky()
                except Exception:
                    return False
            """
        )
        compact_digest = next(iter(compact.findings)).body_sha256
        self.assertEqual(compact_digest, next(iter(reformatted.findings)).body_sha256)
        self.assertNotEqual(compact_digest, next(iter(changed.findings)).body_sha256)

    def test_handlers_are_collected_with_scope_context(self):
        result = scan(
            """
            class Backend:
                def authenticate(self):
                    try:
                        work()
                    except Exception:
                        return None
            """
        )
        self.assertEqual(
            finding_prefixes(result),
            {("itambox/core/sample.py", "ClassDef:Backend/FunctionDef:authenticate", "Exception", "silent"): 1},
        )

    def test_module_level_handlers_have_an_empty_scope(self):
        result = scan(
            """
            try:
                CONFIG = load()
            except Exception:
                CONFIG = {}
            """
        )
        self.assertEqual(
            finding_prefixes(result),
            {("itambox/core/sample.py", "", "Exception", "silent"): 1},
        )

    def test_narrow_handlers_that_act_are_not_collected(self):
        result = scan(
            """
            def work():
                try:
                    risky()
                except ValueError:
                    logger.warning('bad value')
            """
        )
        self.assertEqual(finding_prefixes(result), {})

    def test_repeated_identical_handlers_are_counted(self):
        result = scan(
            """
            def work():
                try:
                    a()
                except Exception:
                    pass
                try:
                    b()
                except Exception:
                    pass
            """
        )
        self.assertEqual(
            finding_prefixes(result),
            {("itambox/core/sample.py", "FunctionDef:work", "Exception", "pass-only"): 2},
        )

    def test_async_functions_are_scanned(self):
        result = scan(
            """
            async def work():
                try:
                    await risky()
                except Exception:
                    pass
            """
        )
        self.assertEqual(
            finding_prefixes(result),
            {("itambox/core/sample.py", "AsyncFunctionDef:work", "Exception", "pass-only"): 1},
        )

    def test_identity_ignores_line_numbers_and_formatting(self):
        first = scan(
            """
            def work():
                try:
                    risky()
                except Exception:
                    pass
            """
        )
        second = scan(
            """
            # a new comment
            #
            # and another


            def work():
                # leading noise
                value = 1
                try:
                    risky()
                except Exception:
                    pass
            """
        )
        self.assertEqual(dict(first.findings), dict(second.findings))

    def test_unparsable_source_fails_closed(self):
        with self.assertRaises(PolicyError):
            scan("def broken(:\n    pass\n")


class ExclusionTests(unittest.TestCase):
    """Generated, vendored, and test trees are never production code."""

    def test_tests_migrations_and_vendor_paths_are_excluded(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            body = """
                def work():
                    try:
                        risky()
                    except Exception:
                        pass
                """
            for relative in (
                "itambox/core/tests/test_thing.py",
                "itambox/core/tests/helpers.py",
                "itambox/core/test_thing.py",
                "itambox/core/tests.py",
                "itambox/conftest.py",
                "itambox/core/migrations/0001_initial.py",
                "itambox/static/dist/bundle.py",
                "itambox/docs/example.py",
                "itambox/node_modules/pkg/thing.py",
            ):
                write(root, relative, body)
            write(root, "itambox/core/real.py", body)
            result = collect_handlers(root, ["itambox"])
        self.assertEqual(
            finding_prefixes(result),
            {("itambox/core/real.py", "FunctionDef:work", "Exception", "pass-only"): 1},
        )

    def test_files_outside_the_configured_targets_are_ignored(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            write(
                root,
                "elsewhere/thing.py",
                """
                def work():
                    try:
                        risky()
                    except Exception:
                        pass
                """,
            )
            result = collect_handlers(root, ["itambox"])
        self.assertEqual(finding_prefixes(result), {})


class AnnotationTests(unittest.TestCase):
    """An annotation moves a handler out of the ratchet, never out of a prohibition."""

    def test_trailing_annotation_marks_a_handler_as_justified(self):
        result = scan(
            """
            def work():
                try:
                    risky()
                except Exception:  # broad except: task-isolation: one row must not kill the batch
                    logger.warning('row failed')
            """
        )
        self.assertEqual(finding_prefixes(result), {})
        self.assertEqual(dict(result.annotated), {"task-isolation": 1})

    def test_preceding_comment_annotation_is_accepted(self):
        result = scan(
            """
            def work():
                try:
                    risky()
                # broad except: render-degrade: one cell must not 500 the page
                except Exception:
                    return ''
            """
        )
        self.assertEqual(finding_prefixes(result), {})
        self.assertEqual(dict(result.annotated), {"render-degrade": 1})

    def test_multi_line_preceding_comment_block_is_accepted(self):
        result = scan(
            """
            def work():
                try:
                    risky()
                # broad except: boundary-isolation: the vendor SDK raises
                #   undocumented errors
                except Exception:
                    logger.exception('vendor call failed')
            """
        )
        self.assertEqual(finding_prefixes(result), {})
        self.assertEqual(dict(result.annotated), {"boundary-isolation": 1})

    def test_every_documented_category_is_accepted(self):
        for category in sorted(POLICY_CATEGORIES):
            with self.subTest(category=category):
                result = scan(
                    f"""
                    def work():
                        try:
                            risky()
                        except Exception:  # broad except: {category}: documented reason
                            logger.warning('x')
                    """
                )
                self.assertEqual(finding_prefixes(result), {})
                self.assertEqual(dict(result.annotated), {category: 1})

    def test_unknown_category_is_a_policy_error(self):
        result = scan(
            """
            def work():
                try:
                    risky()
                except Exception:  # broad except: because-i-said-so: no
                    pass
            """
        )
        self.assertEqual(len(result.malformed), 1)
        self.assertIn("because-i-said-so", result.malformed[0].problem)

    def test_marker_without_a_category_is_a_policy_error(self):
        result = scan(
            """
            def work():
                try:
                    risky()
                except Exception:  # broad except: it is fine really
                    pass
            """
        )
        self.assertEqual(len(result.malformed), 1)

    def test_unrelated_comments_do_not_annotate(self):
        result = scan(
            """
            def work():
                try:
                    risky()
                except Exception:  # TODO: tidy this up
                    pass
            """
        )
        self.assertEqual(
            finding_prefixes(result),
            {("itambox/core/sample.py", "FunctionDef:work", "Exception", "pass-only"): 1},
        )
        self.assertEqual(result.malformed, [])

    def test_an_annotation_does_not_carry_to_the_next_handler(self):
        """Handlers are multi-line; silent group inheritance would be a footgun."""
        result = scan(
            """
            def work():
                try:
                    risky()
                except ValueError:  # broad except: render-degrade: documented reason
                    pass
                except Exception:
                    pass
            """
        )
        self.assertEqual(
            finding_prefixes(result),
            {("itambox/core/sample.py", "FunctionDef:work", "Exception", "pass-only"): 1},
        )
        self.assertEqual(dict(result.annotated), {"render-degrade": 1})


class LayerTests(unittest.TestCase):
    """Layer is derived from the path, never typed by a human."""

    def test_layers_are_resolved_from_the_path(self):
        cases = {
            "itambox/assets/models/asset.py": "domain",
            "itambox/assets/services.py": "domain",
            "itambox/assets/tasks/checkout.py": "task",
            "itambox/assets/signals.py": "task",
            "itambox/core/importers/snipeit.py": "integration",
            "itambox/assets/api/serializers.py": "integration",
            "itambox/assets/tables.py": "presentation",
            "itambox/extras/templatetags/money.py": "presentation",
            "itambox/assets/views/request_views.py": "application-service",
            "itambox/itambox/views/generic/edit.py": "application-service",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(resolve_layer(path), expected)

    def test_every_path_resolves_to_some_layer(self):
        self.assertTrue(resolve_layer("itambox/itambox/middleware.py"))
        self.assertTrue(resolve_layer("scripts/check_exception_policy.py"))


class ProhibitionTests(unittest.TestCase):
    """Security scopes propagate or use a narrowly permitted observable boundary."""

    def test_prohibited_paths_are_detected(self):
        cases = {
            "itambox/core/crypto.py": "crypto",
            "itambox/core/management/commands/rotate_encryption_keys.py": "crypto",
            "itambox/core/auth/ldap.py": "authentication",
            "itambox/itambox/api/authentication.py": "authentication",
            "itambox/itambox/api/permissions.py": "authorization",
            "itambox/core/managers.py": "tenant-resolution",
            "itambox/itambox/middleware.py": "tenant-resolution",
            "itambox/core/settings/base.py": "config-load",
        }
        for path, domain in cases.items():
            with self.subTest(path=path):
                self.assertIn(domain, resolve_prohibited_domains(path, (), transactional=False))

    def test_prohibited_scope_names_apply_in_any_file(self):
        domains = resolve_prohibited_domains("itambox/extras/views.py", ("Report", "has_permission"), False)
        self.assertEqual(domains, ("authorization",))

    def test_ordinary_code_is_not_prohibited(self):
        self.assertEqual(resolve_prohibited_domains("itambox/assets/tables.py", ("render_cost",), False), ())

    def test_transactional_containment_is_a_prohibited_domain(self):
        self.assertEqual(resolve_prohibited_domains("itambox/assets/tables.py", ("x",), True), ("transactional",))

    def test_handler_inside_an_atomic_block_is_transactional(self):
        result = scan(
            """
            def work():
                with transaction.atomic():
                    try:
                        risky()
                    except Exception:
                        pass
            """,
            relative="itambox/assets/views.py",
        )
        self.assertEqual([entry.domains for entry in result.prohibited], [("transactional",)])

    def test_handler_wrapping_an_atomic_block_is_not_transactional(self):
        """Catching *around* a transaction runs after the rollback; that is correct."""
        result = scan(
            """
            def work():
                try:
                    with transaction.atomic():
                        risky()
                except Exception:
                    logger.exception('rolled back')
            """,
            relative="itambox/assets/views.py",
        )
        self.assertEqual(result.prohibited, [])

    def test_bare_atomic_import_form_is_recognised(self):
        result = scan(
            """
            def work():
                with atomic():
                    try:
                        risky()
                    except Exception:
                        pass
            """,
            relative="itambox/assets/views.py",
        )
        self.assertEqual([entry.domains for entry in result.prohibited], [("transactional",)])

    def test_imported_atomic_and_suppress_aliases_are_resolved(self):
        result = scan(
            """
            from contextlib import suppress as ignore
            from django.db.transaction import atomic as db_atomic

            def work():
                with db_atomic():
                    with ignore(Exception):
                        risky()
            """,
            relative="itambox/assets/views.py",
        )
        self.assertEqual(len(result.prohibited), 1)
        self.assertEqual(result.prohibited[0].classification, "pass-only")
        self.assertEqual(result.prohibited[0].domains, ("transactional",))

    def test_transaction_atomic_decorator_is_transactional(self):
        result = scan(
            """
            @transaction.atomic
            def work():
                try:
                    risky()
                except Exception:
                    pass
            """,
            relative="itambox/assets/views.py",
        )
        self.assertEqual([entry.domains for entry in result.prohibited], [("transactional",)])

    PROHIBITED_BODIES = {
        "cleanup-reraise": "self.restore()\nraise",
        "raise": "raise ValueError('converted')",
        "log-and-raise": "logger.exception('failed')\nraise ValueError('converted')",
        "log-only": "logger.exception('failed')",
        "silent": "return None",
        "pass-only": "pass",
    }

    def prohibited_for(self, body):
        """Build a handler in a prohibited path with exactly the given body.

        Assembled by explicit indentation rather than f-string interpolation:
        a multi-line body pasted into an indented template defeats
        ``textwrap.dedent`` and silently changes the shape under test.
        """
        source = (
            "def authenticate(request):\n"
            "    try:\n"
            "        return resolve(request)\n"
            "    except Exception:\n" + textwrap.indent(body, " " * 8) + "\n"
        )
        return scan(source, relative="itambox/core/auth/backend.py").prohibited

    def test_the_body_fixture_produces_the_shape_it_claims(self):
        """Guard the guard: a mis-indented fixture would fake every result here."""
        for classification, body in self.PROHIBITED_BODIES.items():
            with self.subTest(classification=classification):
                source = "try:\n    work()\nexcept Exception:\n" + textwrap.indent(body, "    ") + "\n"
                self.assertEqual(only_handler(source), classification)

    def test_every_shape_that_reaches_the_caller_is_admissible(self):
        """Catch/clean up/re-raise and typed failures both tell the caller."""
        for classification in PROPAGATING_CLASSIFICATIONS:
            with self.subTest(classification=classification):
                self.assertEqual(self.prohibited_for(self.PROHIBITED_BODIES[classification]), [])

    def test_every_shape_that_swallows_is_refused(self):
        """Logging is not propagating: the caller still proceeds as if it worked."""
        for classification in SWALLOWING_CLASSIFICATIONS:
            with self.subTest(classification=classification):
                violations = self.prohibited_for(self.PROHIBITED_BODIES[classification])
                self.assertEqual(len(violations), 1)
                self.assertEqual(violations[0].classification, classification)

    def test_an_annotation_does_not_unlock_a_prohibited_scope(self):
        result = scan(
            """
            def get_fernet():
                try:
                    return build()
                except Exception:  # broad except: availability-tradeoff: documented reason
                    return fallback()
            """,
            relative="itambox/core/crypto.py",
        )
        self.assertEqual(len(result.prohibited), 1)
        self.assertEqual(result.prohibited[0].domains, ("crypto",))

    def test_logged_availability_tradeoff_is_allowed_only_for_authentication(self):
        self.assertFalse(
            is_prohibited_violation(
                ("authentication",),
                "log-only",
                "availability-tradeoff",
            )
        )
        self.assertTrue(
            is_prohibited_violation(
                ("authorization",),
                "log-only",
                "availability-tradeoff",
            )
        )

    def test_logged_boundary_isolation_is_allowed_at_authentication_boundary(self):
        result = scan(
            """
            def authenticate(request):
                try:
                    provider_call()
                # broad except: boundary-isolation: provider exceptions are external
                except Exception:
                    logger.warning('provider failed')
            """,
            relative="itambox/core/auth/backend.py",
        )
        self.assertEqual(result.prohibited, [])
        self.assertEqual(dict(result.annotated), {"boundary-isolation": 1})

    def test_task_isolation_requires_logging_inside_a_transaction(self):
        logged = scan(
            """
            def work():
                with transaction.atomic():
                    try:
                        mutate_one_item()
                    # broad except: task-isolation: one item must not abort the batch
                    except Exception:
                        logger.exception('item failed')
            """,
            relative="itambox/assets/views.py",
        )
        silent = scan(
            """
            def work():
                with transaction.atomic():
                    try:
                        mutate_one_item()
                    # broad except: task-isolation: one item must not abort the batch
                    except Exception:
                        pass
            """,
            relative="itambox/assets/views.py",
        )
        self.assertEqual(logged.prohibited, [])
        self.assertEqual(len(silent.prohibited), 1)

    def test_logged_boundary_isolation_is_allowed_inside_a_transaction(self):
        self.assertFalse(
            is_prohibited_violation(
                ("transactional",),
                "log-only",
                "boundary-isolation",
            )
        )

    def test_wrong_category_does_not_unlock_a_security_domain(self):
        self.assertTrue(
            is_prohibited_violation(
                ("authentication",),
                "log-only",
                "render-degrade",
            )
        )

    def test_narrow_handlers_that_act_are_not_prohibited(self):
        """Naming what you handle is the point; only broad or pass-only is caught."""
        result = scan(
            """
            class Middleware:
                def process_request(self, request):
                    try:
                        return Tenant.objects.get(pk=1)
                    except Tenant.DoesNotExist:
                        request.session.pop('active_tenant_id', None)
            """,
            relative="itambox/itambox/middleware.py",
        )
        self.assertEqual(result.prohibited, [])

    def test_narrow_pass_only_handlers_are_prohibited(self):
        result = scan(
            """
            class QuerySet:
                def filter_by_tenant(self):
                    try:
                        probe()
                    except FieldDoesNotExist:
                        pass
            """,
            relative="itambox/core/managers.py",
        )
        self.assertEqual(len(result.prohibited), 1)
        self.assertIn("tenant-resolution", result.prohibited[0].domains)

    def test_a_prohibited_violation_reports_its_line_for_the_reviewer(self):
        result = scan(
            """
            def get_fernet():
                try:
                    return build()
                except Exception:
                    pass
            """,
            relative="itambox/core/crypto.py",
        )
        self.assertEqual(result.prohibited[0].line, 4)


class IdentityTests(unittest.TestCase):
    """Identity validation keeps unusable rows out of the baseline."""

    def test_identity_is_ordered_and_hashable(self):
        first = HandlerIdentity("a.py", "FunctionDef:f", "Exception", "silent", BODY_A)
        second = HandlerIdentity("b.py", "FunctionDef:f", "Exception", "silent", BODY_A)
        self.assertLess(first, second)
        self.assertEqual(len({first, first}), 1)

    def test_absolute_and_windows_paths_are_rejected(self):
        for path in ("/itambox/core/x.py", "itambox\\core\\x.py", "C:/itambox/x.py"):
            with self.subTest(path=path), self.assertRaises(IdentityError):
                HandlerIdentity(path, "", "Exception", "silent", BODY_A)

    def test_paths_may_not_traverse_outside_the_repository(self):
        with self.assertRaises(IdentityError):
            HandlerIdentity("itambox/../../etc/passwd.py", "", "Exception", "silent", BODY_A)

    def test_line_numbers_may_not_leak_into_an_identity(self):
        with self.assertRaises(IdentityError):
            HandlerIdentity("itambox/core/x.py:42", "", "Exception", "silent", BODY_A)

    def test_unknown_classification_is_rejected(self):
        with self.assertRaises(IdentityError):
            HandlerIdentity("itambox/core/x.py", "", "Exception", "mostly-fine", BODY_A)

    def test_invalid_body_digest_is_rejected(self):
        with self.assertRaises(IdentityError):
            HandlerIdentity("itambox/core/x.py", "", "Exception", "silent", "not-a-digest")


class BaselineTests(unittest.TestCase):
    """The baseline is a reviewed record, not a cache."""

    def setUp(self):
        self.fingerprint = compute_policy_fingerprint(["itambox"])
        self.findings = {
            ("itambox/core/a.py", "FunctionDef:f", "Exception", "silent", BODY_A): 2,
            ("itambox/core/b.py", "", "Exception", "pass-only", BODY_B): 1,
        }

    def round_trip(self, findings):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "baseline.json"
            with redirect_stdout(io.StringIO()):
                write_baseline(findings, path, self.fingerprint)
            raw = json.loads(path.read_text(encoding="utf-8"))
            return raw, load_baseline(path, self.fingerprint)

    def test_round_trip_is_stable_and_sorted(self):
        raw, loaded = self.round_trip(self.findings)
        self.assertEqual(dict(loaded), self.findings)
        self.assertEqual(raw["schema_version"], SCHEMA_VERSION)
        self.assertEqual(raw["canonical_python"], f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}")
        self.assertEqual(raw["policy_sha256"], self.fingerprint)
        identities = [
            (row["path"], row["scope"], row["handler_type"], row["classification"], row["body_sha256"])
            for row in raw["findings"]
        ]
        self.assertEqual(identities, sorted(identities))

    def test_baseline_is_bound_to_the_policy_fingerprint(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "baseline.json"
            write_baseline(self.findings, path, self.fingerprint)
            with self.assertRaises(PolicyError):
                load_baseline(path, "0" * 64)

    def test_policy_fingerprint_tracks_the_effective_policy(self):
        self.assertNotEqual(
            compute_policy_fingerprint(["itambox"]),
            compute_policy_fingerprint(["itambox", "scripts"]),
        )

    def test_malformed_baselines_are_rejected(self):
        cases = {
            "not a mapping": [],
            "missing field": {"schema_version": SCHEMA_VERSION, "findings": []},
            "wrong schema": {
                "schema_version": SCHEMA_VERSION + 1,
                "canonical_python": "3.12",
                "policy_sha256": self.fingerprint,
                "findings": [],
            },
            "wrong python": {
                "schema_version": SCHEMA_VERSION,
                "canonical_python": "3.11",
                "policy_sha256": self.fingerprint,
                "findings": [],
            },
            "findings not a list": {
                "schema_version": SCHEMA_VERSION,
                "canonical_python": "3.12",
                "policy_sha256": self.fingerprint,
                "findings": {},
            },
            "row missing field": {
                "schema_version": SCHEMA_VERSION,
                "canonical_python": "3.12",
                "policy_sha256": self.fingerprint,
                "findings": [{"path": "a.py", "scope": "", "handler_type": "Exception"}],
            },
            "non positive count": {
                "schema_version": SCHEMA_VERSION,
                "canonical_python": "3.12",
                "policy_sha256": self.fingerprint,
                "findings": [
                    {
                        "path": "a.py",
                        "scope": "",
                        "handler_type": "Exception",
                        "classification": "silent",
                        "count": 0,
                    }
                ],
            },
            "unsorted rows": {
                "schema_version": SCHEMA_VERSION,
                "canonical_python": "3.12",
                "policy_sha256": self.fingerprint,
                "findings": [
                    {
                        "path": "b.py",
                        "scope": "",
                        "handler_type": "Exception",
                        "classification": "silent",
                        "count": 1,
                    },
                    {
                        "path": "a.py",
                        "scope": "",
                        "handler_type": "Exception",
                        "classification": "silent",
                        "count": 1,
                    },
                ],
            },
        }
        for label, payload in cases.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as temporary_directory:
                path = Path(temporary_directory) / "baseline.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(PolicyError):
                    load_baseline(path, self.fingerprint)

    def test_new_identity_is_a_regression_and_removed_identity_is_stale(self):
        baseline = dict(self.findings)
        current = dict(self.findings)
        current.pop(("itambox/core/b.py", "", "Exception", "pass-only", BODY_B))
        current[("itambox/core/c.py", "", "Exception", "silent", BODY_C)] = 1
        regressions, stale = compare_baseline(current, baseline)
        self.assertEqual(
            list(regressions),
            [("itambox/core/c.py", "", "Exception", "silent", BODY_C)],
        )
        self.assertEqual(
            list(stale),
            [("itambox/core/b.py", "", "Exception", "pass-only", BODY_B)],
        )

    def test_changing_only_the_classification_is_a_regression(self):
        """A handler that stops logging must not pass because the count held."""
        baseline = {("itambox/core/a.py", "FunctionDef:f", "Exception", "log-only", BODY_A): 1}
        current = {("itambox/core/a.py", "FunctionDef:f", "Exception", "pass-only", BODY_A): 1}
        regressions, stale = compare_baseline(current, baseline)
        self.assertEqual(list(regressions), [("itambox/core/a.py", "FunctionDef:f", "Exception", "pass-only", BODY_A)])
        self.assertEqual(list(stale), [("itambox/core/a.py", "FunctionDef:f", "Exception", "log-only", BODY_A)])

    def test_changing_only_the_handler_body_is_a_regression(self):
        baseline = {("itambox/core/a.py", "FunctionDef:f", "Exception", "silent", BODY_A): 1}
        current = {("itambox/core/a.py", "FunctionDef:f", "Exception", "silent", BODY_B): 1}
        regressions, stale = compare_baseline(current, baseline)
        self.assertEqual(list(regressions), [("itambox/core/a.py", "FunctionDef:f", "Exception", "silent", BODY_B)])
        self.assertEqual(list(stale), [("itambox/core/a.py", "FunctionDef:f", "Exception", "silent", BODY_A)])


class RenamePairingTests(unittest.TestCase):
    """Whole-file refactors must read as moves, not as new debt."""

    def test_a_pure_move_is_paired(self):
        regressions = {("itambox/core/new.py", "FunctionDef:f", "Exception", "silent", BODY_A): 1}
        stale = {("itambox/core/old.py", "FunctionDef:f", "Exception", "silent", BODY_A): 1}
        moves, remaining_regressions, remaining_stale = pair_moved_identities(regressions, stale)
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0][0][0], "itambox/core/old.py")
        self.assertEqual(moves[0][1][0], "itambox/core/new.py")
        self.assertEqual(remaining_regressions, {})
        self.assertEqual(remaining_stale, {})

    def test_a_renamed_scope_is_paired(self):
        regressions = {("itambox/core/a.py", "FunctionDef:new_name", "Exception", "silent", BODY_A): 1}
        stale = {("itambox/core/a.py", "FunctionDef:old_name", "Exception", "silent", BODY_A): 1}
        moves, remaining_regressions, remaining_stale = pair_moved_identities(regressions, stale)
        self.assertEqual(len(moves), 1)
        self.assertEqual(remaining_regressions, {})

    def test_a_changed_classification_is_never_paired_as_a_move(self):
        regressions = {("itambox/core/new.py", "FunctionDef:f", "Exception", "pass-only", BODY_A): 1}
        stale = {("itambox/core/old.py", "FunctionDef:f", "Exception", "log-only", BODY_A): 1}
        moves, remaining_regressions, remaining_stale = pair_moved_identities(regressions, stale)
        self.assertEqual(moves, [])
        self.assertEqual(len(remaining_regressions), 1)
        self.assertEqual(len(remaining_stale), 1)

    def test_a_changed_handler_type_is_never_paired_as_a_move(self):
        regressions = {("itambox/core/new.py", "FunctionDef:f", "BaseException", "silent", BODY_A): 1}
        stale = {("itambox/core/old.py", "FunctionDef:f", "Exception", "silent", BODY_A): 1}
        moves, remaining_regressions, remaining_stale = pair_moved_identities(regressions, stale)
        self.assertEqual(moves, [])

    def test_counts_are_paired_only_up_to_the_smaller_side(self):
        """Two handlers moved and one added is a move plus real new debt."""
        regressions = {("itambox/core/new.py", "FunctionDef:f", "Exception", "silent", BODY_A): 3}
        stale = {("itambox/core/old.py", "FunctionDef:f", "Exception", "silent", BODY_A): 2}
        moves, remaining_regressions, remaining_stale = pair_moved_identities(regressions, stale)
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0][2], 2)
        self.assertEqual(
            remaining_regressions,
            {("itambox/core/new.py", "FunctionDef:f", "Exception", "silent", BODY_A): 1},
        )
        self.assertEqual(remaining_stale, {})


class CommandLineTests(unittest.TestCase):
    """End-to-end behaviour of the gate as CI invokes it."""

    CLEAN = """
        def work():
            try:
                risky()
            except ValueError:
                logger.warning('bad')
        """

    DEBT = """
        def work():
            try:
                risky()
            except Exception:
                pass
        """

    def run_gate(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue() + err.getvalue()

    def bootstrap(self, root, body, relative="itambox/core/sample.py"):
        write(root, relative, body)
        baseline = root / "baseline.json"
        code, output = self.run_gate(["itambox", "--cwd", str(root), "--baseline", str(baseline), "--write-baseline"])
        return baseline, code, output

    @staticmethod
    def write_v1_baseline(path, findings):
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "canonical_python": "3.12",
                    "policy_sha256": "0" * 64,
                    "findings": findings,
                }
            ),
            encoding="utf-8",
        )

    def test_v1_baseline_migration_preserves_the_no_growth_check(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            write(root, "itambox/core/sample.py", self.DEBT)
            baseline = root / "baseline.json"
            self.write_v1_baseline(
                baseline,
                [
                    {
                        "path": "itambox/core/sample.py",
                        "scope": "FunctionDef:work",
                        "handler_type": "Exception",
                        "classification": "pass-only",
                        "count": 1,
                    }
                ],
            )

            code, output = self.run_gate(
                ["itambox", "--cwd", str(root), "--baseline", str(baseline), "--write-baseline"]
            )
            self.assertEqual(code, 0, output)
            migrated = json.loads(baseline.read_text(encoding="utf-8"))
            self.assertEqual(migrated["schema_version"], SCHEMA_VERSION)
            self.assertRegex(migrated["findings"][0]["body_sha256"], r"^[0-9a-f]{64}$")

    def test_v1_baseline_migration_refuses_new_coarse_identity(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            write(root, "itambox/core/sample.py", self.DEBT)
            baseline = root / "baseline.json"
            self.write_v1_baseline(baseline, [])

            code, output = self.run_gate(
                ["itambox", "--cwd", str(root), "--baseline", str(baseline), "--write-baseline"]
            )
            self.assertEqual(code, 1)
            self.assertIn("new broad or pass-only handler", output)
            self.assertEqual(json.loads(baseline.read_text(encoding="utf-8"))["schema_version"], 1)

    def test_clean_tree_passes_and_new_debt_regresses(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            baseline, code, _ = self.bootstrap(root, self.CLEAN)
            self.assertEqual(code, 0)

            code, output = self.run_gate(["itambox", "--cwd", str(root), "--baseline", str(baseline)])
            self.assertEqual(code, 0, output)

            write(root, "itambox/core/sample.py", self.DEBT)
            code, output = self.run_gate(["itambox", "--cwd", str(root), "--baseline", str(baseline)])
            self.assertEqual(code, 1)
            self.assertIn("new broad or pass-only handler", output)

    def test_removed_debt_makes_the_baseline_stale(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            baseline, _, _ = self.bootstrap(root, self.DEBT)

            write(root, "itambox/core/sample.py", self.CLEAN)
            code, output = self.run_gate(["itambox", "--cwd", str(root), "--baseline", str(baseline)])
            self.assertEqual(code, 1)
            self.assertIn("stale", output)

    def test_annotating_a_handler_removes_it_from_the_ratchet(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            baseline, _, _ = self.bootstrap(root, self.DEBT)

            write(
                root,
                "itambox/core/sample.py",
                """
                def work():
                    try:
                        risky()
                    except Exception:  # broad except: task-isolation: documented reason
                        pass
                """,
            )
            code, output = self.run_gate(["itambox", "--cwd", str(root), "--baseline", str(baseline)])
            self.assertEqual(code, 1)
            self.assertIn("stale", output)

            code, output = self.run_gate(
                ["itambox", "--cwd", str(root), "--baseline", str(baseline), "--write-baseline"]
            )
            self.assertEqual(code, 0, output)
            code, output = self.run_gate(["itambox", "--cwd", str(root), "--baseline", str(baseline)])
            self.assertEqual(code, 0, output)

    def test_write_baseline_refuses_to_grandfather_new_debt(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            baseline, _, _ = self.bootstrap(root, self.CLEAN)

            write(root, "itambox/core/other.py", self.DEBT)
            code, output = self.run_gate(
                ["itambox", "--cwd", str(root), "--baseline", str(baseline), "--write-baseline"]
            )
            self.assertEqual(code, 1)
            self.assertIn("new broad or pass-only handler", output)

    def test_malformed_annotation_fails_and_blocks_baseline_writes(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            write(
                root,
                "itambox/core/sample.py",
                """
                def work():
                    try:
                        risky()
                    except Exception:  # broad except: nonsense: no such category
                        pass
                """,
            )
            baseline = root / "baseline.json"
            code, output = self.run_gate(
                ["itambox", "--cwd", str(root), "--baseline", str(baseline), "--write-baseline"]
            )
            self.assertEqual(code, 1)
            self.assertIn("nonsense", output)
            self.assertFalse(baseline.exists())

    def test_a_prohibited_violation_fails_even_when_recorded_in_the_baseline(self):
        """The whole point: a baseline entry is not permission."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            baseline, code, output = self.bootstrap(
                root,
                """
                def get_fernet():
                    try:
                        return build()
                    except Exception:
                        pass
                """,
                relative="itambox/core/crypto.py",
            )
            self.assertEqual(code, 1, output)
            self.assertIn("prohibited", output.lower())
            self.assertTrue(baseline.exists(), "the ratchet is still recorded for review")

            code, output = self.run_gate(["itambox", "--cwd", str(root), "--baseline", str(baseline)])
            self.assertEqual(code, 1)
            self.assertIn("crypto", output)
            for classification in PROPAGATING_CLASSIFICATIONS:
                self.assertIn(classification, output, "the report must name the admissible shapes")

    def test_a_prohibited_scope_passes_once_the_handler_re_raises(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            baseline, code, output = self.bootstrap(
                root,
                """
                def get_fernet():
                    try:
                        return build()
                    except Exception:
                        cleanup()
                        raise
                """,
                relative="itambox/core/crypto.py",
            )
            self.assertEqual(code, 0, output)
            code, output = self.run_gate(["itambox", "--cwd", str(root), "--baseline", str(baseline)])
            self.assertEqual(code, 0, output)

    def _rewrite_fingerprint(self, baseline, value="0" * 64):
        raw = json.loads(baseline.read_text(encoding="utf-8"))
        raw["policy_sha256"] = value
        baseline.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")

    def test_a_stale_policy_fingerprint_fails_a_read_only_run(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            baseline, _, _ = self.bootstrap(root, self.DEBT)
            self._rewrite_fingerprint(baseline)

            code, output = self.run_gate(["itambox", "--cwd", str(root), "--baseline", str(baseline)])
            self.assertEqual(code, 2)
            self.assertIn("policy_sha256", output)

    def test_regenerating_after_a_policy_change_still_refuses_new_debt(self):
        """A policy change must not become an amnesty for debt added beside it."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            baseline, _, _ = self.bootstrap(root, self.DEBT)
            self._rewrite_fingerprint(baseline)
            write(root, "itambox/core/extra.py", self.DEBT)

            code, output = self.run_gate(
                ["itambox", "--cwd", str(root), "--baseline", str(baseline), "--write-baseline"]
            )
            self.assertEqual(code, 1)
            self.assertIn("new broad or pass-only handler", output)
            self.assertIn("itambox/core/extra.py", output)

    def test_regenerating_after_a_policy_change_succeeds_when_nothing_was_added(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            baseline, _, _ = self.bootstrap(root, self.DEBT)
            self._rewrite_fingerprint(baseline)

            code, output = self.run_gate(
                ["itambox", "--cwd", str(root), "--baseline", str(baseline), "--write-baseline"]
            )
            self.assertEqual(code, 0, output)
            self.assertIn("policy fingerprint changed", output)

            code, output = self.run_gate(["itambox", "--cwd", str(root), "--baseline", str(baseline)])
            self.assertEqual(code, 0, output)

    def test_a_structurally_broken_baseline_is_never_silently_regenerated(self):
        """Only the fingerprint is tolerated in write mode; malformed rows are not."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            baseline, _, _ = self.bootstrap(root, self.DEBT)
            baseline.write_text('{"schema_version": 1}', encoding="utf-8")

            code, output = self.run_gate(
                ["itambox", "--cwd", str(root), "--baseline", str(baseline), "--write-baseline"]
            )
            self.assertEqual(code, 2)

    def test_a_move_is_reported_as_a_move(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            baseline, _, _ = self.bootstrap(root, self.DEBT)

            (root / "itambox/core/sample.py").unlink()
            write(root, "itambox/core/moved.py", self.DEBT)
            code, output = self.run_gate(["itambox", "--cwd", str(root), "--baseline", str(baseline)])
            self.assertEqual(code, 1, "a move still needs a reviewed baseline update")
            self.assertIn("moved:", output)
            self.assertIn("itambox/core/moved.py", output)

    def test_a_move_into_a_prohibited_scope_is_not_excused(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            baseline, _, _ = self.bootstrap(root, self.DEBT, relative="itambox/assets/tables.py")

            (root / "itambox/assets/tables.py").unlink()
            write(root, "itambox/core/crypto.py", self.DEBT)
            code, output = self.run_gate(["itambox", "--cwd", str(root), "--baseline", str(baseline)])
            self.assertEqual(code, 1)
            self.assertIn("crypto", output)

    def test_summary_reports_the_layer_breakdown(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            baseline, _, _ = self.bootstrap(root, self.DEBT, relative="itambox/assets/tables.py")
            code, output = self.run_gate(["itambox", "--cwd", str(root), "--baseline", str(baseline)])
            self.assertEqual(code, 0, output)
            self.assertIn("presentation", output)


class BareHandlerCrossCheckTests(unittest.TestCase):
    """E722 owns bare handlers; this gate only cross-checks that it still does."""

    def test_a_bare_handler_is_reported_separately_from_the_ratchet(self):
        result = scan(
            """
            def work():
                try:
                    risky()
                except:
                    pass
            """
        )
        self.assertEqual(len(result.bare), 1)
        self.assertEqual(result.bare[0].path, "itambox/core/sample.py")

    def test_the_repository_has_no_bare_handlers(self):
        """Flake8 E722 is the gate for these; this asserts the division holds."""
        result = collect_handlers(REPOSITORY_ROOT, ["itambox", "scripts"])
        self.assertEqual(
            [entry.path for entry in result.bare],
            [],
            "bare handlers must be caught by flake8 E722 before reaching this gate",
        )


class RepositoryPolicyTests(unittest.TestCase):
    """The checked-in baseline describes this repository, not a past one."""

    def test_checked_in_baseline_matches_the_repository(self):
        from scripts.check_exception_policy import BASELINE_PATH, DEFAULT_TARGETS

        fingerprint = compute_policy_fingerprint(DEFAULT_TARGETS)
        baseline = load_baseline(BASELINE_PATH, fingerprint)
        result = collect_handlers(REPOSITORY_ROOT, list(DEFAULT_TARGETS))
        regressions, stale = compare_baseline(result.findings, baseline)
        self.assertEqual(dict(regressions), {}, "new unannotated handlers are not recorded in the baseline")
        self.assertEqual(dict(stale), {}, "the baseline records handlers that no longer exist")

    def test_no_malformed_annotations_in_the_repository(self):
        result = collect_handlers(REPOSITORY_ROOT, ["itambox", "scripts"])
        self.assertEqual([entry.problem for entry in result.malformed], [])


if __name__ == "__main__":
    unittest.main()
