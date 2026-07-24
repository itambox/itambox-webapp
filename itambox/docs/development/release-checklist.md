# Release runbook

ITAMbox releases are prepared by `.github/workflows/release.yml`. The workflow deliberately separates an unprivileged pull-request rehearsal from the privileged preparation of a **draft** GitHub Release. Nothing in this workflow publishes a release or pushes an image to a registry automatically.

## Version policy

Public releases use Semantic Versioning with a `v` tag prefix:

```text
v1.0.0-alpha.1 -> v1.0.0-alpha.2 -> v1.0.0-beta.1 -> v1.0.0-rc.1 -> v1.0.0
```

Prerelease identifiers are always dotted. Forms such as `alpha1`, `beta2`, and `rc3` are invalid. Python tooling may display the equivalent PEP 440 identities (`1.0.0a1`, `1.0.0b1`, or `1.0.0rc1`), but they identify the same release.

A candidate version must agree across:

- `pyproject.toml`
- `itambox/itambox/release.py`
- the virtual `itambox` package in `uv.lock` (PEP 440 form)
- the current-version notice in `README.md`
- a dated `CHANGELOG.md` section and its release link

The private `itambox/package.json` belongs to the static-asset build toolchain, is not published, and keeps an independent package identity. It is deliberately not an application release-version source.

Validate a candidate locally from the repository root:

```bash
uv run --locked --only-group dev python scripts/release_policy.py verify \
  --version 1.0.0-alpha.1
uv run --locked --only-group dev python scripts/release_policy.py notes \
  --version 1.0.0-alpha.1
```

## Pull-request rehearsal

A pull request that changes release metadata, the Dockerfile, release documentation, policy code, or the release workflow runs the `rehearsal` job with read-only repository permissions. It:

1. validates version and changelog consistency;
2. builds the complete production image without pushing it;
3. verifies the image's OCI version, revision, and source labels;
4. applies the [security scanning policy](security-scanning.md) to that exact local image.

The rehearsal has no release token and cannot create tags, releases, packages, or repository changes. It is the safe, non-publishing dry run for every release change.

## Prepare a draft release

### Preconditions

- [ ] The release change was reviewed and merged into `main`.
- [ ] The canonical CI, Docker smoke, and Playwright workflows are green for that commit.
- [ ] No unresolved security issue blocks the milestone.
- [ ] The candidate has a dated changelog section.
- [ ] Operator, upgrade, compatibility, and module-maturity documentation matches the candidate.
- [ ] Database migrations and rollback implications have been reviewed.

The privileged job accepts only `workflow_dispatch` runs on `refs/heads/main`. It additionally asks the GitHub API to prove that the exact release commit is the result of a merged pull request targeting `main`. Direct or arbitrary branch commits are rejected even if somebody can dispatch the workflow.

Start preparation in the GitHub Actions UI, or with:

```bash
gh workflow run release.yml --ref main -f version=1.0.0-alpha.1
gh run watch --exit-status
```

The workflow then:

1. revalidates the requested version and reviewed commit;
2. builds the production image from that exact commit;
3. verifies immutable OCI labels and applies the blocking image scan;
4. retains a compressed Docker image and SHA-256 checksum as a workflow artifact;
5. atomically creates the provisional tag, refusing a conflicting target;
6. verifies the remote tag before and after draft creation;
7. creates a draft GitHub Release with changelog-derived notes and attaches the image archive.

Prerelease versions receive GitHub's prerelease flag. The draft does not publish the release, create a public compatibility promise, or push the image to a registry. The workflow creates the requested lightweight tag with an atomic Git push and fails closed if origin cannot resolve it or it does not point to the reviewed commit. Treat that ref as provisional until publication.

## Review and promote

Before publishing the draft:

- [ ] Download the image archive and verify its SHA-256 checksum.
- [ ] Load it with `docker load` and repeat the production Compose smoke test.
- [ ] Confirm `org.opencontainers.image.version`, `revision`, and `source` against the reviewed commit.
- [ ] Review generated release notes and attached assets.
- [ ] Confirm backup, migration, downgrade, and support notes.
- [ ] Confirm the tag name uses dotted SemVer, matches the draft exactly, and points to the reviewed commit.

Publish the reviewed draft in GitHub only after these checks. Release and asset immutability applies only after publication; never move or silently replace the published tag or assets.

Promotion is always a new reviewed release change. Never rename or rewrite an existing tag:

- alpha to alpha: increment the alpha number;
- alpha to beta: start at `beta.1`;
- beta to release candidate: start at `rc.1`;
- release candidate to stable: remove the prerelease suffix;
- post-release fixes: increment patch and repeat the full process.

## Rollback and failed preparation

Before publication, rollback is non-disruptive:

1. delete the draft GitHub Release;
2. remove the associated provisional tag created by the workflow, after confirming that no release was published;
3. delete the workflow artifact if it should no longer be retained;
4. fix the release change in a new pull request;
5. rerun the rehearsal and draft preparation.

No registry image is published by this workflow. If a release was already published, do **not** move or overwrite its tag and do not replace immutable assets silently. Mark the release as affected, document the impact, and ship a new patch/prerelease version. Application rollback follows the backup and restore runbook and must account for irreversible database migrations.

## Current distribution boundary

The automated artifact is a source-built Docker image archive attached to the draft. ITAMbox does not yet claim a mutable `latest` image or automatic registry publication. Registry publication requires a separately reviewed immutable-tag, signing, provenance, retention, and rollback policy.
