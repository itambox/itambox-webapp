"""Blocking gate for pinned container images used by release and CI paths."""

from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIRECTORY = ".github/workflows"
WORKFLOW_FILES = (
    ".github/workflows/ci.yml",
    ".github/workflows/docker-smoke.yml",
    ".github/workflows/e2e.yml",
    ".github/workflows/image-drift.yml",
    ".github/workflows/release.yml",
    ".github/workflows/runner-heavy-validation.yml",
    ".github/workflows/runner-validation.yml",
    ".github/workflows/security.yml",
    ".github/workflows/xdist-validation.yml",
)

PINNED_IMAGE = re.compile(
    r"^[A-Za-z0-9_][A-Za-z0-9._-]*(?::[0-9]+)?"
    r"(?:/[A-Za-z0-9_][A-Za-z0-9._-]*)*"
    r":[A-Za-z0-9_][A-Za-z0-9._-]*@sha256:[0-9a-fA-F]{64}$"
)
DOCKER_FROM = re.compile(r"^\s*FROM(?:\s+|$)(.*)$", re.IGNORECASE)
YAML_IMAGE = re.compile(r"^\s*image\s*:\s*(.*?)\s*$")


def _diagnostic(path: str, line_number: int, image: str) -> str:
    return f"{path}:{line_number}: unpinned container image reference {image!r}; expected <name>:<tag>@sha256:<64-hex>"


def _is_pinned(image: str) -> bool:
    return PINNED_IMAGE.fullmatch(image) is not None


def scan_dockerfile(path: str, content: str) -> list[str]:
    """Return findings for every Dockerfile FROM instruction in content."""
    findings = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        match = DOCKER_FROM.match(line)
        if match is None:
            continue

        parts = match.group(1).split()
        while parts and parts[0].startswith("--"):
            parts.pop(0)
        image = parts[0] if parts else "<missing image>"
        if not _is_pinned(image):
            findings.append(_diagnostic(path, line_number, image))
    return findings


def _yaml_scalar(value: str) -> str:
    value = value.strip()
    if value.startswith(("'", '"')):
        quote = value[0]
        closing_quote = value.find(quote, 1)
        if closing_quote < 0:
            return value
        remainder = value[closing_quote + 1 :].strip()
        if remainder and not remainder.startswith("#"):
            return value
        return value[1:closing_quote]
    return value.split("#", 1)[0].strip()


def scan_yaml_images(path: str, content: str) -> list[str]:
    """Return findings for every YAML image key in content."""
    findings = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        match = YAML_IMAGE.match(line)
        if match is None:
            continue

        image = _yaml_scalar(match.group(1)) or "<missing image>"
        if not _is_pinned(image):
            findings.append(_diagnostic(path, line_number, image))
    return findings


def scan_repository(root: Path = REPOSITORY_ROOT) -> list[str]:
    """Check every explicit source file and fail if the workflow inventory drifts."""
    root = root.resolve()
    findings = []
    explicit_files = ("Dockerfile", "docker-compose.yml", *WORKFLOW_FILES)
    workflow_directory = root / WORKFLOW_DIRECTORY
    discovered_workflows = set()
    if workflow_directory.is_dir():
        discovered_workflows = {
            path.relative_to(root).as_posix()
            for path in workflow_directory.iterdir()
            if path.is_file() and path.suffix in {".yml", ".yaml"}
        }
    expected_workflows = set(WORKFLOW_FILES)
    if discovered_workflows != expected_workflows:
        missing = ", ".join(sorted(expected_workflows - discovered_workflows)) or "none"
        unexpected = ", ".join(sorted(discovered_workflows - expected_workflows)) or "none"
        findings.append(
            f"{WORKFLOW_DIRECTORY}:1: workflow image scan list changed; missing={missing}; unexpected={unexpected}"
        )
        explicit_files = (*explicit_files, *sorted(discovered_workflows - expected_workflows))

    for relative_path in explicit_files:
        path = root / relative_path
        if not path.is_file():
            findings.append(f"{relative_path}:1: expected image scan file is missing")
            continue

        content = path.read_text(encoding="utf-8")
        if relative_path == "Dockerfile":
            findings.extend(scan_dockerfile(relative_path, content))
        else:
            findings.extend(scan_yaml_images(relative_path, content))

    return sorted(findings)


def main() -> int:
    findings = scan_repository()
    if not findings:
        print("container image pinning policy: all scanned image references include a tag and SHA-256 digest.")
        return 0

    print("container image pinning policy: unpinned image references or scan-list changes were found:")
    for finding in findings:
        print(f"  {finding}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
