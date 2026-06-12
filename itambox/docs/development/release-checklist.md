# Release Checklist

Use this checklist for every tagged release.

## Pre-release

- [ ] All tests pass: `pytest -q --no-cov`
- [ ] No open critical/security issues on the milestone
- [ ] `docs/` is up to date for any new features
- [ ] API schema regenerated: `python manage.py spectacular --file schema.yaml` (commit if changed)
- [ ] Docs build clean: `cd itambox && mkdocs build --strict` (run from repo root; output goes to `static/docs/`)

## Version bump

- [ ] Update `itambox/release.py` → `VERSION = "X.Y.Z"`
- [ ] `pyproject.toml` `version` field matches (update manually — no build tooling sync yet)
- [ ] Verify `GET /api/status/` returns the new version

## Changelog

- [ ] Add `## [X.Y.Z] - YYYY-MM-DD` section to `CHANGELOG.md`
- [ ] Fill in `Added`, `Changed`, `Fixed`, `Security` sub-sections from `git log` since last tag
- [ ] Commit: `chore(release): bump version to X.Y.Z`

## Tag

```bash
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z
```

## Build & publish image

```bash
docker build -t itambox:X.Y.Z -t itambox:latest .
docker push itambox:X.Y.Z
docker push itambox:latest
```

## Post-release

- [ ] Refresh demo instance: `docker compose pull && docker compose exec app python manage.py migrate`
- [ ] Create GitHub Release from the tag; paste CHANGELOG section as release notes
- [ ] Close the milestone
