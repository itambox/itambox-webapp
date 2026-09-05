# Warranties

A **Warranty** represents a manufacturer or third-party warranty agreement covering one or more physical assets. It defines coverage dates, supplier references, and terms of service.

## Fields

### Asset

The physical asset covered under this warranty.

**Required:** Yes.

### Cost

Recorded cost for this record.

### Currency

ISO 4217 code. Leave blank to use the tenant default currency.

### End Date

The date the warranty coverage expires.

**Required:** Yes.

### Notes

Optional comments or details on terms.

### Provider

e.g. "Dell ProSupport Plus"

### Reference

Claim number, policy reference, or contract ID.

### Start Date

The date the warranty coverage begins.

**Required:** Yes.

### Terms

Recorded terms or coverage notes for this record.

### Warranty Type

Coverage category for this warranty.

**Required:** Yes.


## Features & Validation

* **Coverage Checks**: Automatic warning flags when the warranty is expired or close to expiration.
* **Date Consistency**: Validates that `end_date` is on or after `start_date`.
