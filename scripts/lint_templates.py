"""Run djLint over the complete tracked authored-template inventory.

The same inventory is used by Make, CI, and the mandatory pre-commit hook. Git
pathspecs are kept here because shell glob semantics differ between Git Bash,
Ubuntu, and pre-commit's command runner.
"""

from __future__ import annotations

import subprocess
import sys

TEMPLATE_PATHS = (
    "itambox/**/templates/**/*.html",
    "itambox/**/templates/*.html",
    "itambox/templates/*.html",
)


def tracked_template_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", *TEMPLATE_PATHS],
        check=True,
        capture_output=True,
    )
    return [path for path in result.stdout.decode().split("\0") if path]


def main() -> int:
    files = tracked_template_files()
    if not files:
        print("No tracked Django templates matched the configured inventory.", file=sys.stderr)
        return 1
    return subprocess.run(["djlint", *sys.argv[1:], *files], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
