"""
scripts/check_flake8_baseline.py is the blocking-lint gate (see .github/workflows/ci.yml
"lint" job and .pre-commit-config.yaml): it runs the exact pinned Flake8 toolchain and
requires every path/code/message/source/AST-context identity to match the policy-bound
schema-v3 baseline. New identities are regressions; removed identities require updating
the baseline in the same cleanup, preventing fixed debt from becoming stale headroom.

These tests exercise the gate script itself, end-to-end, against throwaway fixture
projects (never the real 4k-violation baseline) so they stay fast and are not coupled to
ITAMbox's own lint debt shrinking or growing over time. This is the "newly introduced
blocking violation causes nonzero exit" proof required by issue #15.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "check_flake8_baseline.py"

# Deliberately outside flake8's ignore list (E203, E501, W503) and cheap to trigger:
# an unused import (F401) on line 1.
CLEAN_MODULE = "def add(a, b):\n    return a + b\n"
VIOLATING_MODULE = "import os\n\n\ndef add(a, b):\n    return a + b\n"


def _finding(
    *,
    path="pkg/mod.py",
    code="F401",
    message="'os' imported but unused",
    source="import os",
    context="Module:body",
    count=1,
):
    return {
        "path": path,
        "code": code,
        "message": message,
        "source": source,
        "context": context,
        "count": count,
    }


def _baseline(*findings):
    return {
        "schema_version": 3,
        "canonical_python": "3.12",
        "findings": list(findings),
    }


def _with_policy(root, baseline, targets=("pkg",)):
    baseline = dict(baseline)
    baseline["policy_sha256"] = _load_script(
        "check_flake8_baseline_fixture_policy"
    ).compute_policy_fingerprint(root, list(targets))
    return baseline


def _write_fixture_policy(root):
    (root / "setup.cfg").write_text(
        "[flake8]\nselect = B,C,E,F,W,B9\nignore = E203, E501, W503\n",
        encoding="utf-8",
    )


@pytest.fixture
def fixture_project(tmp_path):
    """A throwaway one-file "project" with its own flake8 config, isolated from the
    real repo's setup.cfg and scripts/flake8_baseline.json."""
    _write_fixture_policy(tmp_path)
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    module = pkg / "mod.py"
    module.write_text(CLEAN_MODULE, encoding="utf-8")
    return tmp_path, module


def _run(fixture_project, baseline):
    root, _module = fixture_project
    baseline_path = root / "flake8_baseline.json"
    baseline = _with_policy(root, baseline)
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "pkg", "--baseline", str(baseline_path), "--cwd", str(root)],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return result


def test_clean_project_passes_with_empty_baseline(fixture_project):
    result = _run(fixture_project, baseline=_baseline())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "match the identity baseline" in result.stdout


def test_new_violation_in_untracked_file_fails(fixture_project):
    root, module = fixture_project
    module.write_text(VIOLATING_MODULE, encoding="utf-8")
    result = _run(fixture_project, baseline=_baseline())
    assert result.returncode != 0, result.stdout + result.stderr
    assert "pkg/mod.py: F401 count 0 -> 1" in result.stdout


def test_violation_count_within_existing_baseline_passes(fixture_project):
    root, module = fixture_project
    module.write_text(VIOLATING_MODULE, encoding="utf-8")
    result = _run(fixture_project, baseline=_baseline(_finding()))
    assert result.returncode == 0, result.stdout + result.stderr


def test_unchanged_finding_identity_survives_line_shift(fixture_project):
    root, module = fixture_project
    module.write_text(
        "# unrelated line inserted above existing debt\n" + VIOLATING_MODULE,
        encoding="utf-8",
    )
    result = _run(fixture_project, baseline=_baseline(_finding()))
    assert result.returncode == 0, result.stdout + result.stderr


def test_same_source_finding_moved_between_named_scopes_is_new_identity(tmp_path):
    gate = _load_script("check_flake8_baseline_scope_identity")
    module = tmp_path / "mod.py"
    message = "'os' imported but unused"
    module.write_text(
        "def first():\n    import os\n\ndef second():\n    pass\n",
        encoding="utf-8",
    )
    old, _examples = gate.parse_findings(
        f"mod.py:2:5: F401 {message}\n",
        tmp_path,
    )
    module.write_text(
        "def first():\n    pass\n\ndef second():\n    import os\n",
        encoding="utf-8",
    )
    new, _examples = gate.parse_findings(
        f"mod.py:5:5: F401 {message}\n",
        tmp_path,
    )

    assert old != new
    assert old - new == old
    assert new - old == new


def test_same_finding_moved_between_identical_sibling_if_blocks_is_new_identity(
    tmp_path,
):
    gate = _load_script("check_flake8_baseline_sibling_if_identity")
    module = tmp_path / "mod.py"
    message = "'os' imported but unused"
    module.write_text(
        "def f(flag):\n    if flag:\n        import os\n    if flag:\n        pass\n",
        encoding="utf-8",
    )
    old, _examples = gate.parse_findings(
        f"mod.py:3:9: F401 {message}\n",
        tmp_path,
    )
    module.write_text(
        "def f(flag):\n    if flag:\n        pass\n    if flag:\n        import os\n",
        encoding="utf-8",
    )
    new, _examples = gate.parse_findings(
        f"mod.py:5:9: F401 {message}\n",
        tmp_path,
    )

    assert old != new
    assert old - new == old
    assert new - old == new


def test_finding_on_blank_line_uses_nearest_ast_context(tmp_path):
    gate = _load_script("check_flake8_baseline_blank_line_context")
    module = tmp_path / "mod.py"
    module.write_text("import os\n\ndef function():\n    pass\n", encoding="utf-8")

    findings, _examples = gate.parse_findings(
        "mod.py:2:1: E303 too many blank lines (2)\n",
        tmp_path,
    )

    assert len(findings) == 1
    assert next(iter(findings))[4] == "Module:body"


def test_finding_in_whitespace_only_module_uses_module_context(tmp_path):
    gate = _load_script("check_flake8_baseline_empty_module_context")
    module = tmp_path / "mod.py"
    module.write_text(" \n", encoding="utf-8")

    findings, _examples = gate.parse_findings(
        "mod.py:1:1: W293 blank line contains whitespace\n",
        tmp_path,
    )

    assert len(findings) == 1
    assert next(iter(findings))[4] == "Module:body"


def test_blank_line_finding_moved_between_functions_is_new_identity(tmp_path):
    gate = _load_script("check_flake8_baseline_blank_scope_identity")
    module = tmp_path / "mod.py"
    module.write_text(
        "def first():\n    value = 1\n    \n\ndef second():\n    value = 2\n",
        encoding="utf-8",
    )
    old, _examples = gate.parse_findings(
        "mod.py:3:1: W293 blank line contains whitespace\n",
        tmp_path,
    )
    module.write_text(
        "def first():\n    value = 1\n\ndef second():\n    value = 2\n    \n",
        encoding="utf-8",
    )
    new, _examples = gate.parse_findings(
        "mod.py:6:1: W293 blank line contains whitespace\n",
        tmp_path,
    )

    assert old != new


def test_b907_multiline_statement_anchor_is_stable_across_reported_rows(
    fixture_project,
):
    root, module = fixture_project
    module.write_text(
        "def render(checkin_url):\n"
        "    return (\n"
        "        '<a '\n"
        "        f'hx-get=\"{checkin_url}\"'\n"
        "    )\n",
        encoding="utf-8",
    )
    gate = _load_script("check_flake8_baseline_b907_anchor")
    message = (
        "'checkin_url' is manually surrounded by quotes, "
        "consider using the `!r` conversion flag."
    )
    row_two, _ = gate.parse_findings(
        f"pkg/mod.py:2:5: B907 {message}", root,
    )
    row_four, _ = gate.parse_findings(
        f"pkg/mod.py:4:9: B907 {message}", root,
    )
    assert row_two == row_four
    anchor = next(iter(row_two))[3]
    assert anchor.startswith("return (")
    assert "f'hx-get=\"{checkin_url}\"'" in anchor


def test_b907_url_anchor_is_stable_across_reported_rows(fixture_project):
    root, module = fixture_project
    module.write_text(
        "html = (\n"
        "    f'<a href=\"{url}\">link</a>'\n"
        ")\n",
        encoding="utf-8",
    )
    gate = _load_script("check_flake8_baseline_b907_rows")
    message = (
        "'url' is manually surrounded by quotes, consider using the `!r` "
        "conversion flag."
    )
    row_one, _ = gate.parse_findings(
        f"pkg/mod.py:1:1: B907 {message}\n",
        root,
    )
    row_two, _ = gate.parse_findings(
        f"pkg/mod.py:2:1: B907 {message}\n",
        root,
    )
    assert row_one == row_two


def test_python311_b907_statement_replacement_is_blocked(monkeypatch):
    gate = _load_script("check_flake8_baseline_b907_replacement")
    old = (
        "pkg/mod.py",
        "B907",
        "'url' is manually surrounded by quotes, consider using the `!r` conversion flag.",
        "html = f'<a href=\"{url}\">link</a>'",
        "Module:body",
    )
    new = (
        old[0],
        old[1],
        old[2],
        "html = f'<img src=\"{url}\">'",
        old[4],
    )
    monkeypatch.setattr(gate.sys, "version_info", (3, 11))

    regressions, stale = gate.compare_baseline(
        gate.collections.Counter({new: 1}),
        gate.collections.Counter({old: 1}),
    )

    assert regressions == gate.collections.Counter({new: 1})
    assert stale == gate.collections.Counter({old: 1})


def test_violation_count_exceeding_baseline_fails(fixture_project):
    root, module = fixture_project
    # Two unused imports: baseline only grandfathers one exact F401 identity.
    module.write_text("import os\nimport sys\n\n\ndef add(a, b):\n    return a + b\n", encoding="utf-8")
    result = _run(fixture_project, baseline=_baseline(_finding()))
    assert result.returncode != 0, result.stdout + result.stderr
    assert "pkg/mod.py: F401 count 1 -> 2" in result.stdout


def test_replacing_existing_finding_with_new_same_code_fails(fixture_project):
    """A count-neutral F401 replacement is still a newly introduced finding."""
    root, module = fixture_project
    module.write_text("import sys\n\n\ndef add(a, b):\n    return a + b\n", encoding="utf-8")
    result = _run(fixture_project, baseline=_baseline(_finding()))
    assert result.returncode != 0, result.stdout + result.stderr
    assert "new violation" in result.stdout


def test_reducing_violations_requires_baseline_update(fixture_project):
    """Cleanup cannot leave headroom that permits later reintroduction."""
    result = _run(fixture_project, baseline=_baseline(_finding(count=5)))
    assert result.returncode != 0, result.stdout + result.stderr
    assert "baseline is stale" in result.stdout
    assert "F401 count 5 -> 0" in result.stdout


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _baseline_for(root, *findings):
    return _with_policy(root, _baseline(*findings))


def test_write_baseline_refuses_new_identity(
    fixture_project, monkeypatch,
):
    root, _module = fixture_project
    baseline_path = root / "flake8_baseline.json"
    original = _baseline_for(root)
    baseline_path.write_text(json.dumps(original), encoding="utf-8")
    gate = _load_script("check_flake8_baseline_write_regression")
    monkeypatch.setattr(gate.sys, "version_info", (3, 12))
    monkeypatch.setattr(
        gate,
        "run_flake8",
        lambda targets, cwd: (
            "pkg/mod.py:1:1: F401 'os' imported but unused\n",
            "",
            1,
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "pkg",
            "--baseline",
            str(baseline_path),
            "--cwd",
            str(root),
            "--write-baseline",
        ],
    )

    assert gate.main() != 0
    assert json.loads(baseline_path.read_text(encoding="utf-8")) == original


def test_write_baseline_updates_after_cleanup(
    fixture_project, monkeypatch,
):
    root, _module = fixture_project
    baseline_path = root / "flake8_baseline.json"
    baseline_path.write_text(
        json.dumps(_baseline_for(root, _finding())),
        encoding="utf-8",
    )
    gate = _load_script("check_flake8_baseline_write_cleanup")
    monkeypatch.setattr(gate.sys, "version_info", (3, 12))
    monkeypatch.setattr(gate, "run_flake8", lambda targets, cwd: ("", "", 0))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "pkg",
            "--baseline",
            str(baseline_path),
            "--cwd",
            str(root),
            "--write-baseline",
        ],
    )

    assert gate.main() == 0
    assert json.loads(baseline_path.read_text(encoding="utf-8")) == _baseline_for(root)


def test_write_baseline_refuses_noncanonical_python(
    fixture_project, monkeypatch, capsys,
):
    root, _module = fixture_project
    baseline_path = root / "flake8_baseline.json"
    baseline_path.write_text(json.dumps(_baseline_for(root)), encoding="utf-8")
    gate = _load_script("check_flake8_baseline_write_python311")
    monkeypatch.setattr(gate.sys, "version_info", (3, 11))
    monkeypatch.setattr(gate, "run_flake8", lambda targets, cwd: ("", "", 0))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "pkg",
            "--baseline",
            str(baseline_path),
            "--cwd",
            str(root),
            "--write-baseline",
        ],
    )

    assert gate.main() == 2
    assert "outside Python 3.12" in capsys.readouterr().err


def test_write_baseline_refuses_weakened_flake8_policy(
    fixture_project, monkeypatch, capsys,
):
    root, _module = fixture_project
    baseline_path = root / "flake8_baseline.json"
    original = _baseline_for(root, _finding())
    baseline_path.write_text(json.dumps(original), encoding="utf-8")
    (root / "setup.cfg").write_text(
        "[flake8]\nselect = NOT_A_CODE\n",
        encoding="utf-8",
    )
    gate = _load_script("check_flake8_baseline_weakened_policy")
    monkeypatch.setattr(gate.sys, "version_info", (3, 12))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "pkg",
            "--baseline",
            str(baseline_path),
            "--cwd",
            str(root),
            "--write-baseline",
        ],
    )

    assert gate.main() == 2
    assert "policy" in capsys.readouterr().err.lower()
    assert json.loads(baseline_path.read_text(encoding="utf-8")) == original


def test_python311_exact_fstring_shortfall_is_interpreter_version_aware(monkeypatch):
    gate = _load_script("check_flake8_baseline_python_compat")
    baseline = gate.PYTHON311_FSTRING_SHORTFALL.copy()
    assert len(baseline) == 7
    assert sum(baseline.values()) == 10

    monkeypatch.setattr(gate.sys, "version_info", (3, 11))
    regressions, stale = gate.compare_baseline(gate.collections.Counter(), baseline)
    assert not regressions
    assert not stale

    monkeypatch.setattr(gate.sys, "version_info", (3, 12))
    regressions, stale = gate.compare_baseline(gate.collections.Counter(), baseline)
    assert not regressions
    assert stale == baseline

    monkeypatch.setattr(gate.sys, "version_info", (3, 10))
    regressions, stale = gate.compare_baseline(gate.collections.Counter(), baseline)
    assert not regressions
    assert stale == baseline


def test_python311_does_not_hide_unlisted_identity_with_allowed_path_and_code(
    monkeypatch,
):
    gate = _load_script("check_flake8_baseline_python_compat_unlisted")
    unlisted_key = (
        "itambox/assets/tests/test_requests.py",
        "E226",
        "missing whitespace around arithmetic operator",
        "value = f'{1+1}'",
        "Module:body",
    )
    baseline = gate.collections.Counter({unlisted_key: 1})

    monkeypatch.setattr(gate.sys, "version_info", (3, 11))
    regressions, stale = gate.compare_baseline(gate.collections.Counter(), baseline)
    assert not regressions
    assert stale == baseline


def test_malformed_identity_baseline_fails_closed(fixture_project):
    result = _run(
        fixture_project,
        baseline={
            "schema_version": 2,
            "canonical_python": "3.12",
            "findings": [{"path": "pkg/mod.py"}],
        },
    )
    assert result.returncode == 2
    assert "invalid flake8 baseline" in result.stderr


def test_unknown_baseline_metadata_fails_closed(tmp_path):
    gate = _load_script("check_flake8_baseline_unknown_metadata")
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "canonical_python": "3.12",
                "policy_sha256": "expected",
                "findings": [],
                "unexpected": "ignored-policy-data",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="top-level fields"):
        gate.load_baseline(baseline_path, "expected")


def test_unsorted_baseline_fails_closed(tmp_path):
    gate = _load_script("check_flake8_baseline_unsorted")
    baseline_path = tmp_path / "baseline.json"
    baseline = _baseline(
        _finding(path="z.py"),
        _finding(path="a.py"),
    )
    baseline["policy_sha256"] = "expected"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    with pytest.raises(ValueError, match="sorted"):
        gate.load_baseline(baseline_path, "expected")


def test_unknown_stdout_with_success_status_fails_closed(tmp_path):
    gate = _load_script("check_flake8_baseline_unknown_success")
    _findings, _examples, failure = gate.validate_flake8_result(
        "INTERNAL PLUGIN FAILURE\n",
        "",
        0,
        tmp_path,
    )
    assert failure == 2


def test_finding_output_with_success_status_fails_closed(tmp_path):
    module = tmp_path / "mod.py"
    module.write_text("import os\n", encoding="utf-8")
    gate = _load_script("check_flake8_baseline_inconsistent_success")
    _findings, _examples, failure = gate.validate_flake8_result(
        "mod.py:1:1: F401 'os' imported but unused\n",
        "",
        0,
        tmp_path,
    )
    assert failure == 2


def test_whitespace_only_stderr_fails_closed(tmp_path):
    gate = _load_script("check_flake8_baseline_whitespace_stderr")
    _findings, _examples, failure = gate.validate_flake8_result(
        "",
        " \n",
        0,
        tmp_path,
    )
    assert failure == 2


def test_unknown_stdout_mixed_with_finding_fails_closed(tmp_path):
    module = tmp_path / "mod.py"
    module.write_text("import os\n", encoding="utf-8")
    gate = _load_script("check_flake8_baseline_unknown_mixed")
    _findings, _examples, failure = gate.validate_flake8_result(
        "mod.py:1:1: F401 'os' imported but unused\n"
        "INTERNAL PLUGIN FAILURE\n",
        "",
        1,
        tmp_path,
    )
    assert failure == 2


def test_operational_exit_one_without_findings_fails_closed(monkeypatch, tmp_path, capsys):
    """A Flake8/plugin config failure must not look like a clean empty run."""
    spec = importlib.util.spec_from_file_location("check_flake8_baseline_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    _write_fixture_policy(tmp_path)
    (tmp_path / "pkg").mkdir()
    baseline_path = tmp_path / "flake8_baseline.json"
    baseline_path.write_text(json.dumps(_baseline_for(tmp_path)), encoding="utf-8")
    monkeypatch.setattr(
        module,
        "run_flake8",
        lambda targets, cwd: ("", "plugin configuration exploded", 1),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT), "pkg", "--baseline", str(baseline_path), "--cwd", str(tmp_path)],
    )

    assert module.main() != 0
    captured = capsys.readouterr()
    assert "unexpected stderr" in captured.err


def test_operational_stderr_with_baseline_covered_findings_fails_closed(
    monkeypatch, tmp_path, capsys,
):
    """A plugin failure cannot hide beside otherwise grandfathered output."""
    spec = importlib.util.spec_from_file_location("check_flake8_baseline_mixed", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    _write_fixture_policy(tmp_path)
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "mod.py").write_text(VIOLATING_MODULE, encoding="utf-8")
    baseline_path = tmp_path / "flake8_baseline.json"
    baseline_path.write_text(
        json.dumps(_baseline_for(tmp_path, _finding())),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        module,
        "run_flake8",
        lambda targets, cwd: (
            "pkg/mod.py:1:1: F401 'os' imported but unused\n",
            "plugin configuration exploded",
            1,
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT), "pkg", "--baseline", str(baseline_path), "--cwd", str(tmp_path)],
    )

    assert module.main() != 0
    captured = capsys.readouterr()
    assert "unexpected stderr" in captured.err


def test_missing_or_wrong_plugin_version_fails_before_flake8(monkeypatch, tmp_path, capsys):
    spec = importlib.util.spec_from_file_location("check_flake8_baseline_tools", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(
        module,
        "version",
        lambda distribution: "0.0" if distribution == "flake8-bugbear" else module.REQUIRED_TOOL_VERSIONS[distribution],
    )
    monkeypatch.setattr(
        module,
        "run_flake8",
        lambda targets, cwd: pytest.fail("Flake8 must not run with a mismatched toolchain"),
    )
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--cwd", str(tmp_path)])

    assert module.main() == 2
    captured = capsys.readouterr()
    assert "flake8-bugbear==0.0" in captured.err


def test_bugbear_violation_is_part_of_enforced_policy(fixture_project):
    root, module = fixture_project
    module.write_text("def collect(items=[]):\n    return items\n", encoding="utf-8")
    result = _run(fixture_project, baseline=_baseline())
    assert result.returncode != 0, result.stdout + result.stderr
    assert "pkg/mod.py: B006 count 0 -> 1" in result.stdout
