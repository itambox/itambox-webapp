# Webhooks And Event Rules

Event Rules let ITAMbox react to supported object events. A rule can connect an event to actions such as sending a webhook or creating a notification, depending on the configured action and current capability support.

## Event Rules

An Event Rule selects the object type, event type, action, and enabled state. Keep a rule narrow enough that an operator can predict when it will run.

Condition authoring is deliberately limited in the current release. If the UI marks conditions as unavailable or withdrawn, do not assume a hidden condition will still be enforced. Review the rule detail and current capability information before relying on conditional delivery.

### Event Rule Conditions Withdrawn For 1.0 {#event-rule-conditions-withdrawn-for-10}

Condition authoring is not part of the supported 1.0 rule contract. Existing rules should be reviewed without assuming a previously configured condition continues to filter delivery.

## Webhook Endpoints

A Webhook Endpoint defines the receiving URL, HTTP method, headers, secret, enabled state, and retry policy used for outbound delivery.

Webhooks are an external integration contract. Receivers should validate the delivery identifier/signature information supplied by ITAMbox, return an appropriate success response promptly, and tolerate legitimate retries or redeliveries.

## Delivery And Retry

Webhook delivery is durable background work. A delivery records attempts, response information, retry timing, and final state. Retry count/backoff settings control automatic retry behavior. Operators can inspect delivery history when a receiver is unavailable.

Do not put secrets in event payload content, logs, or human-readable rule names. Treat webhook URLs, headers, and signing secrets as credentials.

## Troubleshooting

If one endpoint fails, inspect its recent delivery records and the receiving service. If unrelated webhooks stop progressing, inspect the background worker health described in [Background Job Administration](../administration/background-jobs.md).

For exact API and stored fields, see [Webhook Endpoint](../models/extras/webhookendpoint.md) and [Event Rule](../models/extras/eventrule.md).
