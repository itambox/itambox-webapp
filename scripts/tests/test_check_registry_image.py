"""Behaviour tests for the container registry publication gate.

The gate runs on a bare interpreter inside the release workflow, so these tests
are standard-library-only and exercise the real verification path for the
multi-platform index push the release publishes: the containerd image store
(Docker 29 default) publishes the built image as an OCI image index that
bundles the linux/amd64 and linux/arm64 platform image manifests with
build-time attestation manifests. Every way of describing a different image, a
different digest, a missing or unsupported platform, or a malformed digest
fails closed, and a single-platform push is rejected because the release
publishes both platforms.
"""

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scripts.check_registry_image import (
    RegistryImageError,
    main,
    verify_image_manifest,
    verify_manifest,
)

IMAGE = "ghcr.io/itambox/itambox-webapp"
CONFIG_DIGEST = "sha256:" + "cd" * 32
ARM64_CONFIG_DIGEST = "sha256:" + "ef" * 32
LAYER_DIGEST = "sha256:" + "11" * 32
OTHER_DIGEST = "sha256:" + "ab" * 32
ATTESTATION_DIGEST = "sha256:" + "5a" * 32


def _raw(document: dict) -> bytes:
    return json.dumps(document).encode("utf-8")


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _image_manifest(*, config_digest: str = CONFIG_DIGEST) -> dict:
    return {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": config_digest,
            "size": 1234,
        },
        "layers": [
            {
                "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                "digest": LAYER_DIGEST,
                "size": 5678,
            }
        ],
    }


def _platform_entry(*, digest: str, architecture: str, size: int = 1234) -> dict:
    return {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "digest": digest,
        "size": size,
        "platform": {"architecture": architecture, "os": "linux"},
    }


def _attestation_entry(*, digest: str, reference_digest: str) -> dict:
    return {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "digest": digest,
        "size": 837,
        "annotations": {
            "vnd.docker.reference.digest": reference_digest,
            "vnd.docker.reference.type": "attestation-manifest",
        },
        "platform": {"architecture": "unknown", "os": "unknown"},
    }


def _index(*, amd64_manifest_digest: str, arm64_manifest_digest: str, extra_entries: list | None = None) -> dict:
    entries = [
        _platform_entry(digest=amd64_manifest_digest, architecture="amd64"),
        _platform_entry(digest=arm64_manifest_digest, architecture="arm64"),
        _attestation_entry(digest=OTHER_DIGEST, reference_digest=amd64_manifest_digest),
        _attestation_entry(digest=ATTESTATION_DIGEST, reference_digest=arm64_manifest_digest),
    ]
    if extra_entries:
        entries.extend(extra_entries)
    return {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": entries,
    }


class RegistryImageGateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write(self, name: str, content) -> Path:
        path = self.root / name
        if isinstance(content, (dict, list)):
            content = json.dumps(content)
        if isinstance(content, str):
            content = content.encode("utf-8")
        path.write_bytes(content)
        return path

    def _single_inputs(
        self,
        *,
        image_manifest: dict | None = None,
        reported_digest: str | None = None,
        repo_digest_entries: str | None = None,
        expected_index_digest: str = CONFIG_DIGEST,
        image: str = IMAGE,
    ) -> dict:
        """Write the inputs describing a single-platform manifest push."""
        if image_manifest is None:
            image_manifest = _image_manifest()
        raw = _raw(image_manifest)
        digest = _digest(raw)
        if reported_digest is None:
            reported_digest = digest
        if repo_digest_entries is None:
            repo_digest_entries = f"{image}@{digest}\n"
        return {
            "manifest_raw_path": self._write("manifest.json", raw),
            "descriptor_path": self._write(
                "descriptor.json",
                {
                    "mediaType": image_manifest.get("mediaType"),
                    "digest": reported_digest,
                    "size": len(raw),
                },
            ),
            "repo_digests_path": self._write("repo-digests.txt", repo_digest_entries),
            "expected_index_digest_path": self._write("expected-index-digest.txt", expected_index_digest),
            "image_manifest_raw_path": self._write("manifest-copy.json", raw),
            "manifest_digest": digest,
            "image": image,
        }

    def _index_inputs(
        self,
        *,
        platform_manifest: dict | None = None,
        arm64_platform_manifest: dict | None = None,
        index_document: dict | None = None,
        reported_digest: str | None = None,
        repo_digest_entries: str | None = None,
        expected_index_digest: str | None = None,
        image: str = IMAGE,
    ) -> dict:
        """Write the inputs describing a published image index."""
        if platform_manifest is None:
            platform_manifest = _image_manifest()
        if arm64_platform_manifest is None:
            arm64_platform_manifest = _image_manifest(config_digest=ARM64_CONFIG_DIGEST)
        platform_raw = _raw(platform_manifest)
        platform_digest = _digest(platform_raw)
        arm64_raw = _raw(arm64_platform_manifest)
        arm64_digest = _digest(arm64_raw)
        if index_document is None:
            index_document = _index(amd64_manifest_digest=platform_digest, arm64_manifest_digest=arm64_digest)
        index_raw = _raw(index_document)
        index_digest = _digest(index_raw)
        if reported_digest is None:
            reported_digest = index_digest
        if repo_digest_entries is None:
            repo_digest_entries = f"{image}@{index_digest}\n"
        if expected_index_digest is None:
            expected_index_digest = index_digest
        return {
            "manifest_raw_path": self._write("index.json", index_raw),
            "descriptor_path": self._write(
                "descriptor.json",
                {
                    "mediaType": index_document.get("mediaType"),
                    "digest": reported_digest,
                    "size": len(index_raw),
                },
            ),
            "repo_digests_path": self._write("repo-digests.txt", repo_digest_entries),
            "expected_index_digest_path": self._write("expected-index-digest.txt", expected_index_digest),
            "image_manifest_raw_path": self._write("image-manifest-amd64.json", platform_raw),
            "arm64_image_manifest_raw_path": self._write("image-manifest-arm64.json", arm64_raw),
            "index_digest": index_digest,
            "platform_digest": platform_digest,
            "arm64_platform_digest": arm64_digest,
            "image": image,
        }

    def _verify_manifest(self, files: dict):
        return verify_manifest(
            manifest_raw_path=files["manifest_raw_path"],
            descriptor_path=files["descriptor_path"],
            repo_digests_path=files["repo_digests_path"],
            expected_index_digest_path=files["expected_index_digest_path"],
            image=files["image"],
        )

    def test_rejects_a_single_manifest_push(self):
        files = self._single_inputs()

        with self.assertRaisesRegex(RegistryImageError, "not a multi-platform image index"):
            self._verify_manifest(files)

    def test_index_mode_binds_the_index_digest_and_selects_both_platform_manifests(self):
        files = self._index_inputs()

        selection = self._verify_manifest(files)

        self.assertEqual(selection.mode, "index")
        self.assertEqual(selection.manifest_digest, files["index_digest"])
        self.assertEqual(
            selection.platform_manifest_digests,
            {"amd64": files["platform_digest"], "arm64": files["arm64_platform_digest"]},
        )
        self.assertEqual(
            verify_image_manifest(
                manifest_raw_path=files["image_manifest_raw_path"],
                expected_digest=files["platform_digest"],
            ),
            CONFIG_DIGEST,
        )
        self.assertEqual(
            verify_image_manifest(
                manifest_raw_path=files["arm64_image_manifest_raw_path"],
                expected_digest=files["arm64_platform_digest"],
            ),
            ARM64_CONFIG_DIGEST,
        )
        self.assertNotEqual(selection.manifest_digest, CONFIG_DIGEST)

    def test_rejects_an_index_without_exactly_the_two_supported_platforms(self):
        amd64_raw = _raw(_image_manifest())
        arm64_raw = _raw(_image_manifest(config_digest=ARM64_CONFIG_DIGEST))
        amd64_entry = _platform_entry(digest=_digest(amd64_raw), architecture="amd64")
        arm64_entry = _platform_entry(digest=_digest(arm64_raw), architecture="arm64")
        attestation_entry = _attestation_entry(digest=OTHER_DIGEST, reference_digest=amd64_entry["digest"])
        extra_platform_entry = _platform_entry(digest=ATTESTATION_DIGEST, architecture="ppc64le")
        cases = {
            "arm64 platform missing": [amd64_entry, attestation_entry],
            "amd64 platform missing": [arm64_entry, attestation_entry],
            "unsupported extra platform": [amd64_entry, arm64_entry, extra_platform_entry],
            "duplicate platform": [amd64_entry, {**amd64_entry, "digest": ATTESTATION_DIGEST}, arm64_entry],
            "no platform manifests": [attestation_entry],
        }
        for label, entries in cases.items():
            with self.subTest(case=label):
                files = self._index_inputs(
                    index_document={
                        "schemaVersion": 2,
                        "mediaType": "application/vnd.oci.image.index.v1+json",
                        "manifests": entries,
                    }
                )
                with self.assertRaisesRegex(RegistryImageError, "must contain exactly the linux/amd64 and linux/arm64"):
                    self._verify_manifest(files)

    def test_rejects_an_index_digest_that_is_not_the_qualified_build(self):
        files = self._index_inputs(expected_index_digest=OTHER_DIGEST)

        with self.assertRaisesRegex(RegistryImageError, "does not match the qualified build"):
            self._verify_manifest(files)

    def test_rejects_an_image_manifest_that_does_not_hash_to_the_index_entry(self):
        files = self._index_inputs()
        tampered = self._write("tampered.json", _raw(_image_manifest(config_digest=OTHER_DIGEST)))

        with self.assertRaisesRegex(RegistryImageError, "hashes to"):
            verify_image_manifest(
                manifest_raw_path=tampered,
                expected_digest=files["platform_digest"],
            )

    def test_rejects_a_reported_digest_that_differs_from_the_served_manifest(self):
        files = self._index_inputs(reported_digest=OTHER_DIGEST)

        with self.assertRaisesRegex(RegistryImageError, "registry resolved"):
            self._verify_manifest(files)

    def test_rejects_a_push_record_without_the_served_manifest_digest(self):
        files = self._index_inputs(repo_digest_entries=f"{IMAGE}@{OTHER_DIGEST}\n")

        with self.assertRaisesRegex(RegistryImageError, "not the served manifest digest"):
            self._verify_manifest(files)

    def test_ignores_repository_digest_lines_for_other_names(self):
        files = self._index_inputs(repo_digest_entries=f"itambox@{OTHER_DIGEST}\nghcr.io/other/app@{OTHER_DIGEST}\n")
        files["repo_digests_path"].write_text(
            files["repo_digests_path"].read_text(encoding="utf-8") + f"{IMAGE}@{files['index_digest']}\n",
            encoding="utf-8",
        )

        selection = self._verify_manifest(files)

        self.assertEqual(selection.manifest_digest, files["index_digest"])

    def test_rejects_malformed_digests_in_every_input(self):
        def malformed_index_entry():
            return self._index_inputs(
                index_document={
                    "schemaVersion": 2,
                    "mediaType": "application/vnd.oci.image.index.v1+json",
                    "manifests": [
                        {
                            "mediaType": "application/vnd.oci.image.manifest.v1+json",
                            "digest": "sha256:short",
                            "size": 1234,
                            "platform": {"architecture": "amd64", "os": "linux"},
                        }
                    ],
                }
            )

        cases = {
            "descriptor": lambda: self._index_inputs(reported_digest="sha256:not-hex"),
            "expected index digest": lambda: self._index_inputs(expected_index_digest="deadbeef"),
            "index entry": malformed_index_entry,
        }
        for label, build in cases.items():
            with self.subTest(input=label):
                with self.assertRaisesRegex(RegistryImageError, "sha256 content digest"):
                    self._verify_manifest(build())

    def test_rejects_a_platform_manifest_with_a_malformed_configuration_digest(self):
        raw = _raw(_image_manifest(config_digest="sha256:short"))

        with self.assertRaisesRegex(RegistryImageError, "sha256 content digest"):
            verify_image_manifest(
                manifest_raw_path=self._write("bad-config.json", raw),
                expected_digest=_digest(raw),
            )

    def test_rejects_a_platform_manifest_without_a_configuration_descriptor(self):
        raw = _raw({"schemaVersion": 2})

        with self.assertRaisesRegex(RegistryImageError, "no image configuration descriptor"):
            verify_image_manifest(
                manifest_raw_path=self._write("no-config.json", raw),
                expected_digest=_digest(raw),
            )

    def test_rejects_missing_digest_inputs(self):
        cases = {
            "no repository digests": lambda: self._index_inputs(repo_digest_entries="\n"),
            "empty manifest descriptor": lambda: self._index_inputs(reported_digest=""),
        }
        for label, build in cases.items():
            with self.subTest(input=label):
                with self.assertRaises(RegistryImageError):
                    self._verify_manifest(build())

    def test_rejects_a_tagged_or_unqualified_image_name(self):
        for image in (f"{IMAGE}:1.0.0-beta.2", f"{IMAGE}@sha256:{'ab' * 32}", "itambox-webapp"):
            with self.subTest(image=image):
                files = self._index_inputs(image=image)
                with self.assertRaisesRegex(RegistryImageError, "untagged repository reference|fully-qualified"):
                    self._verify_manifest(files)

    def test_cli_prints_the_workflow_outputs_and_fails_closed(self):
        files = self._index_inputs()
        argv = [
            "verify-manifest",
            "--manifest-raw",
            str(files["manifest_raw_path"]),
            "--descriptor",
            str(files["descriptor_path"]),
            "--repo-digests",
            str(files["repo_digests_path"]),
            "--expected-index-digest",
            str(files["expected_index_digest_path"]),
            "--image",
            files["image"],
        ]

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(argv)

        self.assertEqual(status, 0)
        self.assertEqual(
            stdout.getvalue().splitlines(),
            [
                "MODE=index",
                f"GHCR_MANIFEST_DIGEST={files['index_digest']}",
                f"IMAGE_MANIFEST_DIGEST_AMD64={files['platform_digest']}",
                f"IMAGE_MANIFEST_DIGEST_ARM64={files['arm64_platform_digest']}",
            ],
        )
        self.assertIn("verification passed", stderr.getvalue())

        rejected = self._index_inputs(expected_index_digest=OTHER_DIGEST)
        argv[argv.index("--expected-index-digest") + 1] = str(rejected["expected_index_digest_path"])
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(argv)

        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("registry image verification failed", stderr.getvalue())

    def test_cli_verify_image_prints_the_configuration_digest_and_fails_closed(self):
        files = self._index_inputs()
        argv = [
            "verify-image",
            "--manifest-raw",
            str(files["image_manifest_raw_path"]),
            "--expected-digest",
            files["platform_digest"],
        ]

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(argv)

        self.assertEqual(status, 0)
        self.assertEqual(stdout.getvalue().splitlines(), [f"IMAGE_CONFIG_DIGEST={CONFIG_DIGEST}"])

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(
                [
                    "verify-image",
                    "--manifest-raw",
                    str(self._write("empty.json", b"{}")),
                    "--expected-digest",
                    OTHER_DIGEST,
                ]
            )

        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("registry image verification failed", stderr.getvalue())

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(
                [
                    "verify-image",
                    "--manifest-raw",
                    str(files["arm64_image_manifest_raw_path"]),
                    "--expected-digest",
                    files["arm64_platform_digest"],
                ]
            )

        self.assertEqual(status, 0)
        self.assertEqual(stdout.getvalue().splitlines(), [f"IMAGE_CONFIG_DIGEST={ARM64_CONFIG_DIGEST}"])


if __name__ == "__main__":
    unittest.main()
