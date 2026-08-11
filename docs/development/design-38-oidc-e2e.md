# Deterministic positive OIDC end-to-end coverage

## Status

This document is the implementation design for the remaining positive-OIDC slice of
[issue #38](https://github.com/itambox/itambox-webapp/issues/38).

It is intentionally implementation-ready but contains no implementation.

PR [#258](https://github.com/itambox/itambox-webapp/pull/258) delivered the negative-contract and
tenant-isolation slice and explicitly deferred a deterministic external provider until a bounded
provider contract was selected.

The selected provider is NAV's `mock-oauth2-server`, run as a digest-pinned GitHub Actions service
container.

The selected test surface is one successful authorization-code login for one seeded tenant and one
new deterministic user, followed by UI assertions for tenant binding and JIT provisioning.

## Objectives

1. Close the remaining issue #38 acceptance criterion with a real browser redirect through an OIDC
   provider process.
2. Exercise the application/provider boundary used by `mozilla-django-oidc` 5.x without turning the
   E2E job into a general IdP compatibility suite.
3. Prove that a successful callback creates the expected User, AssetHolder, Membership, and mapped
   role and leaves the browser authenticated in the initiating tenant.
4. Keep all three existing negative OIDC contracts intact.
5. Make failures deterministic, local to the CI runner, and diagnosable from Playwright traces and
   provider/application logs.
6. Avoid adding application runtime code or a test-only OIDC implementation to ITAMbox.

## Evidence and citation convention

Repository observations in this design cite the exact file and line range inspected on main at
`c901c471f3efe58c4d42c6cd03d444862bb2a18d`.

Normative statements use **must**, **shall**, or **will** and describe the follow-up implementation;
they are decisions, not claims that the code already behaves that way.

External provider facts link to primary project documentation in
[External references](#external-references).

## Current repository baseline

### OIDC runtime

The direct dependency range is `mozilla-django-oidc>=5.0.2,<6.0.0`
(`pyproject.toml:35`), and the lock currently resolves 5.0.2 (`uv.lock:1567-1574`).

The tenant-aware implementation consists of `TenantOIDCSettingsMixin`, `TenantOIDCBackend`,
`TenantOIDCAuthorizeView`, and `TenantOIDCCallbackView`
(`itambox/core/auth/oidc.py:97-135`, `itambox/core/auth/oidc.py:406-459`).

The application exposes a global initiation route, a tenant initiation route, and one shared
callback (`itambox/core/urls.py:238-245`).

Tenant OIDC JSON is parsed from `ITAMBOX_TENANT_OIDC_CONFIGS`; malformed JSON or a non-object fails
settings loading with `ImproperlyConfigured` (`itambox/core/settings/base.py:390-400`).

That loader runs while base settings are imported, including the OIDC assignment at
`itambox/core/settings/base.py:436-445`.

Therefore the CI value must exist before the first `manage.py`, Django check, migration, seed, shell,
or server process starts.

Usable tenant OIDC entries require client ID, client secret, authorization endpoint, token endpoint,
user endpoint, and issuer (`itambox/core/auth/providers.py:31-38`).

For an RSA or EC signing algorithm, provider discovery also requires either a static IdP signing key
or a JWKS endpoint (`itambox/core/auth/providers.py:145-157`).

Only usable configs whose key matches a live tenant are rendered, and their action targets the
tenant-aware initiation route (`itambox/core/auth/providers.py:67-78`).

The login template renders those actions as `Sign in with <provider name>` links
(`itambox/templates/registration/login.html:61-74`).

### Token boundary

The tenant settings mixin reads exact or lowercase keys from the active tenant config and otherwise
falls back to global settings (`itambox/core/auth/oidc.py:104-127`).

Its local defaults are RS256 and `openid email profile`
(`itambox/core/auth/oidc.py:115-121`).

`TenantOIDCBackend.verify_token` delegates signature and nonce verification to the upstream backend
before applying ITAMbox's additional checks (`itambox/core/auth/oidc.py:140-151`).

The client ID must occur in `aud`, an `azp` claim must equal that client ID when present, and `iss`
must exactly equal the configured issuer (`itambox/core/auth/oidc.py:153-172`).

The positive fixture must consequently mint a signed RS256 token with the configured issuer,
`aud` containing the client ID, and `azp` equal to the client ID.

### Tenant and session boundary

Authorize dispatch resolves the tenant and stores its slug in `oidc_tenant_slug`
(`itambox/core/auth/oidc.py:427-443`).

Callback dispatch restores that tenant from the same browser session before delegating to the
upstream callback (`itambox/core/auth/oidc.py:446-452`).

Successful login stores the tenant primary key in `active_tenant_id`
(`itambox/core/auth/oidc.py:454-459`).

The rendered top bar exposes the active tenant name in `.workspace-switcher-name`
(`itambox/templates/global_includes/_topbar.html:45-61`).

OIDC logins are exempt from local TOTP enforcement because MFA is delegated to the SSO provider
(`itambox/core/settings/base.py:373-377`; enforced via `is_password_login_session` in
`itambox/core/mfa.py:46-58` and the middleware gate in `itambox/core/otp_middleware.py:66-79`).

### JIT boundary

User lookup matches email first and then the derived username
(`itambox/core/auth/oidc.py:196-207`).

The derived username is email, then subject, then a fallback
(`itambox/core/auth/oidc.py:209-213`).

JIT user creation consumes email, `given_name`, and `family_name`
(`itambox/core/auth/oidc.py:215-230`).

Profile sync links or creates an AssetHolder by UPN/email in the initiating tenant
(`itambox/core/auth/oidc.py:315-351`).

Group mapping resolves a single highest-priority role in the order Admin, Manager, Member and falls
back to Member (`itambox/core/auth/oidc.py:353-380`).

Membership provisioning reuses a live tenant role when it exists and otherwise may create one;
the current setting default for privileged role auto-creation is true
(`itambox/core/auth/provisioning.py:51-75`).

The seed fixture creates `Administrator`, not `Admin`, as its full-permission local role
(`itambox/core/management/commands/_seed/access.py:324-345`).

The CI fixture must therefore deliberately create a live `Admin` role before the browser flow,
rather than making the test depend on implicit privileged-role creation.

The AssetHolder list is filterable and backed by `AssetHolderTable`
(`itambox/organization/views/assetholder_views.py:35-46`).

Its free-text query includes first name, last name, UPN, and email
(`itambox/organization/filters.py:228-248`).

Its table declares `email` but does not render it in `default_columns`; the visible identity
columns are UPN, first name, last name, and tenant (`itambox/organization/tables.py:192-221`).

The Membership list is filterable and backed by `MembershipTable`
(`itambox/organization/views/membership_views.py:42-81`).

Its free-text query includes user username, user email, role name, and tenant name
(`itambox/organization/filters.py:379-392`).

Its table declares the linked user, tenant, active state, and rendered live role grants
(`itambox/organization/tables.py:349-400`); the tenant column is excluded while a single active
tenant is selected (`itambox/organization/views/membership_views.py:156-165`).

Those two UI lists are therefore the chosen black-box assertion surface for the JIT result.
Assertions use the visible AssetHolder UPN column (the fixture UPN equals the email) and derive
Membership tenant binding from the active workspace plus the tenant-scoped URL, never from a hidden
cell.

### Existing E2E boundary

The current SSO/SCIM spec gets required values through `requiredEnv`, uses the configured `baseURL`,
and creates an authenticated API context for SCIM (`itambox/tests/e2e/spec/07-sso-scim.spec.ts:3-17`,
`itambox/tests/e2e/spec/07-sso-scim.spec.ts:89-101`).

It already asserts unknown-tenant initiation, an uninitiated callback, and a provider-error callback
(`itambox/tests/e2e/spec/07-sso-scim.spec.ts:114-141`,
`itambox/tests/e2e/spec/07-sso-scim.spec.ts:367-398`).

It contains no successful provider redirect or successful OIDC callback
(`itambox/tests/e2e/spec/07-sso-scim.spec.ts:1-399`).

Playwright runs this suite serially with one worker; CI retries a failed test twice
(`itambox/tests/e2e/playwright.config.ts:5-9`).

The default project uses the authenticated global storage state
(`itambox/tests/e2e/playwright.config.ts:11-18`).

The positive OIDC test must therefore create its own browser context with `baseURL` and no storage
state, just as the provider-error contract creates a separate context
(`itambox/tests/e2e/spec/07-sso-scim.spec.ts:367-398`).

### Current CI boundary

The E2E job runs on `ubuntu-latest` and already defines PostgreSQL as a service
(`.github/workflows/e2e.yml:15-33`).

Its job environment fixes the development settings, seeded tenant slug, isolation tenant, and base
URL before any Django command runs (`.github/workflows/e2e.yml:35-52`).

It migrates, runs `seed_data --force`, provisions the E2E superuser/isolation tenant/SCIM token,
starts Django on `127.0.0.1:8000`, and finally runs `npm test`
(`.github/workflows/e2e.yml:130-222`).

Failed runs already upload Playwright traces (`.github/workflows/e2e.yml:240-250`).

The preflight script currently validates the local credential, tenant, isolation, group, and SCIM
inputs but has no OIDC-provider contract (`itambox/tests/e2e/preflight-check.mjs:59-97`).

The workflow policy test pins explicit status assertions and the three negative OIDC cases
(`itambox/core/tests/test_e2e_workflow.py:81-112`).

## Decision summary

| Area | Decision | Reason |
|---|---|---|
| Provider | NAV `mock-oauth2-server` 6.0.0 container pinned by digest | Small, scriptable OIDC surface with discovery, JWKS, interactive authorization-code login, and no application code |
| Placement | GitHub Actions service beside PostgreSQL | Starts before Django, has job-scoped lifecycle, and needs no custom process cleanup |
| Signing | Ephemeral provider RS256 key served by JWKS | Exercises the production-style JWKS path while keeping private key material out of the repository |
| Claims | One exact subject/email/name/groups fixture; `aud`/`azp` mapped explicitly, `iss` pinned via the discovery assertion and tenant config | Covers strict validation and JIT without a provider matrix |
| Browser scope | One positive test in the existing SSO/SCIM spec | Preserves manifest shape and keeps the complete flow in one browser context |
| Role scope | One mapped Admin case; pre-provision the Admin role | Proves group mapping without testing implicit privileged-role creation |
| Logout and variants | Defer | They do not close the remaining acceptance criterion and are covered at lower layers or by existing session-error tests |
| Dependency | CI image only; no Python package or uv dependency | The mock is infrastructure, not application/test-library code |

## Provider choice

### Premise correction

The project named `mock-oauth2-server` is not a Python package with YAML configuration.

The maintained project is NAV's JVM/Docker mock server, and its supported container configuration is
the `JSON_CONFIG` environment variable.

The similarly purposed Python project is named `oidc-provider-mock`, not
`mock-oauth2-server`.

This design evaluates the requested `mock-oauth2-server` candidate by its actual upstream artifact
and configuration surface.

### Option A — NAV `mock-oauth2-server`

**Pros**

- Provides OIDC discovery, authorization, token, userinfo, and JWKS endpoints.
- Provides an interactive login page suitable for a real Playwright redirect.
- Allows exact deterministic request-to-claim mappings through one JSON configuration.
- Runs as one public container with no database or realm import.
- Supports RS256 and a live JWKS/key-ID path.
- Has a dedicated liveness endpoint.
- Uses the permissive MIT license.
- Is intentionally a mock server, so its configuration vocabulary is smaller than a production IdP.
- Has no locale-sensitive fixture import, email delivery, or external network dependency after image
  pull.

**Cons**

- It is a JVM service, so startup and memory are greater than an in-process Python stub.
- Its signing key is generated at process startup unless a custom key is supplied.
- Its interactive form is intentionally simple and is not representative of a branded enterprise
  login screen.
- JSON embedded in workflow YAML requires careful quoting and a policy test to prevent drift.
- The correction from the issue's “Python/YAML” description must be made explicit in review.

### Option B — Dex container

**Pros**

- Is a production-grade OpenID Connect identity service rather than a protocol stub.
- Supports static clients, a local password connector, groups, discovery, and JWKS.
- Uses YAML configuration and a single Go service.
- Uses the permissive Apache-2.0 license.
- More closely resembles a deployed IdP's client and connector configuration.

**Cons**

- Requires client, connector, static password hash, storage, and claim/scope configuration for one
  synthetic user.
- Introduces connector behavior that ITAMbox does not intend to test.
- Exact custom claim shaping is less direct than the mock server's request mapping.
- Adds more configuration and upgrade surface than the remaining acceptance criterion needs.
- A failure can arise in Dex connector/realm behavior even when the RP contract is correct.

### Option C — Keycloak container

**Pros**

- Is a widely deployed production IdP with realistic client, realm, user, role, and mapper behavior.
- Supports discovery, JWKS, authorization code, nonce/state, groups, and detailed client policy.
- Supports deterministic realm import.
- Uses the permissive Apache-2.0 license.

**Cons**

- Has the highest startup time and memory cost of the candidates.
- Requires a realm import containing a user, credentials, client, redirect URI, groups, and protocol
  mappers.
- Its large administration and persistence surface is unrelated to the bounded RP contract.
- Version upgrades can change login markup and realm schema, creating E2E churn.
- Playwright selectors would couple the test to a full IdP product UI rather than the protocol seam.

### Option D — in-repository custom mock

**Pros**

- Can expose only the exact endpoints and HTML selectors the test needs.
- Can use a checked-in deterministic signing key and start quickly.
- Adds no third-party service runtime.
- Can be made fully independent of upstream UI changes.

**Cons**

- ITAMbox would own authorization-code issuance, one-time code exchange, nonce propagation, JWT
  signing, JWKS formatting, userinfo, expiry, and error behavior.
- A mock and the relying party could share the same protocol misunderstanding and pass together.
- Security-sensitive signing code would become test infrastructure maintained in the application
  repository.
- It would add application-adjacent code solely to close one E2E acceptance criterion.
- It creates a new test surface that needs its own tests, dependency policy, and security review.

### Comparative fit

| Criterion | NAV mock server | Dex | Keycloak | Custom mock |
|---|---|---|---|---|
| CI startup/resource cost | Low-to-medium JVM process | Medium Go service plus connector/config | High JVM service plus realm bootstrap | Low runtime, high engineering cost |
| Deterministic claim shaping | Direct exact request mapping | Possible, with connector/scope configuration | Possible, with realm groups and protocol mappers | Entirely under repository control |
| Authorization-code browser realism | Protocol-realistic, deliberately simple login | Production-IdP behavior | Production-IdP behavior | Only as realistic as the implementation made here |
| `mozilla-django-oidc` 5.x seam | Discovery, JWKS, code, token, nonce, userinfo | Full OIDC seam | Full OIDC seam | Must be implemented and kept compatible locally |
| RSA signing choice | Generated RS256 key plus JWKS, or custom key | Managed signing key plus JWKS | Realm signing key plus JWKS | Repository must generate or own key material |
| Configuration/maintenance | One JSON object and pinned image | Client, connector, user, storage, scopes | Realm, client, user, groups, mappers | Endpoints, state/code store, JWT/JWKS, UI, tests |
| License | MIT | Apache-2.0 | Apache-2.0 | ITAMbox repository license |
| Linux CI fit | Direct container service | Direct container service | Direct but heavier container service | Depends on chosen local runtime |
| Windows/local fit | Docker Desktop or compatible container runtime; not a CI dependency | Same | Same, with more resources | Must be made cross-platform by maintainers |
| Locale sensitivity | ASCII JSON and a fixed semantic form selector | Static password/UI text can add coupling | Login theme and imported realm can add coupling | Entirely controlled, but entirely owned |

The production IdPs are more realistic at their administration layer, but that layer is outside the
RP behavior selected here.

The NAV mock reaches the same discovery/JWKS/authorization-code/token/userinfo seam while minimizing
unrelated setup.

### Recommendation

Use NAV `mock-oauth2-server` 6.0.0 as a GitHub Actions service container.

Pin both the immutable version and the multi-platform manifest digest:

```text
ghcr.io/navikt/mock-oauth2-server:6.0.0@sha256:b9fa251aefee22a97c32534d23a1c400f01dbd483ab263b013d89f6d60d96691
```

The generated RSA key is intentionally not byte-for-byte deterministic.

The deterministic contract is that every fresh job exposes a valid RS256 JWKS and mints claims with
the exact configured values.

Do not check a private signing key into the repository merely to make the JWKS bytes constant.

## Bounded provider integration contract

### Contract boundary

The E2E slice shall assert only the following provider surface:

1. The provider becomes live locally.
2. Its discovery document identifies the expected issuer and the four endpoint URLs (authorization,
   token, userinfo, JWKS).
3. Its JWKS contains at least one RSA signing key usable for RS256.
4. Its authorization endpoint presents the deterministic interactive login form.
5. It returns an authorization code to ITAMbox's shared callback with the originating state.
6. Its token endpoint exchanges that code and returns an RS256 ID token.
7. Its userinfo endpoint returns the deterministic identity and group claims.
8. ITAMbox accepts the signature, nonce, audience, authorized party, and issuer.

The slice shall not assert provider administration APIs, refresh tokens, logout endpoints,
introspection, revocation, dynamic client registration, consent, password policy, MFA, PKCE variants,
or multiple issuers.

### Canonical mock configuration

The service's `JSON_CONFIG` shall be semantically identical to this object:

```json
{
  "interactiveLogin": true,
  "httpServer": "NettyWrapper",
  "tokenProvider": {
    "keyProvider": {
      "algorithm": "RS256"
    }
  },
  "tokenCallbacks": [
    {
      "issuerId": "itambox-e2e",
      "tokenExpiry": 300,
      "requestMappings": [
        {
          "requestParam": "subject",
          "match": "itambox-e2e-oidc-user",
          "claims": {
            "sub": "itambox-e2e-oidc-user",
            "email": "e2e.oidc@itambox.local",
            "given_name": "E2E",
            "family_name": "OIDC",
            "groups": [
              "e2e-oidc-admins"
            ],
            "aud": [
              "${clientId}"
            ],
            "azp": "${clientId}"
          }
        }
      ]
    }
  ]
}
```

The callback matches the exact username submitted by the interactive page through the provider's
`subject` request parameter.

The mapping supplies `aud` and `azp` from the authoritative token request client ID through the
provider's `${clientId}` template substitution (verified in NAV 6.0.0 `OAuth2TokenCallback.kt`,
`resolve()`: template variables `clientId`/`client_id` are always set and substitution is applied to
strings, lists, and nested maps).

It intentionally does not supply `iss`, protocol timestamps, or nonce.

The provider derives `iss` from `issuerId` and the request origin, and carries the authorization
request's nonce through the code flow (`AuthorizationCodeHandler.kt`). NAV's
`RequestMappingTokenCallback` does NOT add `azp` automatically (only the `DefaultOAuth2TokenCallback`
does), so the mapping must carry `azp` explicitly — and this design does.

Honest coverage note: ITAMbox accepts an absent `azp` (`itambox/core/auth/oidc.py:159-162`), so a
green E2E run proves that `azp == client_id` is accepted, but cannot distinguish a future provider
release that omits `azp` from one that emits it correctly. The workflow policy test pins the explicit
`azp` mapping so the positive authorized-party check stays exercised.

The readiness and browser tests will catch any future provider release that changes the derived
issuer, nonce, or discovery semantics.

### Network identity

| Item | Exact value |
|---|---|
| Provider host visible to browser and Django | `http://127.0.0.1:8081` |
| Issuer ID | `itambox-e2e` |
| Issuer | `http://127.0.0.1:8081/itambox-e2e` |
| Discovery | `http://127.0.0.1:8081/itambox-e2e/.well-known/openid-configuration` |
| Authorization | `http://127.0.0.1:8081/itambox-e2e/authorize` |
| Token | `http://127.0.0.1:8081/itambox-e2e/token` |
| Userinfo | `http://127.0.0.1:8081/itambox-e2e/userinfo` |
| JWKS | `http://127.0.0.1:8081/itambox-e2e/jwks` |
| Liveness | `http://127.0.0.1:8081/isalive` |
| ITAMbox origin | `http://localhost:8000` |
| RP redirect URI | `http://localhost:8000/oidc/callback/` |

Using `127.0.0.1` for the provider and `localhost` for ITAMbox is deliberate.

It ensures the browser retains the ITAMbox session cookie in its context while not sending that
host cookie to the provider origin.

### Client contract

| Item | Exact value |
|---|---|
| Client ID | `itambox-e2e-client` |
| Client secret | `itambox-e2e-secret` |
| Response type | `code` |
| Grant | authorization code |
| Scope string | `openid email profile groups` |
| ID-token signing algorithm | `RS256` |
| Key resolution | `OIDC_OP_JWKS_ENDPOINT` |
| Static signing key | absent |
| Nonce | enabled and returned unchanged by the provider |
| PKCE | not part of this slice; retain upstream default |

The client secret is a public, test-only fixture value.

It must never be copied into production instructions or treated as a GitHub secret.

The selected mock does not model a production client registry or certify redirect-URI/client-secret
policy enforcement.

The scenario proves that ITAMbox sends a usable confidential-client token request and returns to the
exact shared callback; provider-side client administration remains outside the bounded contract.

### Identity and claim contract

| Claim/input | Exact value | Purpose |
|---|---|---|
| Interactive subject | `itambox-e2e-oidc-user` | Deterministic provider form input and `sub` |
| `sub` | `itambox-e2e-oidc-user` | Stable provider identity |
| `email` | `e2e.oidc@itambox.local` | User lookup/username and holder UPN/email |
| `given_name` | `E2E` | User and AssetHolder first name |
| `family_name` | `OIDC` | User and AssetHolder last name |
| `groups` | `["e2e-oidc-admins"]` | One positive group-role mapping |
| `iss` | `http://127.0.0.1:8081/itambox-e2e` | Strict issuer check |
| `aud` | `["itambox-e2e-client"]` | Strict audience check |
| `azp` | `itambox-e2e-client` (mapped explicitly via `${clientId}`) | Positive authorized-party check; emitted explicitly because NAV's request-mapping callback adds no `azp` |
| `nonce` | request nonce | Upstream replay-binding check |

The mock request mapping shall match the exact interactive subject.

No default mapping shall allow an arbitrary typed value to receive the successful claim set.

The identity claims shall be returned by userinfo and be present where required for the ID-token
validation contract.

The test shall not assert exact `iat`, `exp`, authorization code, nonce, state, `kid`, or JWT bytes.

Those are intentionally per-flow values.

Token lifetime shall be five minutes.

The runner and service use the same host clock; no clock freeze or external time service is needed.

### Tenant OIDC settings contract

The job-level `ITAMBOX_TENANT_OIDC_CONFIGS` JSON shall contain one key, `helix-rnd`.

It shall pin:

| Setting | Value |
|---|---|
| `enabled` | `true` |
| `display_name` | `E2E OIDC` |
| `OIDC_RP_CLIENT_ID` | `itambox-e2e-client` |
| `OIDC_RP_CLIENT_SECRET` | `itambox-e2e-secret` |
| `OIDC_OP_AUTHORIZATION_ENDPOINT` | exact authorization URL above |
| `OIDC_OP_TOKEN_ENDPOINT` | exact token URL above |
| `OIDC_OP_USER_ENDPOINT` | exact userinfo URL above |
| `OIDC_OP_ISSUER` | exact issuer above |
| `OIDC_OP_JWKS_ENDPOINT` | exact JWKS URL above |
| `OIDC_RP_SIGN_ALGO` | `RS256` |
| `OIDC_RP_SCOPES` | `openid email profile groups` |
| `OIDC_USE_NONCE` | `true` |
| `OIDC_CREATE_USER` | `true` |
| `OIDC_GROUP_ROLE_MAPPING` | `{"e2e-oidc-admins":"Admin"}` |

It shall not set `OIDC_RP_IDP_SIGN_KEY`.

It shall not set `OIDC_UPDATE_USER`: neither ITAMbox nor `mozilla-django-oidc` 5.0.2 reads that
setting, and `update_user()` runs unconditionally for existing users.

`display_name` resolves to provider name `E2E OIDC (OIDC)` because `_display_name()` appends the
protocol suffix (`itambox/core/auth/providers.py:186-193`); the login action therefore renders as
`Sign in with E2E OIDC (OIDC)`, and every assertion below uses that exact accessible name.

The compact job-level value shall be equivalent to:

```json
{
  "helix-rnd": {
    "enabled": true,
    "display_name": "E2E OIDC",
    "OIDC_RP_CLIENT_ID": "itambox-e2e-client",
    "OIDC_RP_CLIENT_SECRET": "itambox-e2e-secret",
    "OIDC_OP_AUTHORIZATION_ENDPOINT": "http://127.0.0.1:8081/itambox-e2e/authorize",
    "OIDC_OP_TOKEN_ENDPOINT": "http://127.0.0.1:8081/itambox-e2e/token",
    "OIDC_OP_USER_ENDPOINT": "http://127.0.0.1:8081/itambox-e2e/userinfo",
    "OIDC_OP_ISSUER": "http://127.0.0.1:8081/itambox-e2e",
    "OIDC_OP_JWKS_ENDPOINT": "http://127.0.0.1:8081/itambox-e2e/jwks",
    "OIDC_RP_SIGN_ALGO": "RS256",
    "OIDC_RP_SCOPES": "openid email profile groups",
    "OIDC_USE_NONCE": true,
    "OIDC_CREATE_USER": true,
    "OIDC_GROUP_ROLE_MAPPING": {
      "e2e-oidc-admins": "Admin"
    }
  }
}
```

### JWKS versus static key decision

**JWKS pros**

- Exercises the configuration branch required for a typical external RSA-signed provider.
- Exercises `kid` lookup and the provider HTTP boundary.
- Keeps private key material out of source control.
- Allows the mock service to own signing lifecycle.

**JWKS cons**

- The key bytes and `kid` may vary per service start.
- Adds one local HTTP request during verification.
- A JWKS failure can fail the scenario before JIT assertions.

**Static-key pros**

- Makes key bytes constant and removes the JWKS request.
- Reduces one provider endpoint dependency.

**Static-key cons**

- Bypasses a valuable part of the selected provider integration.
- Requires maintaining a checked-in private/public fixture pair or workflow-generated settings.
- Can mask JWKS endpoint or key-ID interoperability errors.

**Decision:** use JWKS.

Determinism means the same observable success/failure contract on every run, not identical
cryptographic randomness.

## Scenario scope

The implementation adds one Playwright test to the end of the existing
`07-sso-scim.spec.ts` describe block.

It shall execute all browser-visible steps in one newly created context.

It shall not reuse the global authenticated storage state.

It shall close the context in `finally` even when an assertion fails.

### Included

- A 200 response for the local login page.
- Visibility and exact target of `Sign in with E2E OIDC (OIDC)`.
- Redirect through the tenant-specific initiation route.
- Arrival at the mock provider's issuer-specific authorization page.
- Entry of the deterministic subject in the provider form.
- Return through the shared callback in the same browser context.
- A 200 response for the authenticated dashboard.
- Exact active-tenant text `Helix Biopharma AG`.
- One User identity represented by the Membership row.
- One linked AssetHolder row with exact names, visible UPN (equal to the email), and tenant.
- One active Membership for `helix-rnd`.
- One live `Admin` role grant derived from the groups claim.

The seed fixture defines `Helix Biopharma AG` with slug `helix-rnd`
(`itambox/core/management/commands/_seed/organizations.py:374-406`).

The organization routes expose the chosen AssetHolder and Membership list pages
(`itambox/organization/urls.py:59-66`, `itambox/organization/urls.py:123-131`).

### Excluded

- OIDC logout or provider single logout.
- A second member/manager/admin mapping case.
- Provider-staff mapping for managed tenants.
- Relogin/profile-update behavior for an existing OIDC user.
- Account-link collision and duplicate email behavior.
- Negative audience, `azp`, issuer, or missing-issuer variants.
- Refresh-token use.
- Token expiry or clock-skew manipulation.
- Multiple tenants or multiple provider buttons.
- Production-provider-specific consent, MFA, branding, or claims transforms.

Role selection variants already have focused backend coverage for mapped Admin, Manager, and default
Member (`itambox/core/tests/test_oidc.py:131-162`) and for priority resolution
(`itambox/core/tests/test_oidc.py:887-911`).

Wrong `azp`, wrong issuer, and missing issuer already have focused failure coverage
(`itambox/core/tests/test_ldap_oidc_error_contracts.py:182-220`).

The follow-up must keep those unit tests unchanged and green.

## Scenario table

| ID | Action | Expected result | Required assertions |
|---|---|---|---|
| OIDC-P0 | CI probes liveness, discovery, and JWKS | Provider contract is ready before Django/Playwright | liveness 200; exact issuer and the four endpoint URLs; at least one RSA signing key; bounded retries then hard failure |
| OIDC-P1 | Fresh context opens `/accounts/login/` | Tenant provider is advertised | response 200; exact accessible button name `Sign in with E2E OIDC (OIDC)`; exact tenant initiation pathname |
| OIDC-P2 | Browser activates tenant provider action | Browser reaches mock interactive login | initiation response is redirect; final provider origin/issuer path is exact; provider form visible; no ITAMbox login loop |
| OIDC-P3 | Browser submits exact provider subject | Code callback authenticates and lands on dashboard | provider submit accepted; callback redirect completes; final URL is ITAMbox `/`; final response 200; dashboard title/marker visible |
| OIDC-P4 | Browser inspects active workspace | Callback restored and selected the initiating tenant | `.workspace-switcher-name` equals `Helix Biopharma AG`; tenant-scoped pages return 200 |
| OIDC-P5 | Browser filters Membership list by fixture email | User and mapped membership exist | exactly one matching row; email/username; active state; `Admin`; tenant binding via the active workspace and tenant-scoped URL |
| OIDC-P6 | Browser filters AssetHolder list by fixture email | Linked profile exists in the same tenant | exactly one matching row; visible UPN equal to the email; `E2E`; `OIDC`; `Helix Biopharma AG` |
| OIDC-N1 | Existing test initiates for unknown tenant | Unknown tenant remains undisclosed | existing 404 assertion remains unchanged |
| OIDC-N2 | Existing test calls callback without initiation | Callback fails closed | existing redirect-to-root then login assertions remain unchanged |
| OIDC-N3 | Existing authenticated session receives provider error | Session is terminated | existing callback and subsequent login redirect assertions remain unchanged |

## Detailed browser sequence

1. Create `browser.newContext({ baseURL })` without a `storageState` option.
2. Create one page in that context.
3. Navigate to `/accounts/login/` and assert the navigation response is not null and has status 200.
4. Locate the provider link via `getByRole('link', { name: 'Sign in with E2E OIDC (OIDC)' })`.
5. Assert it is visible and its URL pathname is exactly `/oidc/authenticate/helix-rnd/`.
6. Arm response/navigation observers before clicking the link; capture the `state` value from the
   initiation redirect location in memory (never printed).
7. Click and assert the initiation response is `302`.
8. Wait for `http://127.0.0.1:8081/itambox-e2e/authorize` with query parameters.
9. Assert the provider page response is 200 and the interactive subject field is visible.
10. Fill `itambox-e2e-oidc-user` into `input[name="username"]` (the NAV 6.0.0 form has no visible
    label text; its placeholder is `Enter any user/subject`).
11. Arm the callback/dashboard navigation observers before submitting the provider form.
12. Submit using `getByRole('button', { name: 'Sign-in' })` — the NAV 6.0.0 login template's submit
    control (`login.ftl`) — never layout-dependent CSS.
13. Assert the provider POST completes with `302` to
    `http://localhost:8000/oidc/callback/?code=…&state=…`; assert status and location without
    printing the query values, and compare the callback `state` with the captured initiation
    `state` in memory (contract item 5: the originating state round-trips).
14. Assert the callback navigation responds with `302` to `http://localhost:8000/`.
15. Assert the final dashboard navigation response is 200.
16. Assert the document title is `Dashboard - ITAMbox` or the stable dashboard marker is visible.
17. Assert `.workspace-switcher-name` has exact trimmed text `Helix Biopharma AG`.
18. Navigate to `/organization/memberships/?q=e2e.oidc%40itambox.local`.
19. Assert the response is 200 and exactly one body row matches the fixture email.
20. Within that row, assert the linked user/email, active state, and `Admin` role text; the tenant
    binding is established by the active workspace (the table omits its tenant column under a single
    active tenant).
21. Navigate to `/organization/asset-holders/?q=e2e.oidc%40itambox.local`.
22. Assert the response is 200 and exactly one body row matches the fixture email or UPN.
23. Within that row, assert `E2E`, `OIDC`, the exact visible UPN (equal to the email), and the tenant.
24. Close the page/context in `finally`.

The test shall not decode its own ID token.

Success through the application callback is the assertion that the upstream nonce/signature checks
and ITAMbox's audience/`azp`/issuer checks accepted the provider output.

The test shall not read Django's session table or cookie contents.

The active-workspace UI and tenant-scoped result pages are the assertion that the shared callback
restored the initiating tenant.

## CI integration

### Service placement

Add `oidc` under the existing `jobs.e2e.services` mapping beside PostgreSQL at
`.github/workflows/e2e.yml:20-33`.

The service shall:

- use the exact tag-and-digest reference in this design;
- expose container port 8080 as runner port 8081;
- receive the complete JSON provider configuration through `JSON_CONFIG`;
- enable interactive login;
- configure RS256;
- configure one exact subject request mapping;
- configure a five-minute token lifetime;
- receive no production secret;
- have no network dependency other than the runner-local browser/RP calls.

### Why a service container

**Pros**

- GitHub Actions owns start and teardown.
- The provider is up before any settings import or Django command.
- Logs are attached to the job/service lifecycle.
- No PID file, trap, or manual cleanup is needed.
- It matches the existing PostgreSQL service pattern.

**Cons**

- Service configuration must be available in workflow YAML before checkout.
- Local reproduction uses an equivalent Docker command rather than the exact workflow lifecycle.
- Embedded JSON is less readable than a checked-in provider config file.

### Rejected background-process placement

A background process could start after checkout and load a repository config file.

That would make the config easier to read, but it would require installation or image-run steps,
manual PID/container cleanup, log plumbing, and ordering relative to the Django server.

The job already has a service lifecycle, so those costs are not justified.

### Environment injection

Add these values to the job-level environment block at `.github/workflows/e2e.yml:35-52`:

- `E2E_OIDC_PROVIDER_URL=http://127.0.0.1:8081`
- `E2E_OIDC_SUBJECT=itambox-e2e-oidc-user`
- `E2E_OIDC_EMAIL=e2e.oidc@itambox.local`
- the complete single-line `ITAMBOX_TENANT_OIDC_CONFIGS` JSON described above.

The service's `JSON_CONFIG` and job's tenant config necessarily repeat a small set of endpoint and
claim literals because GitHub service initialization precedes normal workflow steps.

The workflow policy test shall pin those literals on both sides — including the mapped `aud` and
`azp` literals and the rendered `Sign in with E2E OIDC (OIDC)` accessible name — so drift fails
review/CI.

Do not construct `ITAMBOX_TENANT_OIDC_CONFIGS` in a later shell step or append it to
`GITHUB_ENV` after a Django command has already imported settings.

### Provider readiness and contract check

Add a bounded readiness step after dependency setup and before the first Django check.

The step shall:

1. Poll `/isalive` with `curl --fail --silent --show-error`.
2. Use a short interval and an absolute attempt/deadline bound.
3. Fail the job if the provider never becomes live.
4. Fetch the issuer discovery document.
5. Assert exact `issuer`, `authorization_endpoint`, `token_endpoint`, `userinfo_endpoint`, and
   `jwks_uri` values.
6. Fetch the JWKS document.
7. Assert at least one key has `kty=RSA` and permits signing/RS256.
8. Print concise endpoint diagnostics but no token or client-secret material.

Do not use an unbounded `sleep` or let Playwright become the provider health check.

### Platform and locale independence

The gating environment remains `ubuntu-latest` (`.github/workflows/e2e.yml:15-18`).

The provider contract shall use ASCII-only issuer IDs, subjects, emails, group names, JSON keys, and
semantic form selectors.

It shall not assert localized provider prose, formatted dates, timezone names, or rendered JWT
timestamps.

Windows developers may reproduce the service with Docker Desktop or another compatible container
runtime, but Windows-specific process behavior is not part of the CI contract.

### Django fixture provisioning

Extend the existing provisioning step at `.github/workflows/e2e.yml:150-198`.

Before Django starts, it shall:

1. Resolve the live `helix-rnd` tenant.
2. Resolve the seeded `Administrator` role.
3. Create or update a live tenant-owned `Admin` role with the same permission set.
4. Assert no User exists with `e2e.oidc@itambox.local`.
5. Assert no AssetHolder exists in `helix-rnd` with that email or UPN.
6. Assert no Membership exists for that identity.
7. Fail on unexpected pre-existing fixture data; do not delete it silently.

This precondition proves the first successful attempt takes the JIT creation path.

### Flake resistance

- All provider and RP traffic stays on loopback.
- The service and database are fresh for each job.
- Playwright uses one worker (`itambox/tests/e2e/playwright.config.ts:6-9`).
- The positive flow uses one browser context from initiation through callback.
- Readiness is separated from scenario assertions.
- Exact fixed identity values replace generated names or timestamps.
- The test waits on navigation/response events rather than fixed sleeps.
- Status codes are asserted at each application/provider page boundary.
- Random protocol values are not compared byte-for-byte.
- A retry reuses the same stable provider identity and the JIT path is idempotent after the first
  attempt.
- The positive test runs after existing SCIM/OIDC cases so its authenticated/JIT state cannot change
  earlier assertions.

## Test data and fixture strategy

### Tenant

Use the existing `helix-rnd` tenant configured by `E2E_TENANT_SLUG`
(`.github/workflows/e2e.yml:45-49`).

Do not create a third tenant solely for OIDC.

The OIDC config key must equal that tenant slug because login-provider discovery excludes config
keys with no live tenant (`itambox/core/auth/providers.py:67-73`).

### Identity

Use a new identity distinct from the local `e2e-admin` superuser.

The OIDC user shall have:

- subject `itambox-e2e-oidc-user`;
- username/email `e2e.oidc@itambox.local`;
- first name `E2E`;
- last name `OIDC`;
- group `e2e-oidc-admins`.

Do not reuse `E2E_USERNAME` or the generated local password.

That separation proves JIT rather than account matching and prevents the local global setup from
changing the OIDC test's authentication premise.

### Role

Map `e2e-oidc-admins` to `Admin`.

Provision `Admin` deliberately before login with the seeded Administrator permissions.

This design tests group-to-role selection and membership grant creation.

It does not test whether an incoming group can cause privileged-role definition creation.

### Independence and retry semantics

The test's initial job precondition is “identity absent.”

Within Playwright, the scenario is safe to retry because the backend matches the same email and
updates/reuses the identity on subsequent successful callbacks; `update_user()` runs
unconditionally (`itambox/core/auth/oidc.py:196-248`), which is also why the inert
`OIDC_UPDATE_USER` setting is absent from the canonical configuration.

Postconditions must assert one matching row, not that the row was necessarily created during the
current retry attempt.

No later test may depend on the OIDC user.

No existing SCIM fixture may use the OIDC email, subject, or group.

## Dependency policy

### Decision

Do not add a Python dependency to `pyproject.toml` or `uv.lock`.

The current development dependency group is declared at `pyproject.toml:60-91`; the provider is not
a library imported by tests and does not belong there.

Do not install an unpinned package ad hoc in a workflow step.

Treat the digest-pinned container image as E2E infrastructure, analogous to the digest-pinned
PostgreSQL service at `.github/workflows/e2e.yml:20-33`.

### Upgrade policy

An upgrade must be a reviewed workflow change that updates:

- provider version and digest;
- the upstream release reference;
- readiness/discovery expectations if upstream changed them;
- interactive form selectors only when verified against the new version;
- the workflow policy test's pinned contract.

Do not use `latest`.

## Documentation policy

### Usage documentation

The implementation shall add a short “Testing with a local mock OIDC provider” subsection after the
current OIDC configuration and token-validation guidance
(`itambox/docs/usage/sso-and-mfa.md:273-354`).

It shall document:

- that the recipe is development/test-only;
- the pinned provider image;
- loopback binding;
- the issuer and callback URL;
- a redacted/example tenant config;
- the interactive subject value convention;
- JWKS rather than a static signing key;
- a warning never to expose the mock publicly;
- a warning never to use the fixed client secret outside a local test;
- the exact callback URL rule already described at
  `itambox/docs/usage/sso-and-mfa.md:325-342`.

### Development documentation

This design is the development decision record for the slice.

Do not add a second development note that duplicates it.

The production installation guide currently identifies `ITAMBOX_TENANT_OIDC_CONFIGS` as a
multi-tenant settings input (`itambox/docs/operations/installation.md:230-238`).

Do not add the mock-provider recipe to that production-oriented section.

## Security and quality gates

### Mandatory properties

1. No conditionally skipped positive OIDC scenario.
2. No `try/catch` that logs a provider failure and lets the test pass.
3. No `status() !== undefined` or truthiness-only status assertion.
4. No fixed sleep as the primary readiness or navigation mechanism.
5. No disabling state, nonce, issuer, audience, `azp`, or signature validation.
6. No `verify_ssl=False` workaround for an HTTPS provider; this contract deliberately uses local
   HTTP.
7. No ID token, access token, private key, production secret, or *unconsumed* credential in test
   logs or artifacts. Already-consumed one-time authorization codes may appear only in job-local,
   short-retention (7-day) CI failure artifacts — never in workflow stdout: Django's dev server
   logs the callback request line including `?code=…&state=…`
   (`django/core/servers/basehttp.py`, request-handler logging), and Playwright traces record
   navigation URLs. The E2E workflow shall therefore start Django with request logging that omits
   query strings, and shall keep the existing failure-only trace upload.
8. No checked-in private signing key.
9. No public network call during the test after the container image is present.
10. No change to the three negative OIDC expectations.
11. No change to the existing backend/error-contract tests except where a future independent defect
    requires it.
12. Every navigation/API response used as an assertion boundary has an explicit expected status.

The current policy test already rejects undefined-status and conditional assertion patterns and pins
the negative OIDC contract (`itambox/core/tests/test_e2e_workflow.py:81-112`).

Extend that policy test to require the positive provider literals, service, readiness check, fresh
context, positive test name, callback/dashboard assertions, tenant assertion, and both JIT UI paths.

### Existing negative contracts

Keep these exact behaviors green:

- unknown tenant initiation returns 404
  (`itambox/tests/e2e/spec/07-sso-scim.spec.ts:114-120`);
- callback without initiation redirects to root and an anonymous root request redirects to login
  (`itambox/tests/e2e/spec/07-sso-scim.spec.ts:122-141`);
- provider error terminates an existing authenticated session
  (`itambox/tests/e2e/spec/07-sso-scim.spec.ts:367-398`).

### Test manifest policy

Keep the positive scenario in the existing TypeScript spec.

Do not add a new Python test file for this slice.

The resource-grant boundary gate derives changed Python test paths and requires exact equality with
the resource-grant manifest (`itambox/core/tests/test_import_boundaries.py:964-987`).

Its selector/document consistency is also checked
(`itambox/core/tests/test_import_boundaries.py:944-962`).

`test_e2e_workflow.py` is already represented in the resource-grant manifest
(`scripts/resource_grant_test_manifest.json:19`,
`scripts/resource_grant_test_manifest.json:120`).

If implementation nevertheless adds or moves any Python `test_*.py` file, it must update the
resource-grant manifest's changed and mandatory sets and the corresponding threat-document selector
in the same change.

CI also constructs its serial collection manifest before its parallel and serial-only lanes
(`.github/workflows/ci.yml:227-276`).

Any pytest test addition must remain collectible in exactly the intended lane.

### Repository gates to run

The implementation change shall run, at minimum:

```text
cd itambox
uv run --locked --only-group dev python -m unittest core.tests.test_e2e_workflow
uv run --locked --group dev pytest core/tests/test_oidc.py
uv run --locked --group dev pytest core/tests/test_login_page.py
uv run --locked --group dev pytest core/tests/test_ldap_oidc_error_contracts.py
npm test
```

The full CI gates remain required before merge.

## Risks and pitfalls

| Risk | Failure signature | Mitigation |
|---|---|---|
| Browser context loses ITAMbox cookie | Callback reports missing state/nonce or returns to login | One explicit context from login page through provider and callback; never replace page/context mid-flow |
| ITAMbox cookie leaks to provider | Provider receives unrelated session cookie | Different loopback hostnames: ITAMbox `localhost`, provider `127.0.0.1` |
| Provider and tenant issuer differ | Typed token validation failure and login rejection | One exact issuer literal pinned in service mapping, discovery assertion, and tenant config |
| Audience differs in shape/value | Callback rejects token | Explicit one-element `aud` array containing exact client ID |
| `azp` missing or wrong | Missing `azp` passes the backend check silently; wrong `azp` rejects the token | Explicit `${clientId}` mapping; policy test pins it; honest coverage note in the provider contract |
| Provider omits nonce | Upstream callback rejects token | Interactive authorization mapping preserves the request nonce; do not disable nonce |
| Provider key changes per job | Exact JWKS/JWT snapshot would flap | Assert key type/use/algorithm, not bytes, `kid`, or token text |
| Clock drift/short expiry | Intermittent expired/not-yet-valid token | Five-minute lifetime, same runner clock, no external host, no exact timestamp assertion |
| Callback redirect loop | Browser returns repeatedly to login/initiation | Assert response chain and exact final URL/status; retain trace and service logs |
| Shared callback lacks tenant session | Callback resolves no tenant or JIT occurs outside tenant | Begin via tenant button and retain same context; assert active workspace and tenant-scoped rows |
| Login page hides button | Provider flow never starts | Inject usable config before all Django imports; assert exact button and href first |
| Seed lacks `Admin` role | JIT creates/refuses/assigns unexpected role | Deliberately provision live Admin role with known permissions before server start |
| OIDC identity already exists | First run does not prove JIT | Fail fixture precondition instead of deleting data |
| Test retry sees created identity | Retry falsely expects absence | Absence is job setup assertion; browser postconditions are idempotent and require exactly one row |
| Service starts slowly | First discovery call flakes | Bounded liveness poll before any Django/E2E work |
| Embedded JSON quoting breaks | Service or Django settings fail before test | Keep compact valid JSON; parse/pin it in workflow policy test |
| Provider UI markup changes | Subject form locator fails after image upgrade | Pin digest; use stable semantic/name selector; upgrade only with contract review |
| Local recipe binds publicly | Insecure test IdP exposed | Bind host port to loopback and place warnings in usage docs |
| Trace contains sensitive protocol values | Authorization code/token appears in artifact | Never print response bodies/tokens; fixed secret is test-only; authorization codes are already-consumed, job-local, and 7-day retention; retain failure trace policy consciously |
| Positive test mutates later cases | Ordering-dependent failures | Put positive test last and prohibit later dependencies |

## Test plan

This section mirrors the scenario table and is the implementation verification checklist.

### OIDC-P0 — provider readiness

**Setup**

- Start the digest-pinned service with exact `JSON_CONFIG`.
- Map port 8081 to service port 8080.

**Actions**

- Poll liveness.
- Fetch discovery.
- Fetch JWKS.

**Assertions**

- Liveness returns 200 within the bounded deadline.
- Discovery returns 200.
- `issuer` is exact.
- Authorization, token, userinfo, and JWKS URLs are exact.
- JWKS returns 200 and has an RSA signing key.

### OIDC-P1 — advertised tenant login

**Setup**

- Django starts with job-level tenant OIDC JSON.
- Use a fresh browser context.

**Actions**

- Open `/accounts/login/`.
- Locate `Sign in with E2E OIDC (OIDC)`.

**Assertions**

- Login response is 200.
- Exactly one matching action is visible.
- Its pathname is `/oidc/authenticate/helix-rnd/`.

### OIDC-P2 — provider initiation

**Actions**

- Click the action.
- Follow the redirect to the provider.

**Assertions**

- ITAMbox initiation responds with `302`.
- The browser reaches the exact provider origin and issuer-specific authorize path.
- Provider page response is 200.
- `input[name="username"]` and `getByRole('button', { name: 'Sign-in' })` are visible.

### OIDC-P3 — successful callback and dashboard

**Actions**

- Fill the exact subject into `input[name="username"]`.
- Submit the provider form with the `Sign-in` button.

**Assertions**

- The provider POST responds `302` to `/oidc/callback/?code=…&state=…` (status and location
  asserted without printing query values; callback `state` equals the captured initiation `state`).
- The callback navigation responds `302` to `/`.
- The final dashboard response is 200.
- Dashboard title/marker is visible.
- Browser is not returned to `/accounts/login/`.

### OIDC-P4 — tenant binding

**Actions**

- Read the top-bar active workspace.
- Navigate to a tenant-scoped organization list.

**Assertions**

- Workspace text is exactly `Helix Biopharma AG`.
- Tenant-scoped page response is 200.
- Result data belongs to `helix-rnd`.

### OIDC-P5 — User, Membership, and role mapping

**Actions**

- Filter `/organization/memberships/` by the exact email.

**Assertions**

- Response is 200.
- Exactly one row matches.
- User/email is exact.
- Membership is active.
- Role text contains the one live `Admin` grant.
- Tenant binding follows from the active workspace (`Helix Biopharma AG`) and the tenant-scoped URL;
  the table omits its tenant column under a single active tenant.

### OIDC-P6 — AssetHolder JIT

**Actions**

- Filter `/organization/asset-holders/` by the exact email.

**Assertions**

- Response is 200.
- Exactly one row matches.
- Visible UPN is `e2e.oidc@itambox.local` (the email cell is not rendered by default). If a future
  UI version stops rendering the UPN column, fall back to the filter query plus workspace/tenant
  derivation — never delete the assertion.
- First and last names are `E2E` and `OIDC`.
- Tenant is `Helix Biopharma AG`.

### OIDC-N1 — unknown tenant regression

Run the existing unknown-tenant test unchanged.

Expected: 404.

### OIDC-N2 — uninitiated callback regression

Run the existing uninitiated callback test unchanged.

Expected: callback redirects to root, then anonymous root redirects to login.

### OIDC-N3 — provider-error session regression

Run the existing provider-error test unchanged.

Expected: provider error terminates the authenticated session.

## Acceptance-criteria mapping

| Issue #38 item | Current state | Follow-up evidence | Completion rule |
|---|---|---|---|
| Existing seven checked acceptance criteria | Delivered by PR #258 | OIDC-N1/N2/N3 and existing SCIM tests remain green | No expectation weakened or removed |
| “Configure a deterministic OIDC test provider/mock if successful OIDC authentication is intended to be covered” | Open | OIDC-P0 plus digest-pinned service/config | Provider starts locally and its discovery/JWKS contract hard-passes |
| Successful OIDC authentication | Deferred in PR #258 | OIDC-P1/P2/P3 | Real browser completes authorization-code flow and receives authenticated dashboard 200 |
| Tenant binding across shared callback | Negative/session mechanics exist | OIDC-P4 | Active workspace and tenant-scoped UI identify `helix-rnd` |
| JIT provisioning and role mapping | Unit-covered only | OIDC-P5/P6 | Exact User-facing Membership and AssetHolder rows exist with mapped Admin role |
| Delivery-status item for successful external OIDC login follow-up | Open | Follow-up PR links this design and green E2E run | Mark complete only after implementation merges |

The issue should not be closed on provider liveness alone.

Completion requires the positive browser callback and JIT/tenant assertions.

## Implementation map

| File | Planned change | Existing anchor |
|---|---|---|
| `.github/workflows/e2e.yml` | Add digest-pinned OIDC service, job env JSON, readiness contract, Admin/JIT absence fixture, and Django request logging that omits query strings | services/env `20-52`; provisioning `150-198`; server/test `200-222` |
| `itambox/tests/e2e/spec/07-sso-scim.spec.ts` | Add deterministic env inputs and OIDC-P1 through P6 as one final fresh-context test | env/setup `3-17`; negative cases `114-141`, `367-398` |
| `itambox/tests/e2e/preflight-check.mjs` | Require and report provider URL, subject, email, and tenant OIDC config without secrets | current environment validation `59-97`; summary `159-177` |
| `itambox/core/tests/test_e2e_workflow.py` | Pin service/digest, early settings injection, readiness, exact literals, explicit assertions, and positive flow presence | workflow policy `81-112` |
| `itambox/docs/usage/sso-and-mfa.md` | Add local mock testing subsection and safety warning | OIDC config/callback/validation `273-354` |
| `scripts/resource_grant_test_manifest.json` | No change expected because no Python test file is added and the existing policy test is already listed | existing entries `19`, `120` |
| `pyproject.toml` | No change | runtime dependency `35`; dev group `60-91` |
| `uv.lock` | No change | current OIDC resolution `1567-1574` |
| `itambox/core/auth/oidc.py` | Scope addendum (approved): optional-setting defaults (`OIDC_RP_IDP_SIGN_KEY`, `OIDC_OP_JWKS_ENDPOINT` → `None`) in `get_settings`; E2E otherwise exercises the backend/views as-is | settings mixin `97-133`; backend `135-380`; views `406-459` |

### Expected implementation diff shape

The follow-up should modify five existing files:

1. workflow;
2. existing TypeScript spec;
3. E2E preflight;
4. existing workflow policy test;
5. OIDC usage documentation.

It should add no migration, Python dependency, provider config file, private key, new TypeScript
spec, or new Python test file. The only application-module change is the approved
`core/auth/oidc.py` optional-setting defaults (see the scope addendum below).

If implementation discovers that the mock cannot satisfy the exact bounded contract without an
application change, stop and return to design review rather than weakening validation.

## Rollout and failure diagnosis

### Rollout order

1. Add the service and readiness contract.
2. Verify the provider's interactive subject matching against the running image (`isMatch` with the
   `subject` extra match parameter must reach the mapped claims); if it does not hold, use a named
   fallback mapping — never weaken validation (the document's escape clause governs).
3. Confirm the rendered literals against the running app before finalizing assertions: the
   `Sign in with E2E OIDC (OIDC)` accessible name, the `.workspace-switcher-name` text, and the
   visible AssetHolder UPN column.
4. Add early tenant settings and fixture preconditions.
5. Add the positive browser scenario.
6. Extend workflow policy assertions.
7. Add local testing documentation.
8. Run focused unit/policy tests.
9. Run the E2E job and inspect the first successful trace/log set.
10. Run the full required CI suite.
11. Update issue #38 delivery status only after merge.

### Failure classification

| Last successful boundary | Likely owner |
|---|---|
| Service never live | image/config/service lifecycle |
| Liveness works, discovery/JWKS fails | provider `JSON_CONFIG` or pinned endpoint contract |
| Login button absent | settings injection, tenant slug, or usability check |
| Button works, provider page absent | authorization URL/port/service routing |
| Provider submit returns error | request mapping, redirect URI, or client fixture |
| Interactive subject never reaches the mapped claims | Token lacks fixture identity; JIT assertions fail | Verify `isMatch`/`subject` extra-param behavior against the running image first (rollout step 2); named fallback mapping; never weaken validation |
| Callback rejects before dashboard | state/nonce, signature/JWKS, issuer, audience, `azp`, or token exchange |
| Dashboard works, wrong workspace | shared callback tenant session restoration |
| Workspace works, Membership absent | group mapping/provisioning/role fixture |
| Membership works, holder absent | AssetHolder link/JIT claim mapping |
| All positive checks work, negative fails | unintended state/order coupling or contract regression |

The test must preserve provider and Django stderr in the workflow log on failure.

Playwright trace retention remains the current failure-only behavior
(`.github/workflows/e2e.yml:240-250`).

## Non-goals

- Certifying Microsoft Entra, Okta, Auth0, Dex, or Keycloak.
- Testing every optional `mozilla-django-oidc` setting.
- Testing OIDC conformance.
- Testing the mock provider's security.
- Replacing backend/unit security tests with E2E coverage.
- Adding a production example client secret.
- Proving provider-side MFA.
- Proving cross-provider logout.
- Making CI cryptographic randomness byte-identical.
- Expanding SCIM scope.
- Changing tenant, permission, or role semantics.

## External references

- Issue baseline and remaining criterion:
  [itambox/itambox-webapp#38](https://github.com/itambox/itambox-webapp/issues/38)
- Merged negative-contract slice:
  [PR #258](https://github.com/itambox/itambox-webapp/pull/258)
- Tracking epic:
  [issue #103](https://github.com/itambox/itambox-webapp/issues/103)
- NAV mock server project, features, configuration, liveness, and license:
  [navikt/mock-oauth2-server](https://github.com/navikt/mock-oauth2-server)
- NAV mock server pinned release:
  [6.0.0 release](https://github.com/navikt/mock-oauth2-server/releases/tag/6.0.0)
- Dex getting started and configuration:
  [Dex documentation](https://dexidp.io/docs/getting-started/),
  [custom scopes, claims, and clients](https://dexidp.io/docs/configuration/custom-scopes-claims-clients/)
- Dex source and license:
  [dexidp/dex](https://github.com/dexidp/dex)
- Keycloak container guidance:
  [Running Keycloak in a container](https://www.keycloak.org/server/containers)
- Keycloak source and license:
  [keycloak/keycloak](https://github.com/keycloak/keycloak)
- Upstream relying-party settings and nonce/JWKS behavior:
  [mozilla-django-oidc settings](https://mozilla-django-oidc.readthedocs.io/en/stable/settings.html)
- Distinct Python alternative mentioned in the premise correction:
  [oidc-provider-mock on PyPI](https://pypi.org/project/oidc-provider-mock/)

## Maintainer decisions

All seven decisions were approved by the maintainer on 2026-08-11 ("1-7, yes"). The recommended
defaults below are therefore binding for the follow-up implementation.

1. Approve NAV `mock-oauth2-server` 6.0.0 as a digest-pinned service container? **Approved (recommended default: yes).**
2. Approve live RS256 JWKS with an ephemeral per-service key instead of a checked-in static signing key? **Approved (recommended default: yes).**
3. Approve one positive mapped-Admin scenario, with the `Admin` role deliberately provisioned from seeded Administrator permissions? **Approved (recommended default: yes).**
4. Keep logout, role variants, provider-staff mapping, and relogin/profile-update outside this follow-up? **Approved (recommended default: yes).**
5. Keep the positive scenario in `07-sso-scim.spec.ts` and use a fresh browser context, rather than adding a new spec file? **Approved (recommended default: yes).**
6. Treat the provider as CI infrastructure with no `pyproject.toml`/`uv.lock` dependency and document an equivalent local Docker recipe? **Approved (recommended default: yes).**
7. Require JIT postconditions through the tenant-scoped Membership and AssetHolder UI tables, with no test-only database/API endpoint? **Approved (recommended default: yes).**

## Scope addendum (2026-08-11): optional OIDC settings default to None

The first E2E run of the follow-up PR exposed a defect in `TenantOIDCSettingsMixin.get_settings`
(`itambox/core/auth/oidc.py:104-127`): `TenantOIDCBackend` deliberately skips the upstream
`OIDCAuthenticationBackend.__init__` (which sets `OIDC_RP_IDP_SIGN_KEY` and `OIDC_OP_JWKS_ENDPOINT`
to `None`), so any lazy attribute read of those optional settings falls through to
`import_from_settings` without a default and raises `OIDCConfigurationError` before the JWKS
fallback can run (mozilla-django-oidc 5.0.2 `auth.py:53,59,201-204`). A tenant configured with JWKS
only — exactly what this design mandates — therefore cannot complete a callback: the shared callback
500s at `oidc.py:127`, which the E2E artifact confirmed (`OIDCConfigurationError at
/oidc/callback/`, raised during `TenantOIDCCallbackView`).

Maintainer decision (parent-side, best-judgement while the maintainer was unavailable; reversible):
extend `get_settings` with the two upstream defaults (`OIDC_RP_IDP_SIGN_KEY` → `None`,
`OIDC_OP_JWKS_ENDPOINT` → `None`), mirroring the existing `OIDC_RP_SIGN_ALGO`/`OIDC_RP_SCOPES`
default pattern, plus a unit test in `itambox/core/tests/test_oidc.py`.

Safety assessment: neutral. Required settings (`OIDC_RP_CLIENT_ID`, `OIDC_RP_CLIENT_SECRET`,
endpoints, `OIDC_OP_ISSUER`) still raise when missing; the strict audience/`azp`/issuer validation in
`verify_token` is untouched; the login-page usability check (`itambox/core/auth/providers.py:145-157`)
still requires either JWKS or a signing key before a provider button is rendered. The JWKS-only
provider contract (maintainer decision 2) is preserved and becomes actually exercisable.

Implementation: `itambox/core/auth/oidc.py` (`get_settings` defaults), `itambox/core/tests/test_oidc.py`
(`test_optional_settings_default_to_none`), this addendum, and the implementation-map update above.
