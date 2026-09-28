#!/usr/bin/env python3
"""Verify that the image published to a container registry is the qualified release image.

The release workflow builds one multi-platform image, pushes it to the registry
by digest while every gate is still pending, and promotes the recorded digest
to the version tag only after the scan, archive, and SBOM gates have passed.
This gate refuses to let the release continue when the served tag cannot be
bound to the digest the qualified build recorded, and it never conflates the
digest families involved:

- ``GHCR_MANIFEST_DIGEST``: the digest the registry serves for the released
  tag. The builder publishes the built multi-platform image as an OCI image
  index that bundles one platform image manifest per supported platform
  (``linux/amd64`` and ``linux/arm64``) plus build-time attestation manifests.
  The digest of that index is what the build records when it pushes and what
  the registry returns, so it is the canonical published digest.
- ``IMAGE_MANIFEST_DIGEST_<PLATFORM>``: the digest of each platform image
  manifest the index lists; it is the content-addressed identity of that
  platform image.
- ``IMAGE_CONFIG_DIGEST_<PLATFORM>``: the configuration digest inside each
  platform image manifest. It identifies the runnable image, not the registry
  entry.

Verification performed:

- ``verify-manifest`` compares the raw manifest served by the registry against
  the descriptor reported by Buildx and the digest recorded when the qualified
  build pushed its result, then binds the served tag to that build: the served
  digest must equal the recorded build digest, and the served document must be
  an image index that contains exactly the ``linux/amd64`` and ``linux/arm64``
  platform image manifests, besides optional attestation manifests. Both
  platform manifest digests are reported for the second phase. Every other
  served form fails closed: the release publishes a multi-platform image, so a
  single platform manifest is never accepted.
- ``verify-image`` fetches one platform image manifest by the digest listed in
  the verified index, requires the fetched bytes to hash to that digest, and
  extracts the configuration digest.

Everything is fail-closed: a missing, malformed, or inconsistent digest ends
the release preparation instead of letting it continue.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
SUPPORTED_PLATFORM_LABELS = ("linux/amd64", "linux/arm64")


class RegistryImageError(ValueError):
    """Raised when a published image cannot be bound to the qualified release image."""


@dataclass(frozen=True)
class ManifestSelection:
    """How the registry serves the published tag."""

    mode: str
    manifest_digest: str
    platform_manifest_digests: dict[str, str]


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise RegistryImageError(f"cannot read {label} {path}: {exc}") from exc


def _read_text_line(path: Path, label: str) -> str:
    raw = _read_bytes(path, label)
    try:
        return raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise RegistryImageError(f"{label} {path} is not valid UTF-8: {exc}") from exc


def _decode_json_object(raw: bytes, label: str, source: Path) -> dict:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RegistryImageError(f"{label} {source} is not valid UTF-8: {exc}") from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RegistryImageError(f"{label} {source} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise RegistryImageError(f"{label} {source} must be a JSON object")
    return document


def _require_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not DIGEST_PATTERN.fullmatch(value):
        raise RegistryImageError(f"{label} is not a sha256 content digest: {value!r}")
    return value


def _require_untagged_image(image: str) -> str:
    if "@" in image or ":" in image.rsplit("/", 1)[-1]:
        raise RegistryImageError(f"image must be an untagged repository reference: {image!r}")
    if "/" not in image:
        raise RegistryImageError(f"image must be a fully-qualified repository reference: {image!r}")
    return image


def _manifest_config_digest(document: dict) -> str:
    config = document.get("config")
    if not isinstance(config, dict):
        raise RegistryImageError("registry manifest has no image configuration descriptor")
    return _require_digest(config.get("digest"), "manifest configuration digest")


def _manifest_digest_of(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _reported_manifest_digest(descriptor_path: Path) -> str:
    descriptor = _decode_json_object(
        _read_bytes(descriptor_path, "manifest descriptor"), "manifest descriptor", descriptor_path
    )
    return _require_digest(descriptor.get("digest"), "reported manifest digest")


def _repo_digests(repo_digests_path: Path, image: str) -> set[str]:
    text = _read_text_line(repo_digests_path, "repository digests")
    digests = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith(f"{image}@"):
            # Docker records digests for every repository name the image
            # carries, so entries for other names are expected and ignored.
            continue
        digests.add(_require_digest(line.split("@", 1)[1], "repository digest"))
    if not digests:
        raise RegistryImageError(f"no repository digests recorded for {image}")
    return digests


def _platform_image_manifests(document: dict) -> dict[str, str]:
    """Return each supported platform's image manifest digest from the index."""
    entries = document.get("manifests")
    if not isinstance(entries, list) or not entries:
        raise RegistryImageError("registry index does not list any manifests")
    platform_entries: list[tuple[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise RegistryImageError("registry index contains a non-object manifest entry")
        annotations = entry.get("annotations") or {}
        if annotations.get("vnd.docker.reference.type") == "attestation-manifest":
            continue
        platform = entry.get("platform") or {}
        if platform.get("os") == "unknown" and platform.get("architecture") == "unknown":
            continue
        label = (
            "/".join(
                str(part)
                for part in (platform.get("os"), platform.get("architecture"), platform.get("variant"))
                if part
            )
            or "<missing platform>"
        )
        digest = _require_digest(entry.get("digest"), f"index entry digest for {label}")
        platform_entries.append((label, digest))
    labels = sorted(label for label, _ in platform_entries)
    if labels != sorted(SUPPORTED_PLATFORM_LABELS):
        found = ", ".join(labels) if labels else "none"
        raise RegistryImageError(
            "registry index must contain exactly the linux/amd64 and linux/arm64 platform "
            f"image manifests, found {found}"
        )
    platform_digests = {label.split("/")[1]: digest for label, digest in platform_entries}
    if platform_digests["amd64"] == platform_digests["arm64"]:
        raise RegistryImageError("registry index platform manifests must have distinct digests")
    return platform_digests


def verify_manifest(
    *,
    manifest_raw_path: Path,
    descriptor_path: Path,
    repo_digests_path: Path,
    expected_index_digest_path: Path,
    image: str,
) -> ManifestSelection:
    """Bind the registry-served tag to the digest the qualified build recorded."""
    image = _require_untagged_image(image)
    raw = _read_bytes(manifest_raw_path, "registry manifest")
    document = _decode_json_object(raw, "registry manifest", manifest_raw_path)
    computed = _manifest_digest_of(raw)
    reported = _reported_manifest_digest(descriptor_path)
    repo_digests = _repo_digests(repo_digests_path, image)
    expected_index_digest = _require_digest(
        _read_text_line(expected_index_digest_path, "expected index digest"), "expected index digest"
    )

    if reported != computed:
        raise RegistryImageError(
            f"registry resolved {image} to {reported}, but the served manifest hashes to {computed}"
        )
    if computed not in repo_digests:
        rendered = ", ".join(sorted(repo_digests))
        raise RegistryImageError(f"push recorded {rendered} for {image}, not the served manifest digest {computed}")
    if "manifests" in document:
        if expected_index_digest != computed:
            raise RegistryImageError(
                f"published index digest {computed} does not match the qualified build {expected_index_digest}"
            )
        return ManifestSelection("index", computed, _platform_image_manifests(document))
    raise RegistryImageError(
        "registry manifest is not a multi-platform image index; "
        "the release publishes a linux/amd64 and linux/arm64 image"
    )


def verify_image_manifest(*, manifest_raw_path: Path, expected_digest: str) -> str:
    """Verify the image manifest fetched by digest and return its configuration digest."""
    expected = _require_digest(expected_digest, "expected image manifest digest")
    raw = _read_bytes(manifest_raw_path, "image manifest")
    computed = _manifest_digest_of(raw)
    if computed != expected:
        raise RegistryImageError(f"fetched image manifest hashes to {computed}, expected {expected}")
    document = _decode_json_object(raw, "image manifest", manifest_raw_path)
    return _manifest_config_digest(document)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest = subparsers.add_parser(
        "verify-manifest", help="bind the registry-served manifest to the qualified release image"
    )
    manifest.add_argument("--manifest-raw", type=Path, required=True, help="raw manifest served by the registry")
    manifest.add_argument(
        "--descriptor",
        type=Path,
        required=True,
        help="registry-reported manifest descriptor JSON, for example 'imagetools inspect --format {{json .Manifest}}'",
    )
    manifest.add_argument("--repo-digests", type=Path, required=True, help="repository digests recorded by the push")
    manifest.add_argument(
        "--expected-index-digest",
        type=Path,
        required=True,
        help="index digest recorded by the qualified build, for example 'containerimage.digest' from the buildx metadata file",
    )
    manifest.add_argument(
        "--image",
        required=True,
        help="untagged registry repository, for example ghcr.io/itambox/itambox-webapp",
    )

    image = subparsers.add_parser("verify-image", help="verify the platform image manifest fetched by digest")
    image.add_argument("--manifest-raw", type=Path, required=True, help="raw image manifest fetched by digest")
    image.add_argument("--expected-digest", required=True, help="digest the index listed for it")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "verify-manifest":
            selection = verify_manifest(
                manifest_raw_path=args.manifest_raw,
                descriptor_path=args.descriptor,
                repo_digests_path=args.repo_digests,
                expected_index_digest_path=args.expected_index_digest,
                image=args.image,
            )
            print(f"MODE={selection.mode}")
            print(f"GHCR_MANIFEST_DIGEST={selection.manifest_digest}")
            for architecture in sorted(selection.platform_manifest_digests):
                digest = selection.platform_manifest_digests[architecture]
                print(f"IMAGE_MANIFEST_DIGEST_{architecture.upper()}={digest}")
            print(f"registry manifest verification passed for {args.image}", file=sys.stderr)
        else:
            config_digest = verify_image_manifest(
                manifest_raw_path=args.manifest_raw, expected_digest=args.expected_digest
            )
            print(f"IMAGE_CONFIG_DIGEST={config_digest}")
            print("image manifest verification passed", file=sys.stderr)
    except RegistryImageError as exc:
        print(f"registry image verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
