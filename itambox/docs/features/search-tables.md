# Search, Filters, And Tables

ITAMbox provides global search for finding records across the application and list-page tools for narrowing, arranging, selecting, and exporting a specific object type.

## Global Search

Use the **Search** field in the application header when you know a name, identifier, serial number, or other indexed value but not where the record lives. Search results are limited to records the current user can access.

For physical labels and device identifiers, [Find by Scan](../usage/scanning.md) is usually faster than text search.

## List Search And Filters

Most object lists provide a quick search field plus structured filters. The quick search narrows the current object type. Structured filters are better when you need exact conditions such as tenant, status, site, date, or relationship.

Filters operate inside the active [workspace](workspaces.md). A filter never widens tenant access.

## Saved Filters

Saved Filters preserve a useful filter definition so it can be reused. Use them for recurring operational views such as expiring warranties, devices awaiting repair, or assets at one customer site.

A saved filter is a query definition, not a snapshot. The results can change as the underlying data changes.

## Table Preferences

List pages can expose configurable columns and page-size preferences. Choose columns that help the current workflow instead of trying to display every available field at once.

## Bulk Selection

Bulk actions apply to the records you select and are additionally constrained by permission, object state, and tenant scope. Review the selection before destructive actions. For scan-driven bulk asset operations, use [Scanning Assets](../usage/scanning.md).

## Exporting A Result Set

The normal export workflow uses the current list/filter context. Export Templates can provide alternate formats for supported object types. See [Reports, Exports, and Labels](../usage/reports-and-exports.md) and [Reporting Customization](../customization/reporting.md).
