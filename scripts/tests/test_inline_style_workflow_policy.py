import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class InlineStyleWorkflowPolicyTests(unittest.TestCase):
    def test_make_target_runs_the_policy_gate(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

        self.assertIn("inline-style-check", makefile)
        self.assertIn("python scripts/check_inline_styles.py", makefile)

    def test_pre_commit_runs_the_policy_gate_without_filenames(self):
        config = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")

        self.assertIn("id: inline-style-policy", config)
        self.assertIn("entry: uv run --locked --group dev python scripts/check_inline_styles.py", config)
        self.assertIn("id: inline-style-policy\n        name: CSP inline styles (blocking policy gate)", config)

    def test_ci_has_a_separate_inline_style_job(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        self.assertIn("  inline-style:\n", workflow)
        self.assertIn("- name: Check CSP inline-style policy", workflow)
        self.assertIn("uv run --locked --no-sync python scripts/check_inline_styles.py", workflow)


if __name__ == "__main__":
    unittest.main()
