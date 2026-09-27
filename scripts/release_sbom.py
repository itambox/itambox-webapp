#!/usr/bin/env python3
"""Validate the release image SBOM against the artifact identity it describes.

The release workflow generates an SPDX JSON SBOM from the exact container image
it archives and publishes. This gate refuses to let that SBOM move on
unvalidated: it must be a real SPDX document with content, and it must describe
the image identified by the release build (its configuration digest and its
reference), so a published SBOM can never silently describe a different image
than the one being released.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path


class SbomValidationError(ValueError):
    """Raised when a release SBOM cannot be bound to the released image."""


@dataclass(frozen=True)
class ImageIdentity:
    image_id: str
    repo_tags: tuple[str, ...]


@dataclass(frozen=True)
class SbomSummary:
    spdx_version: str
    document_name: str
    package_count: int
    sha256: str


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise SbomValidationError(f"cannot read {label} {path}: {exc}") from exc


def _decode_and_parse(raw: bytes, label: str, source: Path) -> tuple[str, object]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SbomValidationError(f"{label} {source} is not valid UTF-8: {exc}") from exc
    try:
        return text, json.loads(text)
    except json.JSONDecodeError as exc:
        raise SbomValidationError(f"{label} {source} is not valid JSON: {exc}") from exc


def _require_text(document: dict, field: str, message: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value.strip():
        raise SbomValidationError(message)
    return value


def _require_mention(text: str, description: str, expected: str | None) -> None:
    if expected is not None and expected not in text:
        raise SbomValidationError(f"SBOM does not reference the release {description} {expected}")


def read_image_identity(image_inspect_path: Path) -> ImageIdentity:
    """Return the image ID and repository tags of the image in a docker inspect file."""
    inspection_bytes = _read_bytes(image_inspect_path, "image inspection")
    _, data = _decode_and_parse(inspection_bytes, "image inspection", image_inspect_path)
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise SbomValidationError("image inspection must be a non-empty list of image objects")
    image = data[0]
    image_id = image.get("Id")
    if not isinstance(image_id, str) or not image_id.startswith("sha256:"):
        raise SbomValidationError("image inspection has no sha256 image ID")
    repo_tags = image.get("RepoTags") or []
    if not isinstance(repo_tags, list) or not all(isinstance(tag, str) for tag in repo_tags):
        raise SbomValidationError("image inspection has malformed repository tags")
    return ImageIdentity(image_id=image_id, repo_tags=tuple(repo_tags))


def _packages_of(document: dict) -> list:
    packages = document.get("packages")
    if not isinstance(packages, list) or not packages:
        raise SbomValidationError("SBOM has no packages; an empty document is not a release SBOM")
    if not any(isinstance(package, dict) and package.get("name") for package in packages):
        raise SbomValidationError("SBOM packages carry no names")
    return packages


def validate_sbom(
    sbom_path: Path,
    *,
    expected_image_id: str | None = None,
    expected_image_ref: str | None = None,
    min_packages: int = 1,
) -> SbomSummary:
    """Validate one SPDX JSON SBOM and optionally bind it to the release image."""
    raw = _read_bytes(sbom_path, "SBOM")
    text, document = _decode_and_parse(raw, "SBOM", sbom_path)
    if not isinstance(document, dict):
        raise SbomValidationError("SBOM must be a JSON object")
    spdx_version = _require_text(document, "spdxVersion", "SBOM is not an SPDX document (missing spdxVersion)")
    if not spdx_version.startswith("SPDX-"):
        raise SbomValidationError(f"SBOM is not an SPDX document (unexpected spdxVersion {spdx_version!r})")
    _require_text(document, "SPDXID", "SBOM has no document SPDXID")
    document_name = _require_text(document, "name", "SBOM has no document name")

    packages = _packages_of(document)
    if len(packages) < min_packages:
        raise SbomValidationError(f"SBOM package count {len(packages)} is below the required minimum {min_packages}")
    _require_mention(text, "image digest", expected_image_id)
    _require_mention(text, "image", expected_image_ref)

    return SbomSummary(
        spdx_version=spdx_version,
        document_name=document_name,
        package_count=len(packages),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def verify_release_sbom(
    sbom_path: Path,
    image_inspect_path: Path,
    image_ref: str,
    min_packages: int = 1,
) -> SbomSummary:
    """Bind a generated SBOM to the inspected release image and validate it."""
    identity = read_image_identity(image_inspect_path)
    if image_ref not in identity.repo_tags:
        rendered = ", ".join(identity.repo_tags) if identity.repo_tags else "<untagged>"
        raise SbomValidationError(f"inspected image carries {rendered}, not the expected release reference {image_ref}")
    return validate_sbom(
        sbom_path,
        expected_image_id=identity.image_id,
        expected_image_ref=image_ref,
        min_packages=min_packages,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify", help="validate a release SBOM against the released image identity")
    verify.add_argument("--sbom", type=Path, required=True, help="generated SPDX JSON SBOM")
    verify.add_argument(
        "--image-inspect",
        type=Path,
        required=True,
        help="docker image inspect output for the released image",
    )
    verify.add_argument(
        "--image-ref",
        required=True,
        help="expected image reference, for example itambox:1.0.0-beta.2",
    )
    verify.add_argument("--min-packages", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        summary = verify_release_sbom(
            args.sbom.resolve(),
            args.image_inspect.resolve(),
            args.image_ref,
            min_packages=args.min_packages,
        )
    except SbomValidationError as exc:
        print(f"release SBOM validation failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Release SBOM valid: {args.sbom} ({summary.spdx_version}, {summary.package_count} packages, "
        f"document {summary.document_name!r}, sha256={summary.sha256}) matches {args.image_ref}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
