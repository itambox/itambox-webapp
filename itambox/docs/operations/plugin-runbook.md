# Plugin Removal and Recovery Runbook

Plugins are Experimental, trusted, in-process code. This runbook is for a deployment operator. ITAMbox 1.0 does not provide install, uninstall, upgrade, runtime enable/disable, package-management, or automatic orphan-data cleanup orchestration.

## Check the current state

1. Record the deployed ITAMbox revision and the plugin package revision.
2. Run:

   ```bash
   uv run --locked --no-dev python manage.py plugins
   uv run --locked --no-dev python manage.py plugins --format json
   ```

   A failed plugin row identifies the package, failure class, startup stage,
   compatibility outcome, activation state, `settings.PLUGINS` as the source,
   and whether a configured value was present. Error text is redacted and
   suitable for an operator report. Never copy secrets into an incident ticket.
3. Confirm that the application is serving Stable core and that the failed
   plugin's routes, middleware, API router, GraphQL contribution, menu, and
   template hooks are absent.

## Disable or remove a plugin

1. Remove the package name from `settings.PLUGINS` or `ITAMBOX_PLUGINS` in the
   deployment configuration. This is the only 1.0 activation control.
2. Keep the package revision and configuration under version control until the
   retention/reinstall decision is complete. Do not attempt a runtime toggle.
3. Restart the application using the deployment's normal, reversible restart
   procedure. Verify `manage.py plugins` reports the plugin as not configured.
4. Preserve the plugin's database tables, Django `ContentType` rows, changelog
   rows, and referencing configuration. Removing it from `PLUGINS` does **not**
   remove any of these. This orphan-data behavior is intentional in 1.0 so that
   a later reinstall can recover the data.

There is no supported automatic cleanup command. If permanent deletion is
approved later, take a database backup, identify every plugin-owned table,
ContentType, changelog/reference row, and configuration entry, and perform the
cleanup as a separately reviewed migration or database operation. Do not infer
ownership from table names alone, and do not run destructive SQL as part of
normal plugin removal.

## Recover a failed plugin

1. Keep the failed plugin disabled while investigating. Stable core should not
   be made dependent on a broken Experimental plugin.
2. Compare the plugin's declared `min_version`/`max_version` (ITAMbox product
   compatibility) and `min_plugin_api_version`/`max_plugin_api_version` (host
   plugin API compatibility) with the deployed revision. These are independent
   checks; do not reinterpret product-version bounds as API-version bounds.
3. Fix the package, configuration, middleware, URL, or schema error in a
   development/staging deployment. Pin the tested plugin revision.
4. Re-add the package name to `PLUGINS` only during the planned deployment
   change, restart, and verify:

   ```bash
   uv run --locked --no-dev python manage.py plugins
   uv run --locked --no-dev python manage.py check
   ```

5. Exercise the plugin's supported routes and extension points, plus the Stable
   core smoke/test subset. A plugin that fails again is disabled again; it does
   not abort the application startup.

## Security boundary

A plugin has the same process privileges, database access, filesystem access,
and ability to add middleware as the host application. There is no sandbox,
capability restriction, signature verification, or tenant-level activation.
Installing or enabling one is equivalent to installing trusted Python code.
Only deployment operators may change `PLUGINS`; ordinary tenant users cannot
activate, disable, or clear these diagnostics.
