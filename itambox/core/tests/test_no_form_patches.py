"""Regression check: no runtime replacement of Django form internals (#584, WP5).

Form behavior (tenant-scoped choices, tenant requiredness, ``data-tom-select``)
is owned by explicit abstractions in ``core.forms``. This check scans the
production source with ``ast`` and fails when code assigns to, or deletes, an
attribute of a Django form class or field class (``ModelChoiceField.queryset =
...``, ``BaseForm.__init__ = ...``, ``setattr(forms.ModelChoiceField, ...)``),
so a global patch cannot be reintroduced. It touches no database.
"""

import ast
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]
SKIPPED_PARTS = {"tests", "migrations", "node_modules", "dist", "docs"}
FORM_BASE_NAMES = {"BaseForm", "Form", "ModelForm", "BaseModelForm", "Field", "Widget", "Select", "SelectMultiple"}
FORM_NAME_SUFFIXES = ("Field", "Widget", "Select", "SelectMultiple")


def _is_form_class_name(name):
    return name in FORM_BASE_NAMES or name.endswith(FORM_NAME_SUFFIXES)


def _owner_leaf(owner):
    if isinstance(owner, ast.Attribute):
        return owner.attr
    return getattr(owner, "id", None)


def _is_form_attribute(target):
    """``<FormOrFieldClass>.<attr>``, also through a module path."""
    if not isinstance(target, ast.Attribute):
        return False
    leaf = _owner_leaf(target.value)
    return leaf is not None and _is_form_class_name(leaf)


def _assignment_targets(node):
    if isinstance(node, (ast.Assign, ast.Delete)):
        return node.targets
    if isinstance(node, (ast.AugAssign, ast.AnnAssign)):
        return [node.target]
    return []


def _setattr_patch(node):
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
        return None
    if node.func.id not in {"setattr", "delattr"} or not node.args:
        return None
    leaf = _owner_leaf(node.args[0])
    if leaf is not None and _is_form_class_name(leaf):
        return f"{node.func.id} on {ast.unparse(node.args[0])}"
    return None


def find_form_patches(source, filename="<source>"):
    """Return ``(line, description)`` for every runtime replacement of form internals."""
    patches = []
    # source-text: the guard asserts on production source structure by design
    for node in ast.walk(ast.parse(source, filename)):
        for target in _assignment_targets(node):
            if _is_form_attribute(target):
                patches.append((node.lineno, f"assignment to {ast.unparse(target)}"))
        description = _setattr_patch(node)
        if description:
            patches.append((node.lineno, description))
    return patches


def _production_sources():
    for path in sorted(ROOT.rglob("*.py")):
        relative = path.relative_to(ROOT)
        if SKIPPED_PARTS.intersection(relative.parts) or relative.parts[0] == "static":
            continue
        yield relative, path


class FormInternalsAreNotPatchedTests(SimpleTestCase):
    def test_production_code_installs_no_form_patch(self):
        offenders = []
        for relative, path in _production_sources():
            for line, description in find_form_patches(path.read_text(encoding="utf-8"), str(relative)):
                offenders.append(f"{relative.as_posix()}:{line}: {description}")
        self.assertEqual(
            offenders,
            [],
            "Django form internals must not be replaced at runtime; use the explicit "
            "abstractions in core.forms (TenantScopedFormMixin, TenantScopedModelChoiceField).",
        )

    def test_core_apps_imports_no_form_module(self):
        # source-text: the guard asserts on production source structure by design
        tree = ast.parse((ROOT / "core" / "apps.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        self.assertEqual({name for name in imported if name.startswith("django.forms")}, set())

    def test_detector_flags_the_removed_patches(self):
        sources = (
            "from django.forms.models import ModelChoiceField\nModelChoiceField.queryset = property(lambda s: None)\n",
            "from django.forms.forms import BaseForm\nBaseForm.__init__ = lambda self: None\n",
            "import django\ndjango.forms.models.ModelChoiceField.queryset = None\n",
            "from django import forms\nsetattr(forms.ModelChoiceField, 'queryset', None)\n",
            "from django import forms\ndel forms.BaseForm.__init__\n",
        )
        for source in sources:
            self.assertTrue(find_form_patches(source), source)

    def test_detector_ignores_ordinary_form_code(self):
        source = (
            "from django import forms\n"
            "class F(forms.Form):\n"
            "    a = forms.CharField()\n"
            "    def __init__(self, *args, **kwargs):\n"
            "        super().__init__(*args, **kwargs)\n"
            "        self.fields['a'].required = False\n"
            "        self.fields['a'].widget.attrs['x'] = 1\n"
            "        self.helper = None\n"
            "        self.fields['a'].widget = forms.Select()\n"
        )
        self.assertEqual(find_form_patches(source), [])
