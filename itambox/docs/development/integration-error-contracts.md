# External integration error contracts

This document defines one shared contract for failures at external integration
boundaries. It is the consumer contract; provider adapters decide how their
protocol maps into it.

## Two independent axes

`IntegrationError.disposition` is either:

- `retryable`: the operation may be attempted again later (for example a
  transport failure, HTTP 5xx, or an in-budget HTTP 429);
- `terminal`: retrying the same operation without an external/configuration
  change is not useful (for example invalid credentials, a non-retryable 4xx,
  or an invalid success payload). A local retry-budget exhaustion remains
  `retryable` for a later caller and is marked `retry_exhausted`.

`IntegrationError.user_visible` and its `user_message` are a separate channel.
A retryable outage can still have a stable user-facing job message, and a
terminal authentication error must not reveal whether a user, credential, or
provider resource exists. `str(error)` is the safe message and must not contain
remote URLs, headers, credentials, tokens, response bodies, or raw exception
text.

The shared types live in the dependency-free `core.errors` module:

- `IntegrationAuthenticationError` — terminal provider authentication;
- `IntegrationConfigurationError` — terminal local/provider configuration;
- `IntegrationUnavailableError` — retryable transport/5xx failure;
- `IntegrationRateLimitedError` — retryable in-budget rate limiting; its
  provider delay is finite, non-negative and capped at 300 seconds;
- `IntegrationRetryBudgetExceededError` — retryable for a later caller after
  the current invocation exhausts its local rate-limit budget;
- `IntegrationContractError` — terminal malformed/unexpected success response;
- `IntegrationUntrustedNextLinkError` — terminal provider redirect attempt;
- `IntegrationRequestError` and `IntegrationNotFoundError` — terminal 4xx
  request/resource failures;
- `IntegrationUnexpectedError` — terminal task-isolation fallback for an
  otherwise unknown failure, with no provider exception text persisted.

`IntegrationContext` is an allowlist containing only provider, stable operation,
tenant ID, actor ID and request/correlation ID. `IntegrationError.log_extra()`
returns these fields plus `error_code`, `disposition`, `retry_exhausted`,
`status_code`, and only the explicit optional fields `object_id`,
`exception_type`, `cause_type`, `source_file`, `source_line`, `retry_count`,
`retry_delay` and `retry_after` for `logging.extra`. It never includes a request
URL, headers, payload or response body; exception text is never included.

## Retry rules in the Intune slice

The Microsoft Graph adapter classifies transport failures/5xx as retryable for the
caller and handles HTTP 429 with a finite per-operation `RetryBudget` (not
shared across devices). Both retry count and elapsed wall-clock time are bounded,
and provider `Retry-After` is parsed defensively, made finite, and clamped before
sleeping. Graph pagination accepts only HTTPS `graph.microsoft.com/v1.0/...`
next links, never sends the bearer header to an arbitrary provider-controlled
next-link host, and stops after a fixed page ceiling. OAuth 429 responses produce
a bounded retryable signal, while Graph collection 429 responses consume the
local budget. The adapter does not
retry transport/5xx failures in-process or re-enqueue a django-q job in this
slice; queue re-enqueue is a separate follow-up decision.

HTTP 401/403, 404 and other 4xx responses are terminal. TLS, URL-shape and
redirect-loop transport failures are also terminal; timeouts and connection
failures remain retryable. A successful response
without valid JSON/value or an OAuth response without a non-empty
`access_token` is a terminal contract error.

The Intune task catches `IntegrationError` explicitly, logs only the structured
allowlist, and persists `display_message()`. The structured Job append-log line
is deliberately tenant-scoped operational triage; `mark_failed()` remains the
generic, user-safe message appended separately. Optional software degradation is
counted as `software_degraded` in the completed job result and summary log, so it
is not confused with a tenant that has no detected software. The in-loop
rate-limit signal is
not user-visible; the task maps it to the generic safe message. Its last-resort
unknown failure boundary records only the exception type and traceback source
location (never the traceback text) and persists a safe generic message. It
avoids `logger.exception` so raw provider exception text and URL-bearing request
diagnostics cannot enter logs. OAuth and Graph request frames are marked with Django's
`sensitive_variables()` and transport exceptions are chained from `None` so a
credential-bearing requests frame is not surfaced as the reported cause. The
optional detected-app endpoint is intentionally non-critical:
asset discovery remains available when an ordinary retryable provider failure
hits that endpoint, but authentication, configuration and untrusted-next-link
failures are re-raised
and fail the sync. A 401 evicts only the affected Azure tenant's cached token.
The typed failure is logged with the `device_apps.list` operation and tested.
This is a capability fallback, not permission/authentication fallback.

## Follow-up boundaries

Snipe-IT, LDAP/OIDC, mail and webhook delivery must consume these shared types
in bounded follow-up slices. They must preserve their current explicit retry,
4xx, authentication and optional-dependency semantics. The Intune slice bounds
one Graph operation and its pagination, but does not impose a sync-wide deadline
across a large device fleet. The current base settings provide a 600-second
Django-Q worker timeout and 660-second retry interval as an interim containment,
but the queue/worker-timeout policy must be verified for deployment overrides in
that follow-up. WP-13/WP-16 own the
consumer work for alert/webhook delivery and must not introduce a competing
error taxonomy. SCIM capability detection follows the same optional-capability
form under WP-18.
