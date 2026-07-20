# Release checklist

Use this checklist when preparing a future tagged release. No tagged release or container-publishing workflow exists yet, so establish and review the release plan before creating the first tag.

## Pre-release

- [ ] The canonical gates under "Run the checks" in the repository-root `CONTRIBUTING.md` pass, including lint, smoke, full tests, and Playwright.
- [ ] No open critical/security issues on the milestone
- [ ] Documentation and module-maturity labels match the tagged code.
- [ ] If the REST API changed, generate and review its schema with `python itambox/manage.py spectacular --file schema.yaml` from the repository root.
- [ ] Documentation builds cleanly with `mkdocs build -f itambox/mkdocs.yml --strict`.
- [ ] A source-built production Compose smoke test passes from a clean checkout of the release candidate.

## Version bump

- [ ] Update `itambox/itambox/release.py` to `VERSION = "X.Y.Z"`.
- [ ] Update `pyproject.toml` metadata to the same version.
- [ ] Search for the previous version and review every remaining occurrence before tagging.

## Changelog

- [ ] Add `## [X.Y.Z] - YYYY-MM-DD` section to `CHANGELOG.md`
- [ ] Fill in `Added`, `Changed`, `Fixed`, `Security` sub-sections from `git log` since last tag
- [ ] Replace the `Unreleased` comparison link with the new tag range and verify all repository links.
- [ ] Commit: `chore(release): bump version to X.Y.Z`

## Tag

```bash
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z
```

## Distribution

The repository does not currently publish a container image. Do not document `docker compose pull` or a registry image until a registry, immutable tag policy, provenance process, and publishing workflow are implemented and tested.

## Post-release

- [ ] Create a GitHub Release from the tag and use the matching changelog section as release notes.
- [ ] Verify the tag from a clean checkout with the documented source-build installation path.
- [ ] Confirm that README, installation, upgrade, and security pages describe the newly published artifacts and support status accurately.
- [ ] Close the milestone
