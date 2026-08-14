# Event Rules

An **Event Rule** matches database lifecycle changes (e.g. object creation, updates, deletions) and triggers corresponding actions like executing webhooks or triggering notifications.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Action Config** | Advanced JSON configurations (custom payload overrides, header mappings, etc.). | JSON | No |
| **Action Type** | Type of action to trigger (e.g., Webhook). | Choice | Yes |
| **Conditions** | Preserved authored conditions; withdrawn and read-only for 1.0. Authored values fail closed and do not dispatch the rule. | JSON | No |
| **Enabled** | Flag indicating if this rule is actively monitored. | Boolean | Yes |
| **Events** | List of event types triggering this rule (e.g., `create`, `update`, `delete`). | JSON | Yes |
| **Model** | The target database model being monitored. | Foreign Key | Yes |
| **Name** | Descriptive name for the event rule. | String | Yes |
| **Tenant** | Tenant context that owns this rule. Null implies a system-wide rule. | Foreign Key | No |
| **Webhook** | Target Webhook Endpoint to invoke when the rule conditions match. | Foreign Key | No |

## Features & Validation

* **Condition Preservation**: Existing condition JSON remains readable, but authored conditions are not evaluated in 1.0 and fail closed.
* **Webhook Mapping**: Webhooks mapped under `webhook` take precedence over generic configs.
