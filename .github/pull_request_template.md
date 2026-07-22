## Summary

<!-- What changed, why it is needed, and the user or operator impact. -->

## Related issue

<!-- Use `Closes #123`, `Fixes #123`, or link the relevant discussion. -->

## Change type

- [ ] Feature
- [ ] Bug fix
- [ ] Security hardening
- [ ] Documentation
- [ ] Refactor or maintenance
- [ ] Breaking prerelease change

## Verification

<!-- Check the gates that apply and mark the rest N/A in the notes below. -->

- [ ] Relevant `uv run --locked --group dev pytest ...` targets pass
- [ ] `uv run --locked --only-group dev python scripts/check_flake8_baseline.py` passes
- [ ] `uv run --locked --group dev pre-commit run --all-files` passes
- [ ] `uv run --locked --group dev python itambox/manage.py makemigrations --check --dry-run` passes
- [ ] Frontend build, typecheck, and ESLint pass when frontend files changed
- [ ] `uv run --locked --only-group docs mkdocs build -f itambox/mkdocs.yml --strict` passes when documentation changed
- [ ] Playwright or production Compose smoke tests pass when applicable
- [ ] New or changed behavior has regression coverage

### Commands and manual checks

```text
List the exact commands and manual verification performed.
```

## Compatibility, deployment, and rollback

<!-- Describe migrations, configuration changes, API/route compatibility, downtime, and rollback. Write "None" when not applicable. -->

## UI evidence

<!-- Add screenshots or a short recording for visible changes. Remove this section when not applicable. -->

## Documentation and changelog

- [ ] User/operator documentation is updated or not required
- [ ] `CHANGELOG.md` is updated for user-visible, operational, compatibility, or security changes
