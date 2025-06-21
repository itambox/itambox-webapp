# AssetBox Feature Catalog

This document details the primary features of the **AssetBox** IT Asset Management (ITAM) application, outlining their operational intent, model representations, and UI integration.

---

## 1. Procurement & Lifecycles
AssetBox captures critical procurement lifecycles to align with standard enterprise accounting and vendor management practices.

*   **Purchase Tracking:** Optional inputs for `purchase_cost`, `supplier` (vendor), and `order_number` (PO references) are recorded directly on the `Asset` model.
*   **Warranty expiration:** Automatically manages and displays calendar expirations to warn admins before vendor service agreements lapse.
*   **Visual Representation:** Procurement details are formatted elegantly inside standard 3-column layouts inside forms, and displayed on the primary details page under a unified procurement card.

---

## 2. Dynamic Status Labels
Instead of a static dropdown list of states, inventory flow is driven by a database-backed **Status Label** model.

*   **Metadata Classification:** Statuses are grouped under four high-level states:
    1.  `Deployable`: Available to be assigned to holders/locations.
    2.  `Pending`: Awaiting configuration or sorting.
    3.  `Undeployable`: Out of service, damaged, or undergoing audits.
    4.  `Archived`: Retired, sold, or disposed of.
*   **Dynamic Visual Indicators:** Each status label defines a custom hex color code. Asset list and detail tables dynamically compute and render badging using beautiful, premium semi-transparent HSL variations of the configuration color.

---

## 3. Cryptographic Security at Rest
To protect corporate software license keys and activation credentials, AssetBox symmetrically encrypts sensitive fields before they touch the database.

*   **Fernet AES Encryption:** Utilizes symmetric `cryptography.fernet` cryptography.
*   **Zero-Config Key Derivation:** To keep local setup simple, a cryptographically secure 32-byte Fernet key is dynamically derived from Django's configured `SECRET_KEY` using SHA-256 and base64 encoding.
*   **Sentinel Pattern (`enc$`):** Encrypted entries are prefixed with a sentinel header. Plaintext records in legacy tables remain 100% backwards-compatible, falling back cleanly to plaintext strings without breaking.

---

## 4. Asset Maintenance Ledger (TCO)
IT departments must know what equipment costs to maintain. AssetBox features a repair log that aggregates maintenance operations into **Total Cost of Ownership (TCO)**.

*   **Operations Log:** Records repair actions, upgrades, software calibrations, and hardware support events with dates, supplier links, and precise costs.
*   **Downtime Tracking:** An asset's out-of-service repair span is dynamically computed and presented in downtime calendar days.
*   **TCO Aggregation:** `Asset.total_cost_of_ownership` dynamically calculates the initial purchase cost plus the cost of all completed maintenance actions.

---

## 5. Dynamic JSON-based Custom Fieldsets
To track unique hardware specifications (like mobile phone SIM details, vehicle vehicle identification numbers, or desk serials) without polluting database schemas, AssetBox implements a flexible dynamic field system.

*   **Custom Fields (`CustomField`):** Supports fields of type `text`, `number`, `date`, `boolean`, and `select` / dropdown.
*   **Asset Type Fieldsets (`CustomFieldset`):** Allows grouping fields together (e.g. "SIM UPN & Screen Size" for mobile phones) and attaching them to an `AssetType`.
*   **Dynamic UI Forms:** `AssetForm` parses the associated fieldset on the fly, rendering correct HTML widgets and saving input values directly inside a standard database `JSONField` (`Asset.custom_values`).
*   **Humanizer Filter:** A smart templatetag filter (`|humanize_key`) converts JSON key slugs (e.g., `sim_card_number`) into beautiful visual headers (e.g. `SIM Card Number`) on details cards.

---

## 6. Pre-defined Onboarding Kits
Onboarding new hires is a standard operation. AssetBox groups diverse assets, software seats, and accessories together as **Kits** to allow single-click assignments.

*   **Kit Configurations (`Kit` / `KitItem`):** Bundles can contain a selection of hardware `AssetTypes`, stock `Accessories` (e.g. keyboards, chargers), and software `Licenses`.
*   **Atomic Transactions:** Checked out in a single click via `KitCheckoutView` at `/assets/kits/<pk>/checkout/`.
*   **All-or-Nothing Integrity:** The checkout runs inside a database transaction block. If a single component of the kit is out-of-stock, the entire operation rolls back safely, showing a detailed form validation error list.

---

## 7. Straight-Line Depreciation & Valuation
Allows corporate accounting and tax auditors to audit current equipment asset values in real-time.

*   **Depreciation Schedules (`Depreciation`):** Defines useful lifespans in calendar months (e.g. `36 months`).
*   **Straight-Line Mathematical Model:**
    $$\text{Book Value} = \text{Purchase Cost} - \left( \frac{\text{Purchase Cost} - \text{Salvage Value}}{\text{Lifespan Months}} \times \text{Months Held} \right)$$
*   **Boundary Handlers:** Gracefully caps the value at the `salvage_value` when the lifespan expires, handles future-dated purchases safely, and protects against division-by-zero errors.
*   **Dynamic Visuals:** Book value is updated dynamically inside the "Financial Details" column with capital preservation progress bars.
