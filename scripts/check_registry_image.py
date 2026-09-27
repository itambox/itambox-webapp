#!/usr/bin/env python3
"""Verify that the image published to a container registry is the qualified release image.

The release workflow publishes the exact image it built, scanned, archived, and
SBOM-validated to a container registry. This gate refuses to let the release
continue on a registry publication that cannot be bound back to that qualified
image: the published manifest's configuration digest must equal the locally
inspected image's configuration digest, and the registry manifest digest must
be reported identically by every registry-aware source the workflow consulted.

Manifest digest and configuration digest are distinct sha256 content digests:
the manifest digest is what the registry keys the pushed tag under, the
configuration digest is the image ID embedded in the manifest. They are
validated and reported separately, never conflated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


class RegistryImageError(ValueError):
    """Raised when a published image cannot be bound to the qualified release image."""


DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class RegistryIdentity:
    image: str
    manifest_digest: str
    config_digest: str


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
            raise RegistryImageError(f"repository digest {line!r} does not belong to {image}")
        digests.add(_require_digest(line.split("@", 1)[1], "repository digest"))
    if not digests:
        raise RegistryImageError(f"no repository digests recorded for {image}")
    return digests


def verify_registry_image(
    *,
    manifest_raw_path: Path,
    descriptor_path: Path,
    repo_digests_path: Path,
    local_config_digest_path: Path,
    image: str,
) -> RegistryIdentity:
    """Bind a published registry manifest to the locally inspected release image."""
    image = _require_untagged_image(image)
    raw = _read_bytes(manifest_raw_path, "registry manifest")
    document = _decode_json_object(raw, "registry manifest", manifest_raw_path)
    computed = "sha256:" + hashlib.sha256(raw).hexdigest()
    config_digest = _manifest_config_digest(document)
    reported = _reported_manifest_digest(descriptor_path)
    local_config_digest = _require_digest(
        _read_text_line(local_config_digest_path, "local image configuration digest"),
        "local image configuration digest",
    )
    repo_digests = _repo_digests(repo_digests_path, image)

    if reported != computed:
        raise RegistryImageError(
            f"registry resolved {image} to {reported}, but the served manifest hashes to {computed}"
        )
    if config_digest != local_config_digest:
        raise RegistryImageError(
            f"published manifest configuration digest {config_digest} is not the qualified local image {local_config_digest}"
        )
    if computed not in repo_digests:
        rendered = ", ".join(sorted(repo_digests))
        raise RegistryImageError(f"push recorded {rendered} for {image}, not the served manifest digest {computed}")

    return RegistryIdentity(image=image, manifest_digest=computed, config_digest=config_digest)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify", help="bind a published registry manifest to the qualified release image")
    verify.add_argument("--manifest-raw", type=Path, required=True, help="raw manifest served by the registry")
    verify.add_argument(
        "--descriptor",
        type=Path,
        required=True,
        help="registry-reported manifest descriptor, for example the JSON of 'imagetools inspect --format {{json .Manifest}}'",
    )
    verify.add_argument("--repo-digests", type=Path, required=True, help="repository digests recorded by the push")
    verify.add_argument(
        "--local-config-digest",
        type=Path,
        required=True,
        help="configuration digest of the locally inspected qualified image",
    )
    verify.add_argument(
        "--image", required=True, help="untagged registry repository, for example ghcr.io/itambox/itambox-webapp"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        identity = verify_registry_image(
            manifest_raw_path=args.manifest_raw.resolve(),
            descriptor_path=args.descriptor.resolve(),
            repo_digests_path=args.repo_digests.resolve(),
            local_config_digest_path=args.local_config_digest.resolve(),
            image=args.image,
        )
    except RegistryImageError as exc:
        print(f"registry image verification failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"published {identity.image} matches the qualified image "
        f"(manifest {identity.manifest_digest}, config {identity.config_digest})",
        file=sys.stderr,
    )
    print(f"GHCR_MANIFEST_DIGEST={identity.manifest_digest}")
    print(f"IMAGE_CONFIG_DIGEST={identity.config_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
