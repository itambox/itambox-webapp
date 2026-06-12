# Depreciation — how it works

ITAMbox calculates a straight-line estimated book value for each asset. The purpose is
**indicative IT asset management** — refresh planning, MSP buyout pricing, insurance
schedules — not accounting.

> **Disclaimer:** ITAMbox book values are indicative figures for IT asset management,
> budgeting and insurance. They are **not** a substitute for your accounting system
> (ERP/DATEV), which remains the system of record for tax and balance-sheet
> depreciation.

---

## Resolution chain

The depreciation policy applied to an asset is resolved in this order:

| Rung | Source | Shown as |
|------|--------|----------|
| 1 | Asset-level override (`Asset.depreciation_override`) | "asset override" badge |
| 2 | Tenant default (`Tenant.default_depreciation`) | "tenant default" badge |
| 3 | Asset-type schedule (`AssetType.depreciation`) | "asset type" badge |
| 4 | None → value equals purchase cost | — |

The resolved policy and its source are shown on the asset financial panel so there is
no magic — you can always see which rule is applied.

---

## Methods

| Method | Behaviour |
|--------|-----------|
| `straight_line` | Even monthly charge over the lifespan |
| `none` | No depreciation — value always equals purchase cost |

---

## Conventions (month-counting)

The **convention** controls whether the acquisition month counts as a full depreciation
month.

### `exclude_purchase_month` (legacy default)

```
months_held = (on_date.year − purchase.year) × 12 + on_date.month − purchase.month
```

**Example:** Purchased 15 January. On 31 January `months_held = 0` — no charge yet.
On 1 February `months_held = 1` — first monthly charge.

### `include_purchase_month` (German pro rata temporis — default for new policies)

```
months_held = max(month_diff + 1, 0)
```

**Example:** Purchased 15 January. On 31 January `months_held = 1` — the acquisition
month counts as a full month.

Existing policies created before this feature was added keep `exclude_purchase_month`
via a data migration so installed behaviour is not silently changed.

---

## GWG — immediate expense threshold

Set `immediate_expense_threshold` to expense low-value assets in the acquisition month
instead of spreading them over the lifespan. Example: 800 € for German GWG
(§ 6 Abs. 2 EStG).

When `purchase_cost ≤ threshold` and `months_held ≥ 1` the value drops straight to
the salvage value (or 0 when no salvage is set).

**Seed policy:** "Sofortabschreibung GWG (≤ 800 €)" ships pre-configured.

---

## Disposal sign-off (Restbuchwert)

When an asset transitions to an **archived** status the book value is **frozen**:

- `Asset.disposed_at` is set to the transition timestamp.
- `Asset.disposal_value` is set to the computed book value at that moment.
- Future calls to `compute_book_value()` return `disposal_value` unchanged.

If the asset is un-archived (archived → pending), both fields are cleared and live
depreciation resumes.

The financial panel shows **"Sign-off value (frozen at YYYY-MM-DD)"** for disposed
assets.

---

## In-service date

Set `Asset.in_service_date` to start the depreciation clock from a date other than the
purchase date. Useful when equipment was purchased but sat in a warehouse before being
deployed.

When `in_service_date` is blank the clock starts from `purchase_date`.

---

## Display currency

Each tenant has a `currency` field (ISO 4217, default `EUR`) that controls how monetary
values are formatted in templates via the `{% load money %}` / `{{ value|money:object }}`
filter. This is **display only** — no exchange rates, no per-asset currency.

The system-wide fallback is `ITAMBOX_DEFAULT_CURRENCY` (env var, default `EUR`).

Symbol placement follows the convention of the currency:

| Currency | Example |
|----------|---------|
| EUR | 1.234,56 € |
| CHF | 1.234,56 CHF |
| USD | $1,234.56 |
| GBP | £1,234.56 |

---

## Seed policies

Three example policies ship out of the box:

| Name | Months | Convention | Notes |
|------|--------|------------|-------|
| IT-Hardware 36 Monate (AfA) | 36 | include_purchase_month | AfA-Tabelle 2021 — PCs, notebooks |
| Server 60 Monate (AfA) | 60 | include_purchase_month | AfA-Tabelle 2021 — servers |
| Sofortabschreibung GWG (≤ 800 €) | 1 | include_purchase_month | GWG §6 Abs. 2 EStG, threshold 800 € |

---

## Math reference

```
depreciable_base  = purchase_cost − salvage_value
monthly_charge    = depreciable_base / months
value_at_month_n  = max(purchase_cost − monthly_charge × months_held, salvage_value)
```

Result is quantised to 2 decimal places (ROUND_HALF_UP).
