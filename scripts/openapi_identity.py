"""Portable identities for drf-spectacular warnings and errors.

This module deliberately uses only the Python standard library. Repository gate
suites import it before project dependencies are installed in CI.
"""

import re
from dataclasses import dataclass
from pathlib import Path

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
WINDOWS_ABSOLUTE_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]")
LINE_NUMBER_RE = re.compile(r":\d+(?::\d+)?$")
SEVERITIES = frozenset({"warning", "error"})


class IdentityError(ValueError):
    """Raised when a diagnostic cannot be converted to a stable identity."""


@dataclass(frozen=True, order=True)
class DiagnosticIdentity:
    severity: str
    location: str
    breadcrumb: str
    message: str

    def __post_init__(self):
        if self.severity not in SEVERITIES:
            raise IdentityError(f"unsupported diagnostic severity {self.severity!r}")
        for field_name, value in self.as_dict().items():
            if not isinstance(value, str):
                raise IdentityError(f"diagnostic {field_name} must be a string")
            _reject_unsafe_text(value, field_name)
        if not self.location:
            raise IdentityError("diagnostic location must not be empty")
        if self.location not in {"<unknown>"} and not self.location.startswith("<external>:"):
            if self.location.startswith(("/", "./")) or "\\" in self.location:
                raise IdentityError("diagnostic location must be a POSIX repository-relative path")
            if ".." in self.location.split("/"):
                raise IdentityError("diagnostic location must not traverse outside the repository")
            if LINE_NUMBER_RE.search(self.location):
                raise IdentityError("diagnostic location must not contain a line number")

    def as_dict(self):
        return {
            "severity": self.severity,
            "location": self.location,
            "breadcrumb": self.breadcrumb,
            "message": self.message,
        }


def _reject_unsafe_text(value, field_name):
    if ANSI_ESCAPE_RE.search(value):
        raise IdentityError(f"diagnostic {field_name} contains ANSI escapes")
    if CONTROL_RE.search(value):
        raise IdentityError(f"diagnostic {field_name} contains a control character")


def _normalise_path_text(value):
    return re.sub(r"/+", "/", value.replace("\\", "/")).rstrip("/")


def _is_windows_path(value):
    return bool(re.match(r"^[A-Za-z]:/", value))


def _relative_to_repo(source, repo_root):
    source_text = _normalise_path_text(source)
    root_text = _normalise_path_text(str(Path(repo_root)))
    source_cmp = source_text.casefold() if _is_windows_path(source_text) else source_text
    root_cmp = root_text.casefold() if _is_windows_path(root_text) else root_text
    prefix = root_cmp + "/"
    if source_cmp.startswith(prefix):
        return source_text[len(root_text) + 1 :]
    return None


def _external_module(source):
    normalised = _normalise_path_text(source)
    marker = "/site-packages/"
    index = normalised.casefold().find(marker)
    if index < 0:
        return None
    relative = normalised[index + len(marker) :]
    if relative.endswith(".py"):
        relative = relative[:-3]
    if relative.endswith("/__init__"):
        relative = relative[: -len("/__init__")]
    dotted = relative.replace("/", ".").strip(".")
    if not dotted or not re.fullmatch(r"[A-Za-z0-9_.-]+", dotted):
        raise IdentityError("external diagnostic source cannot be converted to a module token")
    return f"<external>:{dotted}"


def _normalise_location(source, repo_root):
    if not source:
        return "<unknown>"
    source = LINE_NUMBER_RE.sub("", source)

    external = _external_module(source)
    if external is not None:
        return external
    relative = _relative_to_repo(source, repo_root)
    if relative is not None:
        return _normalise_path_text(relative)
    normalised = _normalise_path_text(source)
    if normalised.startswith("/") or _is_windows_path(normalised):
        raise IdentityError("diagnostic source is an absolute path outside the repository")
    return normalised


def _split_rendered_diagnostic(raw, severity):
    label = severity.capitalize()
    if raw.startswith(label):
        source = ""
        remainder = raw[len(label) :]
    else:
        token = f": {label}"
        index = raw.find(token)
        if index < 0:
            raise IdentityError("rendered diagnostic severity does not match the declared severity")
        source = raw[:index]
        remainder = raw[index + len(token) :]
    if remainder.startswith(" ["):
        end = remainder.find("]: ")
        if end < 0:
            raise IdentityError("diagnostic breadcrumb is malformed")
        breadcrumb = remainder[2:end]
        message = remainder[end + 3 :]
    elif remainder.startswith(": "):
        breadcrumb = ""
        message = remainder[2:]
    else:
        raise IdentityError("rendered diagnostic prefix is malformed")
    return source, breadcrumb, message


def parse_diagnostic(raw, severity, repo_root):
    """Convert one GeneratorStats cache key into a stable identity."""
    if severity not in SEVERITIES:
        raise IdentityError(f"unsupported diagnostic severity {severity!r}")
    if not isinstance(raw, str):
        raise IdentityError("rendered diagnostic must be a string")
    _reject_unsafe_text(raw, "rendering")
    source, breadcrumb, message = _split_rendered_diagnostic(raw, severity)
    message = "\n".join(line.rstrip() for line in message.replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()
    breadcrumb = " ".join(breadcrumb.split())
    root_text = _normalise_path_text(str(Path(repo_root)))
    if WINDOWS_ABSOLUTE_RE.search(message) or (
        root_text and root_text.casefold() in _normalise_path_text(message).casefold()
    ):
        raise IdentityError("diagnostic message contains an absolute path")
    return DiagnosticIdentity(
        severity=severity,
        location=_normalise_location(source, repo_root),
        breadcrumb=breadcrumb,
        message=message,
    )
