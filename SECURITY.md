# Security Policy

We take the security of ITAMbox seriously. If you discover a vulnerability, please report it using the instructions below.

---

## Supported Versions

ITAMbox has not yet reached a tagged release. The current version metadata, `1.0.0-alpha1`, describes unreleased development on `main`; there is no supported release line or guaranteed remediation timeline yet. Reports against the current source are still welcome and help maintainers assess prerelease risk. See [CHANGELOG.md](CHANGELOG.md) for release status.

| Target | Status |
|---|---|
| `main` (`1.0.0-alpha1`) | Pre-release; reports accepted without a fix-time guarantee |
| Tagged releases | None published |

---

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

If you discover a security concern (e.g., credential exposure, multi-tenant bypass, symmetric decryption issues), report it privately:

1. Email [security@itambox.dev](mailto:security@itambox.dev) with the subject prefix `[ITAMbox Security]`.
2. Provide a clear description of the vulnerability, including:
   - Steps or proof-of-concept code to reproduce the issue.
   - The potential impact of the vulnerability.
   - The specific version of ITAMbox affected.
3. Maintainers will assess reproducibility, impact, and next steps as availability permits. Response and remediation times are not guaranteed during the prerelease period.

Please avoid public disclosure while a report is being assessed. If a coordinated disclosure is feasible, the reporter and maintainers can agree on timing after the issue and remediation path are understood.
