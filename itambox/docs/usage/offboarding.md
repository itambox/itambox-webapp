# Offboarding readiness

When a person is leaving, ITAMbox can tell you whether they are **"clear"** — i.e. whether
there is anything left to collect, reassign, or sign — from one place.

The offboarding readiness view is a read-only roll-up on the **Asset Holder** record.
It composes the obligations ITAMbox already tracks so you do not have to merge five
screens by hand:

| Obligation class | What it shows |
|---|---|
| Asset assignments | Assets checked out to the person (incl. kits / loans with due dates) |
| Accessory checkouts | Accessories in their name |
| Component allocations | Components allocated to them |
| Consumable checkouts | Consumables checked out to them |
| License seats | License seats assigned to them |
| Unaccepted custody | Custody receipts not yet accepted |
| Open asset requests | Requests where they are the requester **or** the assigned user |
| Reservations | Reservations still inside their window |
| Subscriptions | Subscriptions whose assignment covers them |
| Memberships | Active memberships binding them to the tenant |
| Login state | Whether their account is still active |

## How to use it

Open the departing person's **Asset Holder** record, then the **Offboarding** tab:

```
/organization/asset-holders/<asset-holder-pk>/?tab=offboarding
```

The tab lists every outstanding obligation with a link to the underlying record and a
**Clear** state when there is nothing left to deal with. It is **read-only**: it lists and
links — it does not check anything in, revoke a grant, or deactivate the user. Those are
deliberate operator actions, taken from the links the report provides.

> Readiness is a truthful "is there anything left to deal with?" answer, **not** a claim
> that offboarding has already happened.

## Permissions

The tab inherits the Asset Holder detail view's permission gate: staff or a documented
per-tenant permission can see it, and the report is computed server-side for the concrete
holder, so a person only ever sees their own obligations. Unaccepted custody receipts
additionally require the custody receipt view permission, exactly like the custody tab
on the same page.

## Why there is no script anymore

The former `docs/integration/offboard_user.py` "offboarding script" was removed. It took a
**user ID** but deleted `/api/organization/asset-holders/<user_id>/` — Asset Holder primary
keys are not user IDs, so it targeted the wrong row or 404'd. It also checked in only
*asset* assignments and silently ignored accessories, components, consumables, license
seats, custody receipts, reservations, requests, and memberships, then printed
"Offboarding completed successfully" purely from HTTP-2xx responses. The readiness view
replaces it: the same obligations, on the correct record, with no false sense of completion.
