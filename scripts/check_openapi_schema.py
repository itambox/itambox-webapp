#!/usr/bin/env python
"""Deterministic OpenAPI snapshot and diagnostics identity gate.

The baseline I/O layer is standard-library-only so repository gate tests can
import it before Django and project dependencies are installed in CI. Runtime
schema generation is added below the pure policy helpers.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

try:
    from scripts.openapi_identity import DiagnosticIdentity, IdentityError, parse_diagnostic
except ModuleNotFoundError:  # direct script execution places scripts/ rather than the repository root on sys.path
    from openapi_identity import DiagnosticIdentity, IdentityError, parse_diagnostic

BASELINE_PATH = REPO_ROOT / "scripts" / "openapi_diagnostics_baseline.json"
TRACKED_SCHEMA_PATH = REPO_ROOT / "itambox" / "schema.yaml"
BASELINE_SCHEMA_VERSION = 1
CANONICAL_PYTHON = (3, 12)
CANONICAL_PLATFORM = "linux"

BASELINE_HEADER_FIELDS = (
    "schema_version",
    "canonical_python",
    "canonical_platform",
    "django_version",
    "djangorestframework_version",
    "drf_spectacular_version",
    "pyyaml_version",
    "python_hash_seed",
    "settings_sha256",
    "policy_sha256",
)
DIAGNOSTIC_FIELDS = ("severity", "location", "breadcrumb", "message")
IDENTITY_RULE_VERSION = 1
GENERATION_POLICY = {
    "format": "openapi-yaml",
    "public": True,
    "language": "en",
    "validate": True,
    "trace_line_numbers": True,
    "generations": 2,
    "process_isolation": True,
    "log_level": "WARNING",
    "secret_key": "itambox-openapi-schema-generation-not-for-production",
}
DIAGNOSTIC_MARKER_RE = re.compile(r"(?:^|: )(Warning|Error)(?= \[|: )")
SUMMARY_COUNT_RE = re.compile(r"^(Warnings|Errors):\s+(\d+) \((\d+) unique\)$")


class PolicyError(Exception):
    """Raised when the OpenAPI gate cannot produce a trustworthy verdict."""


@dataclass(frozen=True)
class GenerationResult:
    schema: bytes
    diagnostics: dict
    warning_occurrences: int | None = None
    error_occurrences: int | None = None


def generate_twice(generate):
    """Catch byte drift and process-state/environment drift."""
    first = generate()
    second = generate()
    if first.schema != second.schema:
        raise PolicyError("two clean OpenAPI generations are not byte-for-byte equivalent")
    if Counter(first.diagnostics) != Counter(second.diagnostics):
        raise PolicyError("two clean OpenAPI generations produced different diagnostics")
    if first.warning_occurrences != second.warning_occurrences or first.error_occurrences != second.error_occurrences:
        raise PolicyError("two clean OpenAPI generations produced different diagnostic occurrence totals")
    return first


def check_tracked_schema(generated, tracked_path):
    tracked_path = Path(tracked_path)
    try:
        tracked = tracked_path.read_bytes()
    except FileNotFoundError as exc:
        raise PolicyError(f"no tracked OpenAPI schema snapshot at {tracked_path}") from exc
    except OSError as exc:
        raise PolicyError(f"cannot read tracked OpenAPI schema snapshot {tracked_path}: {exc}") from exc
    if tracked.startswith(b"\xef\xbb\xbf"):
        raise PolicyError("tracked OpenAPI schema snapshot must not contain a UTF-8 BOM")
    if b"\r" in tracked:
        raise PolicyError("tracked OpenAPI schema snapshot must use LF line endings")
    if tracked != generated:
        raise PolicyError("tracked OpenAPI schema snapshot differs from canonical generation")


def _canonical_json(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise PolicyError(f"OpenAPI generation policy is not canonically serializable: {exc}") from exc


def _sha256_json(value):
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def build_header(versions, spectacular_settings, plugins, python_hash_seed):
    required_versions = {"django", "djangorestframework", "drf_spectacular", "pyyaml"}
    if not isinstance(versions, dict) or set(versions) != required_versions:
        raise PolicyError("OpenAPI generation versions are incomplete")
    if python_hash_seed != "0":
        raise PolicyError("canonical OpenAPI generation requires PYTHONHASHSEED=0")
    settings_payload = {
        "spectacular_settings": spectacular_settings,
        "plugins": list(plugins),
    }
    settings_sha256 = _sha256_json(settings_payload)
    policy_payload = {
        "baseline_schema_version": BASELINE_SCHEMA_VERSION,
        "identity_rule_version": IDENTITY_RULE_VERSION,
        "canonical_python": "3.12",
        "canonical_platform": CANONICAL_PLATFORM,
        "versions": versions,
        "python_hash_seed": python_hash_seed,
        "settings_sha256": settings_sha256,
        "generation": GENERATION_POLICY,
    }
    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "canonical_python": "3.12",
        "canonical_platform": CANONICAL_PLATFORM,
        "django_version": versions["django"],
        "djangorestframework_version": versions["djangorestframework"],
        "drf_spectacular_version": versions["drf_spectacular"],
        "pyyaml_version": versions["pyyaml"],
        "python_hash_seed": python_hash_seed,
        "settings_sha256": settings_sha256,
        "policy_sha256": _sha256_json(policy_payload),
    }


def render_diagnostics_report(diagnostics, header, warning_occurrences=None, error_occurrences=None):
    _validate_header(header)
    counts = Counter(diagnostics)
    unique_warnings = sum(identity.severity == "warning" for identity in counts)
    unique_errors = sum(identity.severity == "error" for identity in counts)
    document = {field: header[field] for field in BASELINE_HEADER_FIELDS}
    document["summary"] = {
        "warnings": warning_occurrences
        if warning_occurrences is not None
        else sum(count for identity, count in counts.items() if identity.severity == "warning"),
        "errors": error_occurrences
        if error_occurrences is not None
        else sum(count for identity, count in counts.items() if identity.severity == "error"),
        "unique_warnings": unique_warnings,
        "unique_errors": unique_errors,
    }
    document["diagnostics"] = [
        {"identity": identity.as_dict(), "occurrences": counts[identity]} for identity in sorted(counts)
    ]
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def _normalise_identities(identities):
    try:
        return {
            identity if isinstance(identity, DiagnosticIdentity) else DiagnosticIdentity(**identity)
            for identity in identities
        }
    except (IdentityError, TypeError) as exc:
        raise PolicyError(f"invalid OpenAPI diagnostic identity: {exc}") from exc


def _validate_header(header):
    if not isinstance(header, dict) or tuple(header) != BASELINE_HEADER_FIELDS:
        raise PolicyError("OpenAPI baseline header has invalid fields or field order")
    if header["schema_version"] != BASELINE_SCHEMA_VERSION:
        raise PolicyError(f"expected OpenAPI baseline schema {BASELINE_SCHEMA_VERSION}")
    if header["canonical_python"] != "3.12":
        raise PolicyError("OpenAPI baseline canonical_python must be '3.12'")
    if header["canonical_platform"] != CANONICAL_PLATFORM:
        raise PolicyError(f"OpenAPI baseline canonical_platform must be {CANONICAL_PLATFORM!r}")
    for field in BASELINE_HEADER_FIELDS[3:]:
        if not isinstance(header[field], str) or not header[field]:
            raise PolicyError(f"OpenAPI baseline {field} must be a non-empty string")


def render_baseline(identities, header):
    """Return the canonical, count-free baseline JSON representation."""
    _validate_header(header)
    document = {field: header[field] for field in BASELINE_HEADER_FIELDS}
    document["diagnostics"] = [identity.as_dict() for identity in sorted(_normalise_identities(identities))]
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def load_baseline(path, expected_header):  # noqa: C901 - fail-closed parser validates one canonical document
    """Load and verify a canonical baseline bound to the current policy."""
    path = Path(path)
    try:
        raw_bytes = path.read_bytes()
    except FileNotFoundError as exc:
        raise PolicyError(
            f"no OpenAPI diagnostics baseline at {path}; bootstrap it from the canonical Linux CI artifact"
        ) from exc
    except OSError as exc:
        raise PolicyError(f"cannot read OpenAPI diagnostics baseline {path}: {exc}") from exc
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        raise PolicyError("OpenAPI diagnostics baseline must not contain a UTF-8 BOM")
    if b"\r" in raw_bytes:
        raise PolicyError("OpenAPI diagnostics baseline must use LF line endings")
    try:
        raw_text = raw_bytes.decode("utf-8")
        document = json.loads(raw_text)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot read OpenAPI diagnostics baseline {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise PolicyError("OpenAPI diagnostics baseline must be a JSON object")
    expected_top_level = set(BASELINE_HEADER_FIELDS) | {"diagnostics"}
    if set(document) != expected_top_level:
        raise PolicyError("OpenAPI diagnostics baseline has invalid top-level fields")
    header = {field: document[field] for field in BASELINE_HEADER_FIELDS}
    _validate_header(header)
    for field in BASELINE_HEADER_FIELDS:
        if header[field] != expected_header[field]:
            raise PolicyError(
                f"OpenAPI diagnostics baseline {field} does not match the current generation policy; "
                "regenerate the baseline in the reviewed change"
            )
    rows = document["diagnostics"]
    if not isinstance(rows, list):
        raise PolicyError("OpenAPI diagnostics baseline diagnostics must be a list")
    identities = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or tuple(row) != DIAGNOSTIC_FIELDS:
            raise PolicyError(f"OpenAPI baseline diagnostic {index} has invalid fields or field order")
        try:
            identities.append(DiagnosticIdentity(**row))
        except (IdentityError, TypeError) as exc:
            raise PolicyError(f"OpenAPI baseline diagnostic {index} is invalid: {exc}") from exc
    if len(set(identities)) != len(identities):
        raise PolicyError("OpenAPI diagnostics baseline contains duplicate identities")
    canonical = render_baseline(identities, expected_header)
    if raw_text != canonical:
        raise PolicyError("OpenAPI diagnostics baseline is not in canonical sorted form")
    return set(identities)


def compare_identities(current, recorded):
    current_set = _normalise_identities(current)
    recorded_set = _normalise_identities(recorded)
    return current_set - recorded_set, recorded_set - current_set


def verify_write_environment(version_info=None, platform_name=None):
    version_info = tuple(version_info or sys.version_info[:2])
    platform_name = platform_name if platform_name is not None else sys.platform
    problems = []
    if version_info != CANONICAL_PYTHON:
        problems.append(
            f"Python {CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]} is required "
            f"(running {version_info[0]}.{version_info[1]})"
        )
    if not platform_name.startswith(CANONICAL_PLATFORM):
        problems.append(f"{CANONICAL_PLATFORM} is required (running on {platform_name})")
    if problems:
        raise PolicyError(
            "refusing to write canonical OpenAPI artifacts outside the canonical environment: " + "; ".join(problems)
        )


def _write_lf(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def update_baseline(path, current, header):
    """Bootstrap or record cleanup, never grandfather a new identity."""
    verify_write_environment()
    path = Path(path)
    current_set = _normalise_identities(current)
    if path.exists():
        recorded = load_baseline(path, header)
        new, _stale = compare_identities(current_set, recorded)
        if new:
            raise PolicyError(
                f"refusing to grandfather {len(new)} new OpenAPI diagnostic identity/identities into the baseline"
            )
    _write_lf(path, render_baseline(current_set, header))


def parse_spectacular_stderr(stderr, repo_root):  # noqa: C901 - strict parser rejects every unknown line
    """Parse drf-spectacular's complete diagnostic stream and verify its summary."""
    diagnostics = Counter()
    raw_unique = Counter()
    summary = {}
    summary_started = False

    for line in stderr.splitlines():
        if not line:
            continue
        if line == "Schema generation summary:":
            if summary_started:
                raise PolicyError("duplicate schema generation summary")
            summary_started = True
            continue
        summary_match = SUMMARY_COUNT_RE.fullmatch(line)
        if summary_match:
            if not summary_started:
                raise PolicyError("schema generation counts appeared before the summary header")
            label, occurrences, unique = summary_match.groups()
            summary[label.lower()] = (int(occurrences), int(unique))
            continue
        if summary_started:
            raise PolicyError(f"unrecognized stderr after schema generation summary: {line!r}")

        markers = DIAGNOSTIC_MARKER_RE.findall(line)
        if len(markers) != 1:
            raise PolicyError(f"unrecognized stderr from OpenAPI generation: {line!r}")
        severity = markers[0].lower()
        try:
            identity = parse_diagnostic(line, severity, repo_root)
        except IdentityError as exc:
            raise PolicyError(f"cannot normalize drf-spectacular diagnostic: {exc}") from exc
        diagnostics[identity] += 1
        raw_unique[severity] += 1

    if set(summary) != {"warnings", "errors"}:
        raise PolicyError("missing schema generation summary")
    expected_unique = {
        "warning": summary["warnings"][1],
        "error": summary["errors"][1],
    }
    if dict(raw_unique) != {key: value for key, value in expected_unique.items() if value}:
        raise PolicyError("schema generation summary does not match emitted diagnostics")
    occurrences = {
        "warnings": summary["warnings"][0],
        "errors": summary["errors"][0],
    }
    return dict(diagnostics), occurrences


def _prepare_django_runtime():
    if os.environ.get("PYTHONHASHSEED") != "0":
        raise PolicyError("canonical OpenAPI generation requires PYTHONHASHSEED=0 before Python starts")
    settings_module = os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
    if settings_module != "core.settings":
        raise PolicyError("canonical OpenAPI generation requires DJANGO_SETTINGS_MODULE=core.settings")
    environment = os.environ.setdefault("ITAMBOX_ENV", "dev")
    if environment != "dev":
        raise PolicyError("canonical OpenAPI generation requires ITAMBOX_ENV=dev")
    os.environ["ITAMBOX_SECRET_KEY"] = GENERATION_POLICY["secret_key"]
    application_root = str(REPO_ROOT / "itambox")
    if application_root not in sys.path:
        sys.path.insert(0, application_root)

    # inline imports: optional-dependency: stdlib-only gate tests import this module before project dependencies are installed
    import django
    import drf_spectacular
    import rest_framework
    import yaml
    from django.conf import settings

    django.setup()
    versions = {
        "django": django.get_version(),
        "djangorestframework": rest_framework.VERSION,
        "drf_spectacular": drf_spectacular.__version__,
        "pyyaml": yaml.__version__,
    }
    settings_binding = {
        "SPECTACULAR_SETTINGS": dict(settings.SPECTACULAR_SETTINGS),
        "yaml_with_libyaml": bool(getattr(yaml, "__with_libyaml__", False)),
    }
    plugins = list(getattr(settings, "PLUGINS", []))
    header = build_header(versions, settings_binding, plugins, os.environ["PYTHONHASHSEED"])
    child_environment = os.environ.copy()
    child_environment["PYTHONHASHSEED"] = "0"
    child_environment["DJANGO_SETTINGS_MODULE"] = "core.settings"
    child_environment["ITAMBOX_ENV"] = "dev"
    child_environment["ITAMBOX_SECRET_KEY"] = GENERATION_POLICY["secret_key"]
    child_environment["ITAMBOX_LOG_LEVEL"] = GENERATION_POLICY["log_level"]
    child_environment.pop("PYTHONPATH", None)
    return {"environment": child_environment, "header": header}


def generate_once(runtime):
    """Generate and validate one schema in a clean Django management process."""
    handle = tempfile.NamedTemporaryFile(prefix="itambox-openapi-", suffix=".yaml", delete=False)
    output_path = Path(handle.name)
    handle.close()
    command = [
        sys.executable,
        "manage.py",
        "spectacular",
        "--file",
        str(output_path),
        "--format",
        "openapi",
        "--validate",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT / "itambox",
            env=runtime["environment"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=False,
        )
        if completed.stdout:
            raise PolicyError(f"OpenAPI generation wrote unexpected stdout: {completed.stdout!r}")
        if completed.returncode != 0:
            tail = "\n".join(completed.stderr.splitlines()[-20:])
            raise PolicyError(f"OpenAPI management command failed with exit {completed.returncode}:\n{tail}")
        diagnostics, occurrences = parse_spectacular_stderr(completed.stderr, REPO_ROOT)
        schema = output_path.read_bytes()
    except OSError as exc:
        raise PolicyError(f"cannot run OpenAPI management command: {exc}") from exc
    finally:
        output_path.unlink(missing_ok=True)
    if not schema or b"\r" in schema or schema.startswith(b"\xef\xbb\xbf"):
        raise PolicyError("generated OpenAPI schema must be non-empty UTF-8 without BOM and use LF line endings")
    return GenerationResult(
        schema=schema,
        diagnostics=diagnostics,
        warning_occurrences=occurrences["warnings"],
        error_occurrences=occurrences["errors"],
    )


def _write_bytes(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _artifact_hash(content):
    return hashlib.sha256(content).hexdigest()


def _format_identity(identity):
    context = f" [{identity.breadcrumb}]" if identity.breadcrumb else ""
    return f"{identity.severity.upper()} {identity.location}{context}: {identity.message}"


def _report_identity_changes(new, stale, limit=20):
    if new:
        print(f"OpenAPI diagnostics introduced {len(new)} new identity/identities:", file=sys.stderr)
        for identity in sorted(new)[:limit]:
            print(f"  + {_format_identity(identity)}", file=sys.stderr)
        if len(new) > limit:
            print(f"  ... {len(new) - limit} more in the generated diagnostics artifact", file=sys.stderr)
    if stale:
        print(
            f"OpenAPI diagnostics baseline is stale after {len(stale)} identity/identities disappeared; "
            "record the cleanup so removed debt cannot become headroom:",
            file=sys.stderr,
        )
        for identity in sorted(stale)[:limit]:
            print(f"  - {_format_identity(identity)}", file=sys.stderr)
        if len(stale) > limit:
            print(f"  ... {len(stale) - limit} more in the baseline diff", file=sys.stderr)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    parser.add_argument("--tracked-schema", type=Path, default=TRACKED_SCHEMA_PATH)
    parser.add_argument(
        "--schema-output",
        type=Path,
        default=REPO_ROOT / "itambox" / "schema.generated.yaml",
    )
    parser.add_argument(
        "--diagnostics-output",
        type=Path,
        default=REPO_ROOT / "itambox" / "openapi-diagnostics.generated.json",
    )
    parser.add_argument("--write-schema", action="store_true", help="Write the tracked schema on canonical Linux.")
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Bootstrap or record diagnostic cleanup on canonical Linux; new identities are refused.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        runtime = _prepare_django_runtime()
        result = generate_twice(lambda: generate_once(runtime))
        diagnostics_text = render_diagnostics_report(
            result.diagnostics,
            runtime["header"],
            warning_occurrences=result.warning_occurrences,
            error_occurrences=result.error_occurrences,
        )
        diagnostics_bytes = diagnostics_text.encode("utf-8")
        _write_bytes(args.schema_output, result.schema)
        _write_bytes(args.diagnostics_output, diagnostics_bytes)
        print(f"Generated OpenAPI schema sha256={_artifact_hash(result.schema)} at {args.schema_output}")
        print(f"Generated OpenAPI diagnostics sha256={_artifact_hash(diagnostics_bytes)} at {args.diagnostics_output}")

        if args.write_schema:
            verify_write_environment()
            _write_bytes(args.tracked_schema, result.schema)
            print(f"Wrote tracked OpenAPI schema snapshot to {args.tracked_schema}")
        else:
            check_tracked_schema(result.schema, args.tracked_schema)

        if args.write_baseline:
            update_baseline(args.baseline, result.diagnostics, runtime["header"])
            print(f"Wrote OpenAPI diagnostics baseline to {args.baseline}")
        else:
            recorded = load_baseline(args.baseline, runtime["header"])
            new, stale = compare_identities(result.diagnostics, recorded)
            if new or stale:
                _report_identity_changes(new, stale)
                return 1
    except PolicyError as exc:
        print(f"OpenAPI policy gate failed: {exc}", file=sys.stderr)
        if "no OpenAPI diagnostics baseline" in str(exc) or "no tracked OpenAPI schema snapshot" in str(exc):
            print(
                "Canonical generated artifacts were still emitted when generation succeeded; "
                "bootstrap them from the Linux CI artifact and review the tracked diff.",
                file=sys.stderr,
            )
            return 3
        return 2

    counts = Counter(result.diagnostics)
    warning_occurrences = result.warning_occurrences
    error_occurrences = result.error_occurrences
    print(
        "OpenAPI schema is deterministic and matches the tracked snapshot; diagnostics match the no-growth baseline: "
        f"{warning_occurrences} warning occurrence(s) "
        f"across {sum(identity.severity == 'warning' for identity in counts)} identity/identities, "
        f"{error_occurrences} error occurrence(s) "
        f"across {sum(identity.severity == 'error' for identity in counts)} identity/identities."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
