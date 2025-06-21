# AssetBox Technical Documentation Hub

Welcome to the **AssetBox** documentation directory. AssetBox is an enterprise-grade, lightweight IT Asset Management (ITAM) and tracking platform built with Django, Tabler CSS, and HTMX.

This documentation maps out the system's features, data models, cryptographic security protocols, dynamic rendering mechanics, and test suite configuration.

---

## 📚 Documentation Index

### 1. [Feature Catalog](file:///c:/Users/rene.rettig/HelheimCloud/Projekte/Coding/assetbox-webapp/docs/features.md)
A comprehensive catalog of AssetBox's core and extended ITAM features:
* **Procurement & Lifecycle Management**: Tracking acquisition costs, suppliers, and purchase details.
* **Dynamic Status Labels**: Back-end customizable asset states (Deployable, Pending, Undeployable, Archived).
* **Asset Maintenance (TCO)**: Operations tracking, downtime calculations, and Total Cost of Ownership (TCO) aggregation.
* **Cryptographic Security**: Symmetrically encrypted software product keys at rest.
* **Dynamic Custom Fieldsets**: JSON-based dynamic specifications (e.g. mobile SIM details, vehicle VINs) on an `AssetType` without schema changes.
* **Pre-defined Onboarding Kits**: Single-click atomic kitting allocations for onboarding workflows.
* **Straight-Line Depreciation & Valuation**: Real-time asset book valuation based on historical purchase data and lifespan calendars.

### 2. [System Architecture & Data Models](file:///c:/Users/rene.rettig/HelheimCloud/Projekte/Coding/assetbox-webapp/docs/architecture.md)
Under-the-hood developer blueprint detailing:
* **The Unified Page Navigation (HTMX)**: The single-page container swapping design which synchronizes breadcrumbs, actions, titles, and body content without full-page reloads.
* **Fernet Symmetric Encryption Flow**: Mathematical key derivation from the Django `SECRET_KEY` and transparent model `@property` decryptions.
* **Database Models and ER Schemas**: Visual maps of relationships between `Asset`, `AssetType`, `StatusLabel`, `CustomFieldset`, `Depreciation`, and `Kit`.

### 3. [Testing & Verification Guide](file:///c:/Users/rene.rettig/HelheimCloud/Projekte/Coding/assetbox-webapp/docs/testing_and_verification.md)
Instructions on running the automated test suite, coverage highlights, and step-by-step scripts for manual verification of complex workflows.

---

## 🚀 Quick Setup & Execution

### 1. Prerequisites
Ensure you have Python 3.11+ installed and the virtual environment configured.

### 2. Run Database Migrations
Apply all schema changes and seed the database with standard defaults:
```bash
# In the assetbox directory
..\.venv\Scripts\python manage.py migrate
```

### 3. Start the Development Server
```bash
# Start the local development server
..\.venv\Scripts\python manage.py runserver
```

### 4. Run Automated Test Suite
Ensure all 37 unit and integration tests compile perfectly:
```bash
# Run all tests
..\.venv\Scripts\python manage.py test
```
