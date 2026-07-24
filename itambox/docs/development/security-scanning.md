# Security scanning policy

ITAMbox uses GitHub-native features and checksum-verified OSS scanners. No paid external scanner or external account is required for a green build.

## Gates and coverage

The `Security` workflow runs for every pull request and `main` push, weekly, and on manual dispatch.

| Gate | Canonical input | Blocking policy |
|---|---|---|
| Python dependencies | synchronized `pyproject.toml` and `uv.lock`; `uv lock --check` must pass | unsuppressed `HIGH` or `CRITICAL` |
| Frontend dependencies | `itambox/package-lock.json` | unsuppressed `HIGH` or `CRITICAL` |
| E2E dependencies | `itambox/tests/e2e/package-lock.json` | unsuppressed `HIGH` or `CRITICAL` |
| Secrets in a PR/push | every introduced commit in the exact base-to-head range | every unsuppressed finding |
| Repository history | all reachable commits on the weekly/manual run | every unsuppressed finding |
| Release image | the exact locally built rehearsal/draft candidate | unsuppressed, fix-available `HIGH` or `CRITICAL` |

Scanner, vulnerability-database, report-parser, and suppression-policy failures block rather than degrading to best effort. Image findings for which the operating-system or package vendor has not published a fix are re-evaluated on every run but do not block an otherwise unremediable release. `MEDIUM`, `LOW`, and `UNKNOWN` vulnerabilities do not currently block. GitHub Secret Scanning and Push Protection remain complementary provider-aware controls.

Trivy `0.72.0` and Gitleaks `8.30.1` are downloaded from their public OSS releases and verified against literal SHA-256 digests in `scripts/install_security_tools.sh`. Dependabot monitors Python, both npm roots, and GitHub Actions.

## Result visibility

This repository is public, so normal Actions logs and artifacts are treated as public.

- Trivy and Gitleaks write raw JSON, stdout, and stderr only below `$RUNNER_TEMP`.
- Scanner shell tracing is disabled.
- Logs contain only aggregate severity counts and generic pass/fail messages.
- Redacted Gitleaks reports are never uploaded as artifacts or SARIF.
- Unsuppressed dependency findings are converted after policy evaluation to SARIF and retained in GitHub Code Scanning, whose security-alert permissions govern access. Fork and Dependabot pull requests skip this write-only upload; their merge gate still runs, and `main` retains the result after merge.
- Raw dependency and image reports are not uploaded as normal workflow artifacts.
- Fork pull requests receive no additional secrets; the workflow never uses `pull_request_target`.

If a scanner fails, inspect the ephemeral runner through a controlled reproduction. Do not change the workflow to print or upload raw secret findings.

## Governed suppressions

`security/suppressions.json` is the only scanner-suppression source. Native broad ignore files such as `.trivyignore` and Gitleaks allowlists are not accepted.

Every suppression must contain:

```json
{
  "id": "SEC-2026-001",
  "tool": "trivy",
  "finding": "CVE-YYYY-NNNN",
  "reason": "Why the finding is not currently exploitable and why remediation is deferred.",
  "owner": "@itambox/security",
  "scope": {
    "target": "uv.lock",
    "package": "example",
    "version": "1.2.3"
  },
  "review_on": "2026-08-15",
  "expires_on": "2026-09-15",
  "references": ["https://github.com/itambox/itambox-webapp/issues/123"]
}
```

Trivy scope is the exact target, package, and installed version. Gitleaks scope is the exact stable fingerprint, path, and rule ID. Wildcard Gitleaks scopes are rejected. Suppressions:

- require a meaningful reason and GitHub user/team owner;
- require review and expiry dates;
- may not expire more than 90 days from validation;
- fail when review is overdue or the entry is expired, duplicated, malformed, or overly broad;
- must be introduced and reviewed in the same pull request as the risk decision.

A suppression is temporary risk acceptance, not remediation. Remove it as soon as the finding no longer exists. Confirmed active credentials must be revoked and removed from history where appropriate, never suppressed.

Validate policy locally:

```bash
uv run --locked --only-group dev python scripts/security_gate.py validate
uv run --locked --only-group dev python -m unittest scripts.tests.test_security_gate
```

## Release integration

The release workflow builds each candidate once, verifies its OCI identity, and runs Trivy against that same local image before creating a draft release or tag-associated release object. A failed image gate prevents archive creation and `gh release create`.

The pull-request rehearsal has read-only repository permission and publishes nothing. Manual draft preparation remains restricted to a reviewed `main` commit as documented in the [release runbook](release-checklist.md).

## Repository enforcement

The stable check names are `Security / policy`, `Security / dependencies`, and `Security / secrets`. Repository rules should require these checks and pull requests for `main`; tag rules should prevent moving or deleting published `v*` tags. Workflow failures enforce scanner policy, while repository rules are the separate control that prevents privileged users from bypassing failed checks.
