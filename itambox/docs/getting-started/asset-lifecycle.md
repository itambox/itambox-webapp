# Recording the Asset Lifecycle

An Asset should tell the story of one physical item from acquisition through assignment, maintenance, audit, and eventual disposal.

## Create Or Receive The Asset

Create the Asset manually, import it, discover it from a supported source, or receive it through procurement where supported. Record the tenant, Asset Type, identifiers, location, purchase information, and an appropriate Status Label.

Asset tags identify ITAMbox inventory. Serial numbers identify manufacturer hardware. Keep both when they are available.

## Put The Asset Into Service

Use **Check-out** to assign the asset to an Asset Holder, Location, or another Asset. An active assignment represents current custody or placement. If an already assigned asset is checked out again through a supported reassignment workflow, the previous assignment is closed before the new one becomes active.

Use **Check-in** when custody ends. The check-in closes the active assignment and returns the asset to an appropriate operational status/location based on the workflow choices.

See [Assets and Assignments](../features/assets-assignments.md).

## Maintain Evidence

Add warranties, maintenance records, notes, audit results, and custody records as they occur. Avoid reconstructing the history only when an audit or incident happens.

For physical verification, use [Audits and Custody](../features/audits-custody.md) and [Scanning Assets](../usage/scanning.md).

## Dispose Of The Asset

Disposal is an explicit lifecycle event, not just a label change. Record the method and applicable financial or sanitization evidence. Bulk disposal can run as a [Background Job](../features/background-jobs.md).

For data-quality practices, see [Lifecycle Data Quality](../best-practices/lifecycle-data-quality.md).
