# Testing & Verification Guide

This document outlines the testing methodologies, automated test suites, and manual validation checklists used to guarantee structural and transactional integrity across **AssetBox**.

---

## 1. Automated Test Suite

AssetBox is covered by a robust automated Django test suite consisting of **37 unit and integration tests** validating data schemas, cryptographic properties, transaction rollbacks, HTMX renders, and mathematical formulas.

### Executing the Tests

To run the full test suite, navigate to the `assetbox` project directory and run:

```powershell
# Run the entire test suite
..\.venv\Scripts\python manage.py test

# Run only the assets application tests
..\.venv\Scripts\python manage.py test assets
```

### Key Test Case Coverages

#### 1. Dynamic Custom Fields (`test_dynamic_custom_fieldsets_and_form_saving`)
*   Creates `CustomField` and `CustomFieldset` configs.
*   Binds the fieldset to an `AssetType`.
*   Programmatically binds data values via `AssetForm` (validating string coercions, date formats, and selection values).
*   Verifies correct saving of configurations inside `Asset.custom_values` JSON.
*   Asserts correct form instantiation and reloading on edit requests.

#### 2. Straight-Line Depreciation (`test_straight_line_depreciation_math`)
*   Asserts correct valuation calculations on standard milestones.
*   Tests middle durations (e.g. 6 months into a 12-month schedule).
*   Tests fully-depreciated limits, verifying that calculations correctly cap book values at the specified `salvage_value` post-lifespan.
*   Validates edge cases (e.g., zero purchase costs, zero depreciation months, or future purchase dates).

#### 3. Atomic Onboarding Kits (`test_atomic_kit_checkout_flow`)
*   Configures a Kit containing a hardware `AssetType`, stock `Accessory`, and software `License`.
*   **Rollback Verification:** Attempts checkout when the hardware type has zero available assets in stock. Asserts that checkout fails, and verifies that the transaction rolls back cleanly—meaning no accessory counts are decremented and no software seat assignments are created.
*   **Success Verification:** Supples available assets, submits the kit checkout, and asserts that assets are updated to `in-use`, accessory stock decrements, and license seat assignments link to the holder in a single database transaction.

---

## 2. Manual Verification Checklist

Follow these checklists to manually verify dynamic features inside the AssetBox administration panel:

### Checklist A: Dynamic Custom Fields
1.  Navigate to **Manage > Custom Fields** and click **Add Custom Field**.
2.  Create a field `licence_plate` (`text`) and another `fuel_type` (`select` with choices: `electric`, `petrol`, `diesel`).
3.  Navigate to **Manage > Custom Fieldsets** and click **Add Custom Fieldset**.
4.  Group your new fields under a fieldset named "Vehicle Specs".
5.  Create or Edit an **Asset Type** (e.g., "Company Car") and select "Vehicle Specs" as the custom fieldset.
6.  Navigate to **Assets** and click **Create Asset** selecting "Company Car" as the type.
7.  Verify that:
    *   The form dynamically updates to display the `Licence Plate` text field and the `Fuel Type` dropdown.
    *   Dynamic validation operates (saving a blank value in a required custom field fails with standard form feedback).
8.  Save the asset and open its details page.
    *   Verify that your specifications render dynamically under the specifications card, formatted correctly with humanized labels.

### Checklist B: Onboarding Kits
1.  Navigate to **Manage > Kits** in the Sidebar.
2.  Click **Add Kit** and name it "Developer Onboarding Kit".
3.  Add items to the Kit:
    *   An `AssetType` (e.g. "MacBook Pro").
    *   An `Accessory` with quantity (e.g. "Apple USB-C Charger", `1`).
    *   A software `License` (e.g. "Microsoft 365 Enterprise").
4.  Verify that your Kit details list displays the requested bundle components and stock status.
5.  Click the **Check Out Kit** action button.
6.  Select an employee as the target holder and click submit.
7.  Verify that:
    *   The kit checkout succeeds instantly.
    *   Opening the target employee's details page shows all three allocations linked together (the MacBook Pro asset, the USB-C charger accessory, and the Microsoft 365 license seat).

### Checklist C: Straight-Line Depreciation
1.  Navigate to **Manage > Depreciations** in the Sidebar.
2.  Click **Add Depreciation** and create a schedule named "Laptop Lifespan (12 Months)".
3.  Create or Edit an **Asset Type** and attach the "Laptop Lifespan" schedule.
4.  Create an **Asset** of that type with:
    *   `purchase_date`: Exactly 6 months ago today.
    *   `purchase_cost`: `1000.00`.
    *   `salvage_value`: `200.00`.
5.  Open the Asset details page.
    *   Verify that the "Financial Details" card displays the **Current Depreciated Book Value** as precisely `$600.00` (representing the 50% depreciated value post-salvage base).
