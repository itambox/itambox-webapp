# Optional capability fallbacks

This page is the bounded fallback contract for the optional-capability
slice. It documents places where an optional extension, deployment probe, or
optional dependency may be unavailable without taking down ITAMbox core.

A fallback is not permission to hide an unexpected failure. Each row below has
an explicit boundary, a safe degraded result, and a test. The common safety
rule is:

> An optional fallback may remove an optional capability or contribution. It
> must never weaken authentication, authorization, tenant isolation, or
> cryptographic validation.

Capability and plugin diagnostics are operator-visible, but their shape is
closed. They publish stable identifiers and exception types, not credentials,
request payloads, response bodies, or raw exception messages.

## Fallback matrix

| Boundary | Degraded result | Why swallowing is safe | Evidence |
|---|---|---|---|
| `CapabilityRegistry.unresolved_references()` in `itambox/capabilities.py` | A late ownership-resolution failure becomes an `UnresolvedReference` row containing the capability, reference, and exception type. Startup continues. | A stale dotted reference is a declaration/documentation defect, not an execution or authorization gate. The exception message is not copied into the row. | `itambox/tests/test_capabilities.py` checks unresolved references and that an arbitrary resolver failure exposes only its type. |
| `CapabilityRegistry.state()` in `itambox/capabilities.py` | An application activation-probe failure returns `active=False`, `value_present=False`, and `probe_error=<ExceptionType>`. | Probes are observational: they read configuration or deployment rows and do not authorize or execute an operation. Unknown optional state is reported inactive. Construction rejects a `security_critical` capability with a deactivatable probe. | `itambox/tests/test_capabilities.py` covers fail-closed state, wrong probe types, and redaction; `itambox/tests/test_capability_slices.py` asserts that only authorization-boundary capabilities are security-critical and always-on. |
| `platform.plugins` probe in `itambox/apps.py` | A plugin that failed isolation is excluded from the effective active state. The Experimental capability remains opt-in and can report inactive. | The probe describes the optional plugin system; it is not an authorization gate. Core authentication, RBAC, tenant scoping, and crypto do not depend on this state. | `itambox/tests/test_capability_slices.py` covers the operator activation source; `core/tests/test_plugins.py` covers failed-plugin isolation and diagnostics. |
| `load_plugins()` in `itambox/plugins/utils.py` | Import, configuration, compatibility, or middleware errors disable only the affected configured plugin. Loading continues for other plugins. | Plugin activation is operator opt-in. The failed plugin's middleware, apps, and registry contributions are removed; Stable core and other plugins continue. No core security path is replaced by the fallback. | `core/tests/test_plugins.py` covers import/configuration/compatibility failures, multi-plugin isolation, registry cleanup, and redaction. |
| `PluginConfig.ready()` in `itambox/plugins/__init__.py` | A plugin lifecycle exception marks that plugin inactive, records a diagnostic, removes its registrations, and returns from the hook. | A plugin lifecycle hook is an optional composition point. Its failure cannot abort Stable core or disable another plugin. | `core/tests/test_plugins.py::PluginLoaderTestCase::test_one_failed_plugin_does_not_disable_another`. |
| Plugin REST router in `itambox/plugins/urls.py` | A failed viewset import or registration records an `api` diagnostic and leaves the remaining router usable. | Only an optional plugin route is lost. Core API routing and its tenant/permission boundaries remain intact. | `core/tests/test_plugins.py::PluginLoaderTestCase::test_rest_router_failure_isolated_from_plugin_router_startup`. |
| Plugin GraphQL composition in `core/schema.py` | A failed optional GraphQL schema import is recorded as a `graphql` diagnostic; the core schema is still constructed. | Only the opt-in schema contribution is removed. Core GraphQL authentication, tenant middleware, permissions, complexity limits, and mutation boundaries remain in force. | `core/tests/test_plugins.py::PluginLoaderTestCase::test_graphql_schema_failure_isolated_from_core_schema_startup`, plus the GraphQL security suite. |
| Plugin UI URL loading in `core/urls.py` | An absent exact `plugin.urls` module means that plugin has no optional UI URLconf. A failure inside an existing URLconf is isolated and diagnosed as `urls`. | An optional UI contribution may be missing, but the root URLconf and core routes remain available. Only the exact missing module is treated as absence; dependency/import failures are not silently mistaken for an absent URLconf. | `core/tests/test_plugins.py::PluginLoaderTestCase::test_plugin_urlconf_failure_isolated_from_core_url_startup` and `test_missing_optional_plugin_urlconf_is_not_reported_as_a_failure`. |
| `plugin_template_content()` in `core/templatetags/plugins.py` | A plugin template-hook error becomes a user-visible HTML comment while other registered plugin content still renders. | Template content is presentation-only. This intentional degradation keeps a plugin from breaking a core page; it does not alter authentication, authorization, tenant, or crypto behavior. Plugin-authored content remains subject to the CSP and escaping rules. | `core/tests/test_plugins.py::TemplateTagTestCase::test_template_tag_rendering_and_error_handling`. |
| `is_plugin_active()` in `itambox/plugins/runtime.py` | During an app-registry transition, a plugin already present in `PLUGINS_ACTIVE` but missing an `AppConfig` is treated as active. | The settings-derived active list is a prerequisite, so an unconfigured plugin cannot become active through this fallback. Later schema/UI boundaries still isolate missing configuration. | `core/tests/test_plugins.py::PluginLoaderTestCase::test_active_settings_fallback_does_not_activate_an_unconfigured_plugin`. |
| `validate_file_attachment()` in `core/validators.py` | If `python-magic`/libmagic cannot be imported or classify bytes, MIME is treated as unknown and the dangerous-extension blacklist remains the gate. | The validator never trusts the client-supplied `Content-Type`. Known dangerous extensions are rejected without libmagic. This fallback does not claim full byte-level classification; production full-integration deployments should provide libmagic. | `core/tests/test_security.py` and `core/tests/test_upload_hardening.py` cover extension gates and the missing-libmagic client-MIME contract. |
| `validate_image_attachment()` in `core/validators.py` | If libmagic is unavailable, Pillow verifies and identifies the image. If Pillow cannot decode it, the MIME remains unknown and validation fails. | The extension allowlist plus actual image decoding is fail-closed. Client-supplied `Content-Type` is never used to turn invalid bytes into an accepted image. | `core/tests/test_security.py::SecurityHardeningTests::test_image_validation_falls_back_to_pillow_when_magic_fails`. |

## Security boundary

The following are not optional capability fallbacks and must not be made
silent:

- authentication and authorization decisions;
- tenant lookup, tenant scoping, or object-level permission checks;
- token, password, SAML/OIDC, LDAP, or other credential validation;
- encryption, signature, webhook-secret, or untrusted-redirect validation.

A failing optional probe may make a status banner or optional menu disappear,
but it may not make a security boundary appear to succeed. The registry
reinforces this by refusing a `security_critical` capability that is not
`always-on`, and by publishing only exception types for probe failures.

## User-visible degradation

Plugin failure is intentionally visible through `PLUGINS_DIAGNOSTICS`, the
plugin diagnostics context processor, and the plugin management command. A
failed plugin is not silently presented as healthy: it is disabled, its
contributions are removed, and the operator can see the stage and typed failure
class. Template-hook failures are the narrower presentation exception: the
page remains usable and the hook failure is represented in the rendered
response as an HTML comment.

## SCIM truthfulness

SCIM `ServiceProviderConfig` must advertise exactly what the implementation
supports. The WP-18 contract test
`users/tests/test_scim.py::SCIMServiceProviderConfigAccuracyTests::test_bearer_is_advertised_and_httpbasic_is_not`
asserts that Bearer is advertised and the unimplemented HTTP Basic scheme is
not. No SCIM source or test change is needed for this slice.

## Scope notes

This page deliberately does not reclassify generic missing-app filter/table
helpers or the optional OpenAPI schema component. LDAP, background tasks,
importers, integration adapters, and event delivery belong to other
slices. The `platform.plugins` declaration and its published limitations remain
unchanged; this page documents fallback safety rather than changing the
capability's 1.x limitation text.
