"""Behaviour tests for the container registry publication gate.

The gate runs on a bare interpreter inside the release workflow, so these tests
are standard-library-only and exercise the real verification paths for both
image store models: the classic single-manifest push and the containerd image
store (Docker 29 default) index push that bundles the platform image manifest
with build-time attestation manifests. Every way of describing a different
image, a different digest, or a malformed digest fails closed.
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
LAYER_DIGEST = "sha256:" + "11" * 32
OTHER_DIGEST = "sha256:" + "ab" * 32


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


def _index(*, image_manifest_digest: str, extra_entries: list | None = None) -> dict:
    entries = [
        {
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "digest": image_manifest_digest,
            "size": 1234,
            "platform": {"architecture": "amd64", "os": "linux"},
        },
        {
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "digest": OTHER_DIGEST,
            "size": 837,
            "annotations": {
                "vnd.docker.reference.digest": image_manifest_digest,
                "vnd.docker.reference.type": "attestation-manifest",
            },
            "platform": {"architecture": "unknown", "os": "unknown"},
        },
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
        local_image_id: str = CONFIG_DIGEST,
        image: str = IMAGE,
    ) -> dict:
        """Write the inputs describing a classic single-manifest push."""
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
            "local_image_id_path": self._write("local-image-id.txt", local_image_id),
            "image_manifest_raw_path": self._write("manifest-copy.json", raw),
            "manifest_digest": digest,
            "image": image,
        }

    def _index_inputs(
        self,
        *,
        platform_manifest: dict | None = None,
        index_document: dict | None = None,
        reported_digest: str | None = None,
        repo_digest_entries: str | None = None,
        local_image_id: str | None = None,
        image: str = IMAGE,
    ) -> dict:
        """Write the inputs describing a containerd image store index push."""
        if platform_manifest is None:
            platform_manifest = _image_manifest()
        platform_raw = _raw(platform_manifest)
        platform_digest = _digest(platform_raw)
        if index_document is None:
            index_document = _index(image_manifest_digest=platform_digest)
        index_raw = _raw(index_document)
        index_digest = _digest(index_raw)
        if reported_digest is None:
            reported_digest = index_digest
        if repo_digest_entries is None:
            repo_digest_entries = f"{image}@{index_digest}\n"
        if local_image_id is None:
            local_image_id = index_digest
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
            "local_image_id_path": self._write("local-image-id.txt", local_image_id),
            "image_manifest_raw_path": self._write("image-manifest.json", platform_raw),
            "index_digest": index_digest,
            "platform_digest": platform_digest,
            "image": image,
        }

    def _verify_manifest(self, files: dict):
        return verify_manifest(
            manifest_raw_path=files["manifest_raw_path"],
            descriptor_path=files["descriptor_path"],
            repo_digests_path=files["repo_digests_path"],
            local_image_id_path=files["local_image_id_path"],
            image=files["image"],
        )

    def test_single_manifest_mode_binds_the_configuration_digest(self):
        files = self._single_inputs()

        selection = self._verify_manifest(files)

        self.assertEqual(selection.mode, "single")
        self.assertEqual(selection.manifest_digest, files["manifest_digest"])
        self.assertEqual(selection.image_manifest_digest, files["manifest_digest"])
        config_digest = verify_image_manifest(
            manifest_raw_path=files["image_manifest_raw_path"],
            expected_digest=selection.image_manifest_digest,
        )
        self.assertEqual(config_digest, CONFIG_DIGEST)

    def test_single_manifest_mode_accepts_the_containerd_image_id(self):
        files = self._single_inputs()
        files["local_image_id_path"] = self._write("local-image-id-containerd.txt", files["manifest_digest"])

        selection = self._verify_manifest(files)

        self.assertEqual(selection.mode, "single")
        self.assertEqual(selection.manifest_digest, files["manifest_digest"])

    def test_rejects_a_single_manifest_matching_neither_local_identity(self):
        files = self._single_inputs(local_image_id=OTHER_DIGEST)

        with self.assertRaisesRegex(RegistryImageError, "do not match the qualified local image"):
            self._verify_manifest(files)

    def test_index_mode_binds_the_index_digest_and_selects_the_platform_manifest(self):
        files = self._index_inputs()

        selection = self._verify_manifest(files)

        self.assertEqual(selection.mode, "index")
        self.assertEqual(selection.manifest_digest, files["index_digest"])
        self.assertEqual(selection.image_manifest_digest, files["platform_digest"])
        config_digest = verify_image_manifest(
            manifest_raw_path=files["image_manifest_raw_path"],
            expected_digest=selection.image_manifest_digest,
        )
        self.assertEqual(config_digest, CONFIG_DIGEST)
        self.assertNotEqual(selection.manifest_digest, config_digest)

    def test_rejects_an_index_without_exactly_one_platform_manifest(self):
        extra = {
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "digest": OTHER_DIGEST,
            "size": 1234,
            "platform": {"architecture": "arm64", "os": "linux"},
        }
        cases = {
            "two platform manifests": self._index_inputs(
                index_document=_index(image_manifest_digest=OTHER_DIGEST, extra_entries=[extra])
            ),
            "no platform manifest": self._index_inputs(
                index_document={
                    "schemaVersion": 2,
                    "mediaType": "application/vnd.oci.image.index.v1+json",
                    "manifests": [
                        {
                            "mediaType": "application/vnd.oci.image.manifest.v1+json",
                            "digest": OTHER_DIGEST,
                            "size": 837,
                            "platform": {"architecture": "unknown", "os": "unknown"},
                        }
                    ],
                }
            ),
        }
        for label, files in cases.items():
            with self.subTest(case=label):
                with self.assertRaisesRegex(RegistryImageError, "exactly one platform image manifest"):
                    self._verify_manifest(files)

    def test_rejects_an_index_digest_that_is_not_the_local_image(self):
        files = self._index_inputs(local_image_id=OTHER_DIGEST)

        with self.assertRaisesRegex(RegistryImageError, "not the qualified local image"):
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
        cases = {
            "descriptor": lambda: self._index_inputs(reported_digest="sha256:not-hex"),
            "local image id": lambda: self._index_inputs(local_image_id="deadbeef"),
            "index entry": lambda: self._index_inputs(index_document=_index(image_manifest_digest="sha256:short")),
            "manifest config": lambda: self._single_inputs(
                image_manifest=_image_manifest(config_digest="sha256:short")
            ),
        }
        for label, build in cases.items():
            with self.subTest(input=label):
                with self.assertRaisesRegex(RegistryImageError, "sha256 content digest"):
                    self._verify_manifest(build())

    def test_rejects_missing_digest_inputs(self):
        cases = {
            "no repository digests": lambda: self._index_inputs(repo_digest_entries="\n"),
            "no configuration descriptor": lambda: self._single_inputs(image_manifest={"schemaVersion": 2}),
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
            "--local-image-id",
            str(files["local_image_id_path"]),
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
                f"IMAGE_MANIFEST_DIGEST={files['platform_digest']}",
            ],
        )
        self.assertIn("verification passed", stderr.getvalue())

        rejected = self._index_inputs(local_image_id=OTHER_DIGEST)
        argv[argv.index("--local-image-id") + 1] = str(rejected["local_image_id_path"])
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


if __name__ == "__main__":
    unittest.main()
