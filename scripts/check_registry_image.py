#!/usr/bin/env python3
"""Verify that the image published to a container registry is the qualified release image.

The release workflow publishes the exact image it built, scanned, archived, and
SBOM-validated, then resolves the published tag from the registry and binds it
back to the qualified local image. This gate refuses to let the release
continue when that binding cannot be established, and it never conflates the
two distinct digest families involved:

- ``GHCR_MANIFEST_DIGEST``: the digest the registry serves for the pushed tag.
  Docker 29 (the GitHub runner default) keeps built images in the containerd
  image store, where the tag resolves to an OCI image index that bundles the
  platform image manifest and any build-time attestation manifests. The digest
  of that index is what ``docker push`` records and what the registry returns,
  so it is the canonical pushed digest.
- ``IMAGE_CONFIG_DIGEST``: the configuration digest of the platform image
  manifest (the image ID of the classic image store). It identifies the
  runnable image, not the registry entry.

Verification performed:

- ``verify-manifest`` compares the raw manifest served by the registry against
  the push result recorded by Docker and the descriptor reported by Buildx,
  then checks the local image identity against exact content digests of the
  served artifact:
  - single manifest mode: the local image ID must equal the served manifest
    digest (containerd image store) or the manifest's configuration digest
    (classic image store);
  - index mode: the served index digest must equal the local image ID, and the
    index must contain exactly one platform image manifest besides optional
    attestation manifests, whose digest is reported for the second phase.
- ``verify-image`` fetches the platform image manifest by the digest listed in
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


class RegistryImageError(ValueError):
    """Raised when a published image cannot be bound to the qualified release image."""


@dataclass(frozen=True)
class ManifestSelection:
    """How the registry serves the published tag."""

    mode: str
    manifest_digest: str
    image_manifest_digest: str


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


def _platform_image_manifest(document: dict) -> dict:
    entries = document.get("manifests")
    if not isinstance(entries, list) or not entries:
        raise RegistryImageError("registry index does not list any manifests")
    image_entries = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise RegistryImageError("registry index contains a non-object manifest entry")
        annotations = entry.get("annotations") or {}
        if annotations.get("vnd.docker.reference.type") == "attestation-manifest":
            continue
        platform = entry.get("platform") or {}
        if platform.get("os") == "unknown" and platform.get("architecture") == "unknown":
            continue
        image_entries.append(entry)
    if len(image_entries) != 1:
        raise RegistryImageError(
            f"registry index must contain exactly one platform image manifest, found {len(image_entries)}"
        )
    return image_entries[0]


def verify_manifest(
    *,
    manifest_raw_path: Path,
    descriptor_path: Path,
    repo_digests_path: Path,
    local_image_id_path: Path,
    image: str,
) -> ManifestSelection:
    """Bind the registry-served manifest to the push record and the local image."""
    image = _require_untagged_image(image)
    raw = _read_bytes(manifest_raw_path, "registry manifest")
    document = _decode_json_object(raw, "registry manifest", manifest_raw_path)
    computed = _manifest_digest_of(raw)
    reported = _reported_manifest_digest(descriptor_path)
    repo_digests = _repo_digests(repo_digests_path, image)
    local_image_id = _require_digest(_read_text_line(local_image_id_path, "local image ID"), "local image ID")

    if reported != computed:
        raise RegistryImageError(
            f"registry resolved {image} to {reported}, but the served manifest hashes to {computed}"
        )
    if computed not in repo_digests:
        rendered = ", ".join(sorted(repo_digests))
        raise RegistryImageError(f"push recorded {rendered} for {image}, not the served manifest digest {computed}")
    if "config" in document:
        config_digest = _manifest_config_digest(document)
        # The containerd image store reports the manifest digest as the image
        # ID, the classic image store the configuration digest. Both are exact
        # content identities of the served image, so either may be the
        # qualified local image.
        if local_image_id not in {computed, config_digest}:
            raise RegistryImageError(
                f"served manifest digest {computed} and configuration digest {config_digest} "
                f"do not match the qualified local image {local_image_id}"
            )
        return ManifestSelection("single", computed, computed)
    if "manifests" in document:
        if local_image_id != computed:
            raise RegistryImageError(
                f"published index digest {computed} is not the qualified local image {local_image_id}"
            )
        entry = _platform_image_manifest(document)
        digest = _require_digest(entry.get("digest"), "index entry digest")
        return ManifestSelection("index", computed, digest)
    raise RegistryImageError("registry manifest is neither an image manifest nor an image index")


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
        "--local-image-id",
        type=Path,
        required=True,
        help="image ID reported by 'docker image inspect' for the qualified local image",
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
                local_image_id_path=args.local_image_id,
                image=args.image,
            )
            print(f"MODE={selection.mode}")
            print(f"GHCR_MANIFEST_DIGEST={selection.manifest_digest}")
            print(f"IMAGE_MANIFEST_DIGEST={selection.image_manifest_digest}")
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
