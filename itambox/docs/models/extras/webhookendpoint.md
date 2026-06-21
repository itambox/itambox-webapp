# Webhooks

A **Webhook Endpoint** configures the system to send real-time HTTP POST requests or payloads to external web servers or automation endpoints when matching events occur inside ITAMbox.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Enabled** | Flag indicating if this webhook is active. | Boolean | Yes |
| **Headers** | Custom HTTP headers sent with the webhook call. | JSON | No |
| **HTTP Method** | The HTTP verb to use (e.g. `POST`, `PUT`, `PATCH`). | Choice | Yes |
| **Name** | Unique name identifying the webhook endpoint. | String | Yes |
| **Retry Backoff** | Time in seconds to wait between retries. | Integer | Yes |
| **Retry Count** | Maximum retry attempts if the delivery fails. | Integer | Yes |
| **Secret** | Shared secret used to sign the payload (HMAC-SHA256 signature is included in the headers). | String | No |
| **Tenant** | Tenant context that owns this webhook. Null implies a global/system-wide webhook. | Foreign Key | No |
| **URL** | The destination URL to send HTTP payloads to. | String | Yes |

## Features & Validation

* **Security Verification**: Uses the shared secret to generate a verifiable HMAC header payload signature.
* **Automatic Retries**: Implements exponential or fixed backoff retries when destination servers respond with HTTP error statuses.
