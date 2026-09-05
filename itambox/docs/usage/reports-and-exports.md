# Reports, Exports, And Labels

ITAMbox has several output workflows. Choose the one that matches the question instead of treating every downloadable file as a report.

## List Exports

Use an Export action from an object list when you need the records in the current authorized result set. Filters narrow the output. Export Templates can provide custom formats for supported object types.

See [Search, Filters, and Tables](../features/search-tables.md) and [Reporting Customization](../customization/reporting.md).

## Curated Reports

Curated Reports provide predefined operational views such as inventory, renewals, maintenance, warranty, disposal, or utilization information. Their scope follows the report and the user's current authorization.

## Report Designer

**Report Designer** is a separate capability from curated reports and is controlled by `ITAMBOX_FEATURE_REPORT_DESIGNER`. It is not enabled merely because ordinary Reports are available.

When enabled, administrators can define Report Templates and choose supported datasets/columns. See [Feature Activation](../configuration/feature-activation.md) and [Reporting Customization](../customization/reporting.md).

## Scheduled Reports

A Scheduled Report stores a report definition, schedule, delivery settings, and scope. Cross-tenant schedules can require a durable scope approval before delivery is allowed. If authorization changes, delivery must not continue merely because an old schedule still exists.

See [Report Scope Approvals](../administration/report-scope-approvals.md).

## Labels

Use **Print Labels** from supported lists to select a Label Template and generate output for the selected records. Large/batch label generation can run as a [Background Job](../features/background-jobs.md) and produce a PDF attachment on the Job.

Label Templates define physical dimensions and barcode/QR behavior. See [Reporting Customization](../customization/reporting.md) and [Label Template](../models/extras/labeltemplate.md).

## Generated Files And Scope

Generated report or label files do not become public simply because generation completed. Job attachment downloads are authorized against current access. If the user's tenant access is revoked, an old file link must not preserve access to data they can no longer view.
