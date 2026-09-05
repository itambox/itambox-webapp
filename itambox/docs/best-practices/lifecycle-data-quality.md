# Lifecycle Data Quality

Good asset management data is created during operations, not reconstructed later from memory.

## Prefer Relationships Over Notes

Use first-class fields and related records for Tenant, Location, Holder, Warranty, Maintenance, Purchase Order, Contract, and Cost Center whenever the information has a supported object. Reserve Notes for context that does not deserve its own structured field.

## Preserve Identifiers

Keep both Asset Tag and manufacturer Serial Number where available. Avoid reusing an old asset tag for a different physical device unless your organization has a deliberate policy and understands the historical ambiguity it creates.

## Record State Changes When They Happen

Use Check-out, Check-in, maintenance, audit, and disposal workflows instead of editing the record later to resemble the final state. The history is often as important as the current field values.

## Treat Custom Fields As Schema

A Custom Field may be easy to create, but integrations and reports can make it a long-lived contract. Define an owner, allowed values, and retirement plan before making it required.

## Review Renewal And Expiration Data

Warranties, contracts, licenses, and subscriptions only help when their dates are maintained. Review upcoming renewal/expiration reports on a schedule and correct stale ownership before alerts become urgent.
