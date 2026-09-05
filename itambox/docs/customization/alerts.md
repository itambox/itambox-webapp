# Alert Customization

Alert Rules define conditions that ITAMbox evaluates. Notification Channels define supported delivery destinations for matching alerts.

## Designing A Rule

Give the rule a name that describes the condition and action an operator should understand. Choose severity based on operational impact rather than using the highest severity by default.

Use the re-notification interval deliberately. `0` means the open alert is notified once. A positive number allows another notification after that many days while the alert remains open.

**Mute** when you want matching state to continue to be recorded without channel delivery. Disable a rule when it should no longer be evaluated.

## Channels

Configure only destinations that are actively monitored. Test external channels after changing credentials, URLs, mail settings, or network policy.

Do not place channel secrets in rule descriptions or other fields intended for operators.

## Review The Result

After enabling a rule, confirm that the expected target creates one open alert, acknowledgement keeps it open, resolution closes it, and a later recurrence can create a new alert.

See [Alerts and Notifications](../usage/alerts-and-notifications.md) for the operator workflow.
