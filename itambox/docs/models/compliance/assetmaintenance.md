# Asset Maintenances

An **Asset Maintenance** logs repair tickets, hardware upgrades, support calls, calibration schedules, or vendor maintenance services performed on a physical asset.

## Fields

### Asset

The physical serialized system receiving the maintenance.

**Required:** Yes.

### Completion Date

Date the maintenance was completed.

### Cost

Direct monetary cost of the service.

### Currency

ISO 4217 code. Leave blank to use the tenant default currency.

### Description

Human-readable description of this asset maintenance.

### Maintenance Type

Choice of: `Upgrade`, `Repair`, `Calibration`, `Software Support`, `Hardware Support`.

**Required:** Yes.

### Notes

Detailed log notes.

### Performed By

Name of the specific engineer or entity doing the work.

### Start Date

Date the maintenance work began.

**Required:** Yes.

### Status

State of work: `Scheduled`, `In Progress`, `Completed`, `Cancelled`.

**Required:** Yes.

### Supplier

The external vendor performing the maintenance service.


## Downtime Calculation
ITAMbox automatically calculates and displays the total downtime duration in days as the difference between the `Completion Date` and the `Start Date`.
