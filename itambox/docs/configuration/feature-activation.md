# Feature Activation

Some ITAMbox capabilities are always available, while others require an operator setting or an enabled application object. [Capability Maturity](../operations/capability-maturity.md) is the canonical source for maturity and activation mode.

Examples of deployment-controlled activation in the reviewed release include:

- `ITAMBOX_FEATURE_REPORT_DESIGNER` for Report Designer;
- `ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS` for Asset Request auto-approval policy;
- `ITAMBOX_PLUGINS` for configured plugins.

Changing an environment setting normally requires the affected application processes to restart. Do not describe an inactive feature as broken until its activation mode has been checked.

Object-enabled capabilities such as Event Rules and Alert Rules require both the underlying feature and an enabled/configured object. Creating a disabled rule does not activate its behavior.
