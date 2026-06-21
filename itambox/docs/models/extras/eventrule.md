# Event Rules

An **Event Rule** matches database lifecycle changes (e.g. object creation, updates, deletions) and triggers corresponding actions like executing webhooks or triggering notifications.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Action Config** | Advanced JSON configurations (custom payload overrides, header mappings, etc.). | JSON | No |
| **Action Type** | Type of action to trigger (e.g., Webhook). | Choice | Yes |
| **Conditions** | Optional criteria or filter rules (e.g., match only if `status` changes to `Retired`). | JSON | No |
| **Enabled** | Flag indicating if this rule is actively monitored. | Boolean | Yes |
| **Events** | List of event types triggering this rule (e.g., `create`, `update`, `delete`). | JSON | Yes |
| **Model** | The target database model being monitored. | Foreign Key | Yes |
| **Name** | Descriptive name for the event rule. | String | Yes |
| **Tenant** | Tenant context that owns this rule. Null implies a system-wide rule. | Foreign Key | No |
| **Webhook** | Target Webhook Endpoint to invoke when the rule conditions match. | Foreign Key | No |

## Features & Validation

* **Event Filtering**: Fine-grained conditional checks to prevent webhooks from firing on minor, unrelated field updates.
* **Webhook Mapping**: Webhooks mapped under `webhook` take precedence over generic configs.
