# AssetBox — End-User Guide

**Version 0.1.0**

AssetBox is an enterprise-grade IT Asset Management (ITAM) platform designed to
help your organization track hardware, software, accessories, and consumables
from procurement through disposal. This guide covers everything an end user
needs to know to operate AssetBox effectively.

---

## Table of Contents

1. [Introduction](#introduction)
2. [Getting Started](#getting-started)
3. [User Interface Overview](#user-interface-overview)
4. [Managing Assets](#managing-assets)
5. [Organization Management](#organization-management)
6. [Software & Licenses](#software--licenses)
7. [Onboarding Kits](#onboarding-kits)
8. [Asset Maintenance & TCO](#asset-maintenance--tco)
9. [Depreciation & Valuation](#depreciation--valuation)
10. [Custom Fields & Fieldsets](#custom-fields--fieldsets)
11. [Global Search](#global-search)
12. [Bulk Operations](#bulk-operations)
13. [Audit Trail (Changelog)](#audit-trail-changelog)
14. [Asset Label Printing](#asset-label-printing)
15. [User Profile & Preferences](#user-profile--preferences)
16. [Troubleshooting](#troubleshooting)
17. [Frequently Asked Questions](#frequently-asked-questions)

---

## Introduction

### What Is AssetBox?

AssetBox provides a single place to record, track, and manage every piece of
equipment your organization owns. It is built for IT departments and asset
managers who need to:

- Maintain an accurate inventory of computers, monitors, phones, and peripherals
- Track software product keys securely using encryption
- Manage license seat assignments across employees
- Calculate the current book value of hardware using straight-line depreciation
- Create pre-defined onboarding kits for new hires
- Record maintenance events and calculate Total Cost of Ownership (TCO)

### Key Concepts

| Term | Meaning |
|---|---|
| **Asset** | A physical item you track by serial number (laptop, monitor, server) |
| **Asset Type** | A category template that defines default specifications for an asset |
| **Accessory** | Non-serialized bulk items tracked by quantity (keyboards, chargers) |
| **Consumable** | One-time-use stock items (toner cartridges, batteries) |
| **License** | A software entitlement tracked by seat count |
| **Kit** | A pre-configured bundle of hardware, accessories, and licenses for onboarding |
| **Status Label** | A database-driven tag that describes an asset's current state |

---

## Getting Started

### System Access

AssetBox is a web-based application. Your system administrator will provide you
with a URL (such as `https://assetbox.yourcompany.com`) and login credentials.

1. Open the AssetBox URL in a modern web browser (Chrome, Firefox, Edge, or
   Safari).
2. Enter your username and password on the login page.
3. Click **Sign In**.

### First-Time Login

After logging in for the first time:

1. Navigate to your **User Profile** by clicking your avatar in the top-right
   corner and selecting **Profile**.
2. Review and update your display name and contact information if needed.
3. Visit **Preferences** to set your preferred color theme (light / dark).

### Navigation

The sidebar on the left provides access to all areas of the application. Each
section is organized into logical groups:

- **Home** — Returns to the Dashboard
- **Organization** — Sites, tenants, asset holders, and contacts
- **Assets** — Hardware assets, types, components, accessories, consumables,
  kits, and depreciation schedules
- **Software & Licenses** — Software catalog and license entitlements
- **Extras** — Tags, config templates, and custom field schemas
- **Operations** — Maintenance records and audit changelog
- **Admin** — Django administration panel (restricted access)

---

## User Interface Overview

### The Dashboard

The Dashboard is your landing page after login. It displays six customizable
widgets:

| Widget | Shows |
|---|---|
| **Financial Overview** | Total Cost of Ownership, purchase costs, maintenance costs, salvage values |
| **Asset Status Labels** | Count of assets in each status, color-coded with progress bars |
| **Software License Seats** | License utilization rates with color-coded warning thresholds |
| **Active Repairs** | Ongoing maintenance tasks with downtime and costs |
| **EOL Planning Alerts** | Assets approaching End-of-Life within 90 days |
| **Recent Activity** | Latest audit trail entries showing who changed what |

You can personalize the dashboard:
- **Toggle widgets** on/off using the **Customize Widgets** dropdown in the
  top-right of the dashboard header.
- **Resize widgets** using the S / M / L buttons in each widget's header.
- **Reorder widgets** by dragging the grip handle (⋮⋮⋮) or clicking the arrow
  buttons.
- **Reset** the dashboard to defaults using the **Reset Dashboard** button.

### List Pages

Most sections use a table-based list page. On every list page you can:

- **Sort columns** by clicking column headers
- **Filter** results using the filter form (when available)
- **Search** using the global search bar (see [Global Search](#global-search))
- **Add new records** using the **+ Add** button in the page header or sidebar
- **Select rows** via checkboxes for bulk delete operations
- **View details** by clicking on a row's name or the detail icon

### Detail Pages

Clicking a record opens its detail page, which displays all stored information
organized into cards. Action buttons (Edit, Delete, Check Out, Check In, Print
Label) appear in the page header.

### Forms

Create and Edit pages use structured forms. Required fields are marked. Form
validation runs when you submit — any errors are displayed inline near the
affected field.

---

## Managing Assets

Assets are the core of AssetBox. Each asset represents a physical hardware item
tracked by serial number.

### Viewing All Assets

Navigate to **Assets > Assets** from the sidebar. The list shows each asset's
name, type, status, assigned holder, and location.

### Creating an Asset

1. Navigate to **Assets > Assets**.
2. Click the **+** icon next to "Assets" in the sidebar, or the **Add** button
   on the list page.
3. Fill in the asset details:

| Field | Description |
|---|---|
| **Name** | A descriptive label (e.g. "MacBook Pro — Rene Rettig") |
| **Asset Type** | Select a type (e.g. "Laptop", "Monitor"). This may trigger custom fields. |
| **Serial Number** | The hardware serial number |
| **Status** | The current lifecycle status |
| **Holder** | The person or location assigned to this asset |
| **Location / Site** | Physical location |
| **Purchase Date** | Date of acquisition |
| **Purchase Cost** | Original purchase price |
| **Supplier** | Vendor name |
| **Order Number** | Purchase order reference |
| **Warranty Expiration** | Date the vendor warranty expires |
| **Notes** | Free-text description |

4. If the selected **Asset Type** has a custom fieldset attached, additional
   dynamic fields will appear below the standard fields.
5. Click **Save**.

### Editing an Asset

1. Open the asset's detail page.
2. Click the **Edit** button in the page header.
3. Modify the fields as needed.
4. Click **Save**.

### Checking Out an Asset

Checking out assigns an asset to a specific holder (employee, department, or
location).

1. Open the asset's detail page.
2. Click **Check Out** in the page header.
3. In the modal dialog, select the target holder.
4. Optionally add notes.
5. Click **Check Out**.

### Checking In an Asset

Checking in returns an asset to available inventory.

1. Open the asset's detail page.
2. Click **Check In** in the page header.
3. The asset is immediately returned to inventory.

### Deleting an Asset

1. Open the asset's detail page.
2. Click **Delete** in the page header.
3. Confirm the deletion on the confirmation page.

---

## Organization Management

The Organization section models your company's structure.

### Sites, Regions, and Locations

- **Sites** represent physical offices or facilities.
- **Regions** group sites geographically.
- **Locations** represent specific rooms, floors, or racks within a site.

Navigate to **Organization > Sites** (or Regions, Locations) to view, create,
edit, or delete these entities.

### Asset Holders

Asset Holders represent the people or departments to whom assets can be
assigned. To create an asset holder:

1. Navigate to **Organization > Asset Holders**.
2. Click the **+** icon.
3. Fill in the name and contact details.
4. Click **Save**.

### Viewing Assignments

Navigate to **Organization > Assignments** to see a historical log of all asset
checkout and checkin events, including who received what and when.

### Contacts

Contacts are external or internal points of contact associated with vendors,
departments, or sites. Each contact can have a **Contact Role** (e.g. "Account
Manager", "Support Contact").

---

## Software & Licenses

### Software Products

The Software section tracks the catalog of applications your organization uses.

Navigate to **Software & Licenses > Software Products** to view, create, edit,
or delete software entries.

### Licenses

Licenses track software entitlements. Each license has a **seat count**
representing how many users can be assigned.

1. Navigate to **Software & Licenses > Licenses**.
2. Click **+** to add a new license.
3. Enter the license name, software product, seat count, and product key.

#### Product Key Encryption

Product keys entered into AssetBox are **encrypted at rest** using AES-256
encryption. The key is stored securely in the database and cannot be read in
plaintext by anyone with direct database access. When you view a license in the
application, the key is automatically decrypted for display.

> **Note:** You may see existing records with plaintext product keys. These
> are legacy entries that have not yet been encrypted. As soon as you edit and
> save them, they will be encrypted automatically.

---

## Onboarding Kits

Kits are pre-configured bundles that allow you to assign multiple items to a
new hire with a single click.

### Creating a Kit

1. Navigate to **Assets > Onboarding Kits**.
2. Click **+** to add a new kit.
3. Give the kit a name (e.g. "Developer Onboarding Kit").
4. Add **Kit Items** to the kit:
   - An **Asset Type** (e.g. "MacBook Pro") — one asset of this type will be
     checked out from available inventory.
   - An **Accessory** with quantity (e.g. "USB-C Charger", 1 unit).
   - A **License** (e.g. "Microsoft 365 Enterprise") — one seat will be
     assigned.
5. Click **Save**.

### Checking Out a Kit

1. Open the kit's detail page.
2. Click **Check Out Kit**.
3. Select the target employee (asset holder).
4. Click **Check Out**.

The system processes the entire kit in a single **atomic transaction**. This
means:

- If all items are available, everything is assigned at once.
- If any single item is out of stock (e.g. no available MacBooks), the entire
  operation is **rolled back** — nothing is changed — and you will see an error
  message explaining which item was unavailable.

---

## Asset Maintenance & TCO

The Maintenance module logs repair events and calculates the Total Cost of
Ownership for each asset.

### Recording a Maintenance Event

1. Navigate to **Operations > Maintenances**.
2. Click **+** to add a new maintenance record.
3. Select the **Asset** being serviced.
4. Choose the **Maintenance Type** (Repair, Upgrade, Calibration, etc.).
5. Enter the **Start Date** and optionally an **End Date**.
6. Enter the **Cost** of the maintenance.
7. Optionally record the **Supplier** and any **Notes**.
8. Click **Save**.

### Viewing TCO

The Total Cost of Ownership for an asset is calculated automatically as:

> **TCO = Purchase Cost + Sum of All Maintenance Costs**

You can view an asset's TCO on its detail page under the "Financial Details"
card, and aggregated across all assets on the Dashboard's Financial Overview
widget.

### Downtime Tracking

When an asset is under maintenance (start date recorded, end date not yet set),
the system tracks the number of days it has been out of service. This is
displayed on the asset's detail page and in the Dashboard's Active Repairs
widget.

---

## Depreciation & Valuation

AssetBox uses the **straight-line depreciation** method to calculate the current
book value of hardware assets.

### Formula

```
Book Value = Purchase Cost − ((Purchase Cost − Salvage Value) ÷ Lifespan Months × Months Held)
```

The book value is capped at the **salvage value** — it will never drop below
that amount, even after the full lifespan has elapsed.

### Creating a Depreciation Schedule

1. Navigate to **Assets > Depreciations**.
2. Click **+** to add a new schedule.
3. Enter a **Name** (e.g. "Laptop — 36 Months").
4. Enter the **Lifespan in Months** (e.g. `36`).
5. Click **Save**.

### Applying a Schedule to an Asset Type

1. Navigate to **Assets > Asset Types**.
2. Open or edit an asset type.
3. Select the depreciation schedule in the **Depreciation** field.
4. Click **Save**.

All assets of that type will now show their current book value on their detail
page under the "Financial Details" card.

### Viewing Book Value

1. Open an asset's detail page.
2. Look for the **Financial Details** card.
3. The **Current Depreciated Book Value** shows the calculated value based on
   the purchase date, cost, salvage value, and lifespan.

---

## Custom Fields & Fieldsets

Custom fields let you track specialized information per asset type without
modifying the database schema — for example, vehicle VINs, mobile SIM details,
or desk dimensions.

### Creating Custom Fields

1. Navigate to **Extras > Custom Fields**.
2. Click **+** to add a new field.
3. Configure the field:

| Setting | Description |
|---|---|
| **Name** | Machine-readable slug (e.g. `vin_number`) |
| **Label** | Human-readable display name (e.g. "VIN Number") |
| **Field Type** | `Text`, `Number`, `Date`, `Boolean`, or `Select` |
| **Choices** | For Select fields — one choice per line (e.g. `Electric\nPetrol\nDiesel`) |
| **Required** | Whether this field must be filled in |

4. Click **Save**.

### Grouping Fields into Fieldsets

1. Navigate to **Extras > Custom Fieldsets**.
2. Click **+** to add a new fieldset.
3. Give it a **Name** (e.g. "Vehicle Specs").
4. Select the custom fields to include.
5. Click **Save**.

### Attaching a Fieldset to an Asset Type

1. Navigate to **Assets > Asset Types**.
2. Edit the asset type you want to attach the fieldset to.
3. Select the fieldset in the **Custom Fieldset** dropdown.
4. Click **Save**.

### Using Custom Fields

When you create or edit an asset of a type that has a custom fieldset attached,
the custom fields appear dynamically in the form below the standard fields.
Values are saved in the asset's `custom_values` and displayed on the detail
page under a "Specifications" card with human-readable labels.

---

## Global Search

The global search bar is located in the top navigation bar on every page.

### Searching

1. Type your search term into the search field.
2. Press **Enter** or click the search icon.

The search spans across all major models — assets, software, licenses,
accessories, consumables, contacts, and more. Results are grouped by model type
and displayed with a link to each matching record.

---

## Bulk Operations

### Bulk Delete

1. On any list page, check the boxes on the left of the rows you want to delete.
2. Click the **Delete Selected** button that appears in the batch actions bar.
3. Confirm the deletion.

> **Caution:** Bulk delete is irreversible. Verify your selection before
> confirming.

---

## Audit Trail (Changelog)

Every action in AssetBox — creating, editing, deleting, checking out, and
checking in — is automatically recorded with:

- The user who performed the action
- The timestamp of the action
- The type of action (create, update, delete, checkout, checkin)
- A snapshot of the data before and after the change

### Viewing the Changelog

Navigate to **Operations > Changelog** to see all recorded actions. You can
click on any entry to view the full before/after data snapshot.

### Viewing Changes for a Specific Record

When viewing the detail page of any record (asset, license, etc.), the
changelog section at the bottom of the page shows only the changes related to
that specific record.

---

## Asset Label Printing

AssetBox supports printing barcode labels for physical asset tracking.

### Printing a Label

1. Open the asset's detail page.
2. Click **Print Label** in the page header.
3. A printer-friendly label page opens in a new browser window.
4. Use your browser's print function (Ctrl+P / Cmd+P) to print.

---

## User Profile & Preferences

### Accessing Your Profile

Click your avatar in the top-right corner of the page. The dropdown menu
provides access to:

| Menu Item | Purpose |
|---|---|
| **Profile** | View and edit your display name and contact details |
| **Password** | Change your login password |
| **Preferences** | Set display preferences (color theme) |
| **API Tokens** | Manage personal API access tokens (for developers) |
| **Notifications** | View and manage system notifications |
| **Subscriptions** | Manage notification subscriptions |
| **Sign Out** | Log out of AssetBox |

### Changing Your Password

1. Click your avatar → **Password**.
2. Enter your current password.
3. Enter and confirm your new password.
4. Click **Save**.

---

## Troubleshooting

### I Can't Log In

- Verify you are using the correct URL.
- Check that Caps Lock is not enabled.
- If you have forgotten your password, contact your system administrator to
  reset it.

### A Form Won't Submit

- Check for red error messages near individual fields — these highlight missing
  or invalid values.
- Required fields are marked and must be filled in.
- For number fields, ensure you are entering a valid number (use a dot for
  decimals, e.g. `1999.99`).

### A Kit Checkout Failed

- This usually means one of the kit's items is out of stock. Read the error
  message carefully — it identifies which item was unavailable.
- Ensure there are available assets of the required type in a deployable status.
- Ensure there are enough accessory units in stock.
- Ensure there are enough available license seats.

### Custom Fields Aren't Showing

- Verify that the custom fieldset is attached to the correct **Asset Type**.
- Verify that the asset you are viewing is of that type.
- Edit the asset type and confirm the custom fieldset is selected.

### The Search Doesn't Find What I Expected

- Searches match against names, serial numbers, and descriptive fields.
- Try using partial terms (e.g. "Mac" instead of "MacBook Pro").
- The search is case-insensitive.

### An Asset's Book Value Shows Zero or Looks Wrong

- Verify that the asset has a **Purchase Date**, **Purchase Cost**, and
  **Salvage Value** set.
- Verify that the asset's type has a **Depreciation Schedule** attached.
- If the purchase date is in the future, the book value equals the purchase
  cost.
- If the lifespan has fully elapsed, the book value equals the salvage value.

### Page Doesn't Update After an Action

- AssetBox uses HTMX for smooth page navigation. If a page appears stale, use
  your browser's hard refresh (Ctrl+F5 / Cmd+Shift+R).

---

## Frequently Asked Questions

### What's the difference between an Asset and an Accessory?

**Assets** are serialized items tracked individually (e.g. "Laptop SN-12345").
**Accessories** are bulk items tracked by quantity (e.g. "USB-C Charger —
15 in stock"). You check out individual assets to holders; you check out
accessories by decrementing the stock count.

### What's the difference between an Accessory and a Consumable?

**Accessories** are durable items that return to inventory when checked in
(e.g. keyboards, chargers). **Consumables** are one-time-use items that are
consumed when checked out (e.g. toner cartridges, batteries).

### How is the book value calculated?

AssetBox uses the **straight-line depreciation** formula:

```
Book Value = Purchase Cost − ((Purchase Cost − Salvage Value) ÷ Lifespan Months × Months Held)
```

The value decreases evenly each month until it reaches the salvage value, after
which it stays constant.

### Are my product keys safe?

Yes. Software product keys are encrypted using AES-256 (Fernet) before being
stored in the database. They can only be decrypted and viewed through the
AssetBox application.

### Can I recover a deleted asset?

No. Deletion is permanent. The audit trail (changelog) retains a record of what
was deleted and by whom, but the data itself cannot be recovered through the
application. For critical deletions, contact your database administrator about
restoring from backups.

### How do I create a new status label?

1. Navigate to **Assets > Status Labels**.
2. Click **+** to add a new label.
3. Enter a **Name** (e.g. "In Repair").
4. Select a **Type** (Deployable, Pending, Undeployable, or Archived).
5. Choose a **Color** using the hex color picker (e.g. `#FF6600` for orange).
6. Click **Save**.

### What is the "Deployable" status type for?

Deployable statuses are those where the asset is ready to be assigned to a
holder. Examples: "In Stock", "Available", "Spare".

### What are Component Types and Component Instances?

**Component Types** define categories of internal hardware (e.g. "RAM Module",
"SSD Drive"). **Component Instances** are specific components installed in
specific assets. This lets you track which RAM modules or hard drives are
inside which computers.

### Does AssetBox support multi-tenancy?

Yes. The Organization section includes **Tenants** and **Tenant Groups** for
organizations that manage assets across multiple legal entities or departments.

### Where can I find the API documentation?

Click the API documentation icon (file with code brackets) in the footer of any
page. This opens the interactive Swagger API documentation where you can browse
and test all available REST endpoints.

---

*AssetBox User Guide — Version 0.1.0 — May 2026*
