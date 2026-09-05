# Custody Templates

A **Custody Template** defines the terms of service, End User License Agreements (EULAs), disclaimer statements, and digital signature requirements when checking out assets to holders (users).

Custody Templates can be scoped globally, to specific Tenant Groups (e.g. parent conglomerate scope), or to individual Tenants (e.g. division-specific scope). Additionally, a template can be restricted to override terms for a specific asset **Category** (e.g., restricting a strict EULA to Category: `High-Risk Production Laptops` within Tenant: `R&D`).

---

## Fields

### Category

The asset category this template overrides for the specified scope.

### Disclaimer

Short disclaimer printed at the bottom of the signoff receipt.

### Email Signature Request

Sends an email containing a secure signature link to the holder.

**Required:** Yes.

### EULA Text

The legal terms of service shown to the end-user.

**Required:** Yes.

### Is Active

Deactivate to hide from choices

**Required:** Yes.

### Logo

Custom corporate logo printed on the receipt.

### Name

Descriptive name for this template (e.g. *Standard Laptop EULA*).

**Required:** Yes.

### QMS Reference

Reference ID mapping to an internal Quality Management System document.

### Require Acceptance

If checked, checkout blocks/restricts asset status until signed.

**Required:** Yes.

### Signature Provider

The service handling the signature process (e.g., `local` for on-platform canvas, or plugin integrations like `docusign`).

**Required:** Yes.

### Tenant

The specific Tenant scope that this template applies to.

### Tenant Group

The Tenant Group scope that this template applies to.


## EULA Overrides & Precedence

When checking out an asset governed by category-level acceptance rules, ITAMbox resolves the active EULA template using the following fallback precedence:
1. **Category + Tenant Match**: A Custody Template targeted at the asset's specific Category and Tenant.
2. **Tenant Match**: A Custody Template targeted globally at the asset's Tenant.
3. **Tenant Group Match**: A template targeted at the Tenant's parent group.
4. **Global Category Fallback**: If no scope-specific template is found, it falls back to the static `EULA Text` defined on the asset's Category model.
