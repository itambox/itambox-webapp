"""Behaviour tests for the release SBOM gate.

The gate runs on a bare interpreter inside the release workflow, so these tests
are standard-library-only and exercise the real validation paths: a valid SPDX
document bound to the release image passes, and every way of describing the
wrong (or no) image fails closed.
"""

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scripts.release_sbom import (
    SbomValidationError,
    main,
    read_image_identity,
    validate_sbom,
    verify_release_sbom,
)

IMAGE_ID = "sha256:" + "ab12cd34" * 8
IMAGE_REF = "itambox:1.0.0-beta.2"


def _spdx_document(
    *,
    image_id: str = IMAGE_ID,
    image_ref: str = IMAGE_REF,
    package_count: int = 3,
    spdx_version: str = "SPDX-2.3",
):
    root_package = {
        "name": image_ref,
        "SPDXID": "SPDXRef-Package-root",
        "versionInfo": image_id,
        "externalRefs": [
            {
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": f"pkg:oci/itambox@{image_id}",
            }
        ],
    }
    extra_packages = [
        {"name": f"component-{index}", "SPDXID": f"SPDXRef-Package-{index}", "versionInfo": "1.0"}
        for index in range(1, package_count)
    ]
    return {
        "spdxVersion": spdx_version,
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": image_ref,
        "documentNamespace": "https://trivy.dev/test-document",
        "creationInfo": {"creators": ["Tool: trivy"], "created": "2026-09-27T00:00:00Z"},
        "packages": [root_package, *extra_packages],
    }


def _inspect_document(*, image_id: str = IMAGE_ID, repo_tags=(IMAGE_REF,)):
    return [{"Id": image_id, "RepoTags": list(repo_tags), "Config": {"Labels": {}}}]


def _write_json(directory: Path, name: str, value) -> Path:
    path = directory / name
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(json.dumps(value), encoding="utf-8")
    return path


class SbomValidationTests(unittest.TestCase):
    def test_valid_spdx_document_reports_summary_and_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sbom = _write_json(Path(temp_dir), "image.sbom.spdx.json", _spdx_document())

            summary = validate_sbom(sbom, expected_image_id=IMAGE_ID, expected_image_ref=IMAGE_REF)

            self.assertEqual(summary.spdx_version, "SPDX-2.3")
            self.assertEqual(summary.document_name, IMAGE_REF)
            self.assertEqual(summary.package_count, 3)
            self.assertEqual(summary.sha256, hashlib.sha256(sbom.read_bytes()).hexdigest())

    def test_non_json_and_non_object_documents_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            cases = {
                "truncated.json": b"{not json",
                "array.json": b"[]",
                "string.json": b'"a string"',
            }
            for name, payload in cases.items():
                with self.subTest(name=name):
                    path = _write_json(directory, name, payload)
                    with self.assertRaises(SbomValidationError):
                        validate_sbom(path)

    def test_missing_spdx_identity_is_rejected(self):
        document = _spdx_document()
        del document["spdxVersion"]
        with tempfile.TemporaryDirectory() as temp_dir:
            sbom = _write_json(Path(temp_dir), "sbom.json", document)
            with self.assertRaisesRegex(SbomValidationError, "spdxVersion"):
                validate_sbom(sbom)

        document = _spdx_document()
        document["SPDXID"] = ""
        with tempfile.TemporaryDirectory() as temp_dir:
            sbom = _write_json(Path(temp_dir), "sbom.json", document)
            with self.assertRaisesRegex(SbomValidationError, "SPDXID"):
                validate_sbom(sbom)

    def test_empty_or_unnamed_packages_are_rejected(self):
        document = _spdx_document()
        document["packages"] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            sbom = _write_json(Path(temp_dir), "sbom.json", document)
            with self.assertRaisesRegex(SbomValidationError, "no packages"):
                validate_sbom(sbom)

        document = _spdx_document()
        document["packages"] = [{"SPDXID": "SPDXRef-Package-anon"}]
        with tempfile.TemporaryDirectory() as temp_dir:
            sbom = _write_json(Path(temp_dir), "sbom.json", document)
            with self.assertRaisesRegex(SbomValidationError, "no names"):
                validate_sbom(sbom)

    def test_package_minimum_is_enforced(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sbom = _write_json(Path(temp_dir), "sbom.json", _spdx_document(package_count=2))
            with self.assertRaisesRegex(SbomValidationError, "below the required minimum"):
                validate_sbom(sbom, min_packages=5)

    def test_wrong_image_digest_or_reference_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sbom = _write_json(Path(temp_dir), "sbom.json", _spdx_document())
            with self.assertRaisesRegex(SbomValidationError, "image digest"):
                validate_sbom(sbom, expected_image_id="sha256:" + "0" * 64)
            with self.assertRaisesRegex(SbomValidationError, "image itambox:"):
                validate_sbom(sbom, expected_image_ref="itambox:9.9.9")

    def test_missing_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(SbomValidationError, "cannot read SBOM"):
                validate_sbom(Path(temp_dir) / "absent.json")


class ImageIdentityTests(unittest.TestCase):
    def test_reads_id_and_tags_from_docker_inspect_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            inspect_path = _write_json(Path(temp_dir), "image-inspect.json", _inspect_document())

            identity = read_image_identity(inspect_path)

        self.assertEqual(identity.image_id, IMAGE_ID)
        self.assertEqual(identity.repo_tags, (IMAGE_REF,))

    def test_malformed_inspection_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            for name, payload in {
                "empty.json": json.dumps([]),
                "no-id.json": json.dumps([{"RepoTags": [IMAGE_REF]}]),
                "bad-id.json": json.dumps([{"Id": "not-a-digest"}]),
                "bad-tags.json": json.dumps([{"Id": IMAGE_ID, "RepoTags": [42]}]),
            }.items():
                with self.subTest(name=name):
                    path = _write_json(directory, name, payload.encode("utf-8"))
                    with self.assertRaises(SbomValidationError):
                        read_image_identity(path)


class VerifyReleaseSbomTests(unittest.TestCase):
    def test_bound_sbom_passes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            sbom = _write_json(directory, "sbom.json", _spdx_document())
            inspect_path = _write_json(directory, "inspect.json", _inspect_document())

            summary = verify_release_sbom(sbom, inspect_path, IMAGE_REF)

        self.assertEqual(summary.document_name, IMAGE_REF)

    def test_inspected_image_with_another_reference_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            sbom = _write_json(directory, "sbom.json", _spdx_document())
            inspect_path = _write_json(directory, "inspect.json", _inspect_document(repo_tags=("itambox:other",)))

            with self.assertRaisesRegex(SbomValidationError, "expected release reference"):
                verify_release_sbom(sbom, inspect_path, IMAGE_REF)

    def test_sbom_describing_another_image_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            other_id = "sha256:" + "ff" * 32
            sbom = _write_json(directory, "sbom.json", _spdx_document(image_id=other_id, image_ref="itambox:other"))
            inspect_path = _write_json(directory, "inspect.json", _inspect_document())

            with self.assertRaises(SbomValidationError):
                verify_release_sbom(sbom, inspect_path, IMAGE_REF)


class CliTests(unittest.TestCase):
    def test_cli_verify_reports_success_for_a_bound_sbom(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            sbom = _write_json(directory, "itambox-1.0.0-beta.2.sbom.spdx.json", _spdx_document())
            inspect_path = _write_json(directory, "image-inspect.json", _inspect_document())
            output = io.StringIO()

            with redirect_stdout(output):
                status = main(
                    [
                        "verify",
                        "--sbom",
                        str(sbom),
                        "--image-inspect",
                        str(inspect_path),
                        "--image-ref",
                        IMAGE_REF,
                    ]
                )

        self.assertEqual(status, 0)
        self.assertIn("Release SBOM valid", output.getvalue())
        self.assertIn(IMAGE_REF, output.getvalue())

    def test_cli_verify_fails_closed_and_names_the_reason(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            sbom = _write_json(directory, "sbom.json", _spdx_document())
            inspect_path = _write_json(directory, "inspect.json", _inspect_document(repo_tags=("itambox:wrong",)))
            error = io.StringIO()

            with redirect_stderr(error):
                status = main(
                    [
                        "verify",
                        "--sbom",
                        str(sbom),
                        "--image-inspect",
                        str(inspect_path),
                        "--image-ref",
                        IMAGE_REF,
                    ]
                )

        self.assertEqual(status, 1)
        self.assertIn("release SBOM validation failed", error.getvalue())


if __name__ == "__main__":
    unittest.main()
