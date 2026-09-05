# Alerts And Notifications

Alerts represent conditions that need attention. Notifications are delivery records/messages used to surface activity to people and channels. An Alert can exist without being repeatedly delivered on every evaluation cycle.

## Alert Lifecycle

An alert can be **Active**, **Acknowledged**, or **Resolved**.

- Active means the condition is currently open.
- Acknowledged means someone has seen the alert, but the condition is still open.
- Resolved means the condition is no longer open or has been closed through the supported workflow.

ITAMbox keeps at most one open alert for the same rule and target object. Re-evaluating a condition that is already Active or Acknowledged does not create another open duplicate. After an alert is Resolved, the same condition can create a new alert if it occurs again.

## Alert Rules

An Alert Rule defines what is evaluated, severity, whether the rule is active, whether it is muted, and how often open alerts may notify again.

A re-notification interval of `0` means notify once for that open alert rather than periodically. A positive interval allows another delivery after that many days while the alert remains open.

Muting suppresses delivery while allowing the rule's matching state to be recorded. Disabling a rule stops it from being evaluated.

See [Alert Customization](../customization/alerts.md) for rule and channel setup.

## Notification Channels

Notification Channels define where supported alert notifications are sent, such as in-application or configured external destinations. Delivery availability depends on the configured channel type and environment.

## Operational Expectations

Alert evaluation is scheduled work, not a promise of instantaneous detection. If alerts across unrelated rules stop updating, check [Background Job Administration](../administration/background-jobs.md).

Use [Alert Log](../models/extras/alertlog.md), [Alert Rule](../models/extras/alertrule.md), and [Notification Channel](../models/extras/notificationchannel.md) for stored-field reference.
