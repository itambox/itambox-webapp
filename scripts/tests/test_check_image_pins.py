import unittest

from scripts.check_image_pins import scan_dockerfile, scan_yaml_images


class ImagePinPolicyTests(unittest.TestCase):
    def test_tag_and_digest_references_pass(self):
        digest = "a" * 64
        dockerfile = f"FROM python:3.12-slim-bookworm@sha256:{digest} AS runtime\n"
        compose = f"services:\n  db:\n    image: postgres:16@sha256:{digest}\n"

        self.assertEqual([], scan_dockerfile("Dockerfile", dockerfile))
        self.assertEqual([], scan_yaml_images("docker-compose.yml", compose))

    def test_unpinned_from_and_image_fail_with_path_and_line(self):
        dockerfile_findings = scan_dockerfile("Dockerfile", "# stage\nFROM node:26-slim AS frontend\n")
        workflow_findings = scan_yaml_images(
            ".github/workflows/ci.yml",
            "jobs:\n  test:\n    services:\n      postgres:\n        image: postgres:16\n",
        )

        self.assertEqual(1, len(dockerfile_findings))
        self.assertIn("Dockerfile:2:", dockerfile_findings[0])
        self.assertEqual(1, len(workflow_findings))
        self.assertIn(".github/workflows/ci.yml:5:", workflow_findings[0])


if __name__ == "__main__":
    unittest.main()
