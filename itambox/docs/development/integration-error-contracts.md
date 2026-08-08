# External integration error contracts

Issue #99 defines one shared contract for failures at external integration
boundaries. This document is the consumer contract; provider adapters decide
how their protocol maps into it.

## Two independent axes

`IntegrationError.disposition` is either:

- `retryable`: the operation may be attempted again later (for example a
  transport failure, HTTP 5xx, or an in-budget HTTP 429);
- `terminal`: retrying the same operation without an external/configuration
  change is not useful (for example invalid credentials, a non-retryable 4xx,
  an invalid success payload, or an exhausted local retry budget).

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
- `IntegrationRetryBudgetExceededError` — terminal for the current bounded
  invocation after rate-limit budget exhaustion;
- `IntegrationContractError` — terminal malformed/unexpected success response;
- `IntegrationRequestError` and `IntegrationNotFoundError` — terminal 4xx
  request/resource failures;
- `IntegrationUnexpectedError` — terminal task-isolation fallback for an
  otherwise unknown failure, with no provider exception text persisted.

`IntegrationContext` is an allowlist containing only provider, stable operation,
tenant ID, actor ID and request/correlation ID. `IntegrationError.log_extra()`
returns these fields plus code, disposition and HTTP status for `logging.extra`.
It never includes a request URL, headers, payload, response body or exception
string.

## Retry rules in the Intune slice

The Microsoft Graph adapter classifies transport failures/5xx as retryable for the
caller and handles HTTP 429 with a finite per-operation `RetryBudget` (not
shared across devices). Both retry count and elapsed wall-clock time are bounded,
and provider `Retry-After` is parsed defensively, made finite, and clamped before
sleeping. Graph pagination accepts only HTTPS `graph.microsoft.com/v1.0/...`
next links and never sends the bearer header to an arbitrary provider-controlled
next-link host. OAuth 429 responses produce a bounded retryable signal, while
Graph collection 429 responses consume the local budget. The adapter does not
retry transport/5xx failures in-process or re-enqueue a django-q job in this
slice; queue re-enqueue is a separate follow-up decision.

HTTP 401/403, 404 and other 4xx responses are terminal. A successful response
without valid JSON/value or an OAuth response without a non-empty
`access_token` is a terminal contract error.

The Intune task catches `IntegrationError` explicitly, logs only the structured
allowlist, and persists `display_message()`. Optional software degradation is
counted as `software_degraded` in the completed job result and summary log, so it
is not confused with a tenant that has no detected software. The in-loop
rate-limit signal is
not user-visible; the task maps it to the generic safe message. Its last-resort
unknown failure boundary records only the exception type and traceback source
location (never the traceback text) and persists a safe generic message. It
does not use `logger.exception` while provider credential locals may still be
present. OAuth and Graph request frames are marked with Django's
`sensitive_variables()` and transport exceptions are chained from `None` so a
credential-bearing requests frame is not retained as the public cause. The
optional detected-app endpoint is intentionally non-critical:
asset discovery remains available when an ordinary retryable provider failure
hits that endpoint, but authentication and configuration failures are re-raised
and fail the sync. A 401 evicts only the affected Azure tenant's cached token.
The typed failure is logged with the `device_apps.list` operation and tested.
This is a capability fallback, not permission/authentication fallback.

## Follow-up boundaries

Snipe-IT, LDAP/OIDC, mail and webhook delivery must consume these shared types
in bounded follow-up slices. They must preserve their current explicit retry,
4xx, authentication and optional-dependency semantics. WP-13/WP-16 own the
consumer work for alert/webhook delivery and must not introduce a competing
error taxonomy. SCIM capability detection follows the same optional-capability
form under WP-18.
