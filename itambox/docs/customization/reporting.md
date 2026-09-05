# Reporting Customization

ITAMbox supports several administrator-defined output objects: Export Templates, Label Templates, Report Templates, and Scheduled Reports. They serve different purposes and should be managed separately.

## Export Templates

An Export Template is associated with a supported object type and renders records from an authorized list result. Use it when consumers need a format other than the standard export.

Template code is administrator-controlled executable template logic. Keep it small, deterministic, and focused on presentation. Do not attempt to use an export template as an authorization layer.

## Label Templates

A Label Template controls physical label dimensions and barcode/QR presentation. Test a template with the actual printer and media before producing a large batch.

## Report Templates

Report Templates belong to the Report Designer capability. Report Designer can be disabled even while curated Reports remain available. Choose only columns and grouping that answer the report's operational question.

## Scheduled Reports

A Scheduled Report combines a report with timing, delivery, and scope. Cross-tenant schedules can require [Report Scope Approval](../administration/report-scope-approvals.md). Review schedules when tenant access, recipients, or responsibilities change.

See [Reports, Exports, and Labels](../usage/reports-and-exports.md) for the user workflow and the corresponding Data Model pages for exact fields.
