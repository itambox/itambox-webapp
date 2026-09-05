# Event Rules

An **Event Rule** matches database lifecycle changes (e.g. object creation, updates, deletions) and triggers corresponding actions like executing webhooks or triggering notifications.

## Fields

### Action Config

Advanced JSON configurations (custom payload overrides, header mappings, etc.).

### Action Type

Type of action to trigger (e.g., Webhook).

**Required:** Yes.

### Conditions

Preserved authored conditions; withdrawn and read-only for 1.0. Authored values fail closed and do not dispatch the rule.

### Enabled

Flag indicating if this rule is actively monitored.

**Required:** Yes.

### Events

List of event types triggering this rule (e.g., `create`, `update`, `delete`).

**Required:** Yes.

### Model

The target database model being monitored.

**Required:** Yes.

### Name

Descriptive name for the event rule.

**Required:** Yes.

### Tenant

Tenant context that owns this rule. Null implies a system-wide rule.

### Webhook

Target Webhook Endpoint to invoke when the rule conditions match.


## Features & Validation

* **Condition Preservation**: Existing condition JSON remains readable, but authored conditions are not evaluated in 1.0 and fail closed.
* **Webhook Mapping**: Webhooks mapped under `webhook` take precedence over generic configs.
