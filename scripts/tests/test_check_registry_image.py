"""Behaviour tests for the container registry publication gate.

The gate runs on a bare interpreter inside the release workflow, so these tests
are standard-library-only and exercise the real verification paths: a registry
manifest that matches the qualified local image passes, and every way of
describing a different image, a different digest, or a malformed digest fails
closed.
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
    verify_registry_image,
)

IMAGE = "ghcr.io/itambox/itambox-webapp"
CONFIG_DIGEST = "sha256:" + "cd" * 32
LAYER_DIGEST = "sha256:" + "11" * 32
OTHER_DIGEST = "sha256:" + "ab" * 32


def _manifest_document(*, config_digest: str = CONFIG_DIGEST) -> dict:
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

    def _qualified_inputs(
        self,
        *,
        manifest_document: dict | None = None,
        reported_digest: str | None = None,
        repo_digest_entries: str | None = None,
        local_config_digest: str = CONFIG_DIGEST,
        image: str = IMAGE,
    ) -> dict:
        """Write the four verification inputs describing a published image."""
        if manifest_document is None:
            manifest_document = _manifest_document()
        raw = json.dumps(manifest_document).encode("utf-8")
        manifest_digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        if reported_digest is None:
            reported_digest = manifest_digest
        if repo_digest_entries is None:
            repo_digest_entries = f"{image}@{manifest_digest}\n"
        return {
            "manifest_raw_path": self._write("manifest.json", raw),
            "descriptor_path": self._write(
                "descriptor.json",
                {"mediaType": manifest_document.get("mediaType"), "digest": reported_digest, "size": len(raw)},
            ),
            "repo_digests_path": self._write("repo-digests.txt", repo_digest_entries),
            "local_config_digest_path": self._write("local-config-digest.txt", local_config_digest),
            "image": image,
            "manifest_digest": manifest_digest,
        }

    def _verify(self, files: dict):
        return verify_registry_image(
            manifest_raw_path=files["manifest_raw_path"],
            descriptor_path=files["descriptor_path"],
            repo_digests_path=files["repo_digests_path"],
            local_config_digest_path=files["local_config_digest_path"],
            image=files["image"],
        )

    def test_binds_the_published_manifest_to_the_qualified_local_image(self):
        files = self._qualified_inputs()

        identity = self._verify(files)

        self.assertEqual(identity.image, IMAGE)
        self.assertEqual(identity.manifest_digest, files["manifest_digest"])
        self.assertEqual(identity.config_digest, CONFIG_DIGEST)

    def test_accepts_a_recorded_push_digest_among_multiple_entries(self):
        files = self._qualified_inputs()
        files["repo_digests_path"] = self._write(
            "repo-digests-2.txt",
            f"{IMAGE}@{files['manifest_digest']}\n{IMAGE}@{OTHER_DIGEST}\n",
        )

        identity = self._verify(files)

        self.assertEqual(identity.manifest_digest, files["manifest_digest"])

    def test_rejects_a_reported_digest_that_differs_from_the_served_manifest(self):
        files = self._qualified_inputs(reported_digest=OTHER_DIGEST)

        with self.assertRaisesRegex(RegistryImageError, "registry resolved"):
            self._verify(files)

    def test_rejects_a_manifest_config_that_is_not_the_local_image(self):
        files = self._qualified_inputs(manifest_document=_manifest_document(config_digest=OTHER_DIGEST))

        with self.assertRaisesRegex(RegistryImageError, "not the qualified local image"):
            self._verify(files)

    def test_rejects_a_push_record_without_the_served_manifest_digest(self):
        files = self._qualified_inputs(repo_digest_entries=f"{IMAGE}@{OTHER_DIGEST}\n")

        with self.assertRaisesRegex(RegistryImageError, "not the served manifest digest"):
            self._verify(files)

    def test_rejects_malformed_digests_in_every_input(self):
        cases = {
            "descriptor": {"reported_digest": "sha256:not-hex"},
            "local config": {"local_config_digest": "deadbeef"},
            "manifest config": {"manifest_document": _manifest_document(config_digest="sha256:short")},
        }
        for label, overrides in cases.items():
            with self.subTest(input=label):
                files = self._qualified_inputs(**overrides)
                with self.assertRaisesRegex(RegistryImageError, "sha256 content digest"):
                    self._verify(files)

    def test_rejects_missing_digest_inputs(self):
        cases = {
            "no repository digests": {"repo_digest_entries": "\n"},
            "no configuration descriptor": {"manifest_document": {"schemaVersion": 2}},
            "empty manifest descriptor": {"reported_digest": ""},
        }
        for label, overrides in cases.items():
            with self.subTest(input=label):
                files = self._qualified_inputs(**overrides)
                with self.assertRaises(RegistryImageError):
                    self._verify(files)

    def test_rejects_digest_lines_from_a_foreign_repository(self):
        files = self._qualified_inputs(repo_digest_entries=f"ghcr.io/other/app@{OTHER_DIGEST}\n")

        with self.assertRaisesRegex(RegistryImageError, "does not belong"):
            self._verify(files)

    def test_rejects_a_tagged_or_unqualified_image_name(self):
        for image in (f"{IMAGE}:1.0.0-beta.2", f"{IMAGE}@sha256:{'ab' * 32}", "itambox-webapp"):
            with self.subTest(image=image):
                files = self._qualified_inputs(image=image)
                with self.assertRaisesRegex(RegistryImageError, "untagged repository reference|fully-qualified"):
                    self._verify(files)

    def test_cli_prints_the_workflow_outputs_and_fails_closed(self):
        files = self._qualified_inputs()
        argv = [
            "verify",
            "--manifest-raw",
            str(files["manifest_raw_path"]),
            "--descriptor",
            str(files["descriptor_path"]),
            "--repo-digests",
            str(files["repo_digests_path"]),
            "--local-config-digest",
            str(files["local_config_digest_path"]),
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
                f"GHCR_MANIFEST_DIGEST={files['manifest_digest']}",
                f"IMAGE_CONFIG_DIGEST={CONFIG_DIGEST}",
            ],
        )
        self.assertIn("matches the qualified image", stderr.getvalue())

        rejected = self._qualified_inputs(local_config_digest=OTHER_DIGEST)
        argv[argv.index("--local-config-digest") + 1] = str(rejected["local_config_digest_path"])
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(argv)

        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("registry image verification failed", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
