# Webhooks

A **Webhook Endpoint** configures the system to send real-time HTTP POST requests or payloads to external web servers or automation endpoints when matching events occur inside ITAMbox.

## Fields

### Enabled

Flag indicating if this webhook is active.

**Required:** Yes.

### Headers

Custom HTTP headers sent with the webhook call.

### HTTP Method

The HTTP verb to use (e.g. `POST`, `PUT`, `PATCH`).

**Required:** Yes.

### Name

Unique name identifying the webhook endpoint.

**Required:** Yes.

### Retry Backoff

Time in seconds to wait between retries.

**Required:** Yes.

### Retry Count

Maximum retry attempts if the delivery fails.

**Required:** Yes.

### Secret

Shared secret used to sign the payload (HMAC-SHA256 signature is included in the headers).

### Tenant

Tenant context that owns this webhook. Null implies a global/system-wide webhook.

### URL

The destination URL to send HTTP payloads to.

**Required:** Yes.


## Features & Validation

* **Security Verification**: Uses the shared secret to generate a verifiable HMAC header payload signature.
* **Automatic Retries**: Implements exponential or fixed backoff retries when destination servers respond with HTTP error statuses.
