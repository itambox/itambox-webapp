# Procurement Stable qualification

This document records the bounded procurement surface qualified for the 1.0 Stable contract. It is an inventory of existing behavior, not a commitment to add missing features.

## Qualified models and surfaces

| Object | UI | REST | Model/service | Security and invariants |
|---|---|---|---|---|
| `Contract` | List, detail, create, edit, delete | CRUD | Tenant-scoped model | Cross-tenant list filtering; foreign detail and writes return 404 without mutation |
| `PurchaseOrder` | List, detail, create, edit, delete and lifecycle views | CRUD plus `approve`, `order`, `receive`, `cancel`, `reopen` | Transactional lifecycle services | Tenant isolation, custom action permissions, creator/approver separation, action-only lifecycle writes, auditable transitions |
| `PurchaseOrderLine` | Parent-detail listing, add, edit and delete | CRUD | Tenant-scoped model; receipt service owns `qty_received` | Cross-tenant reads/writes fail closed; `qty_received` is action-only |
| Receipt materialization | Two-step receive UI | `receive` action | Assets, component/accessory/consumable stock, license progress and fulfillment links | PostgreSQL row locking prevents over-receipt and duplicate materialization; purchase currency is preserved on created assets |

## Concurrency contract

The PostgreSQL test suite executes real two-connection races:

- concurrent approval attempts serialize on the purchase-order row; exactly one transition succeeds and the loser receives a validation error reflecting the committed state;
- concurrent receipt attempts serialize on purchase-order lines; exactly one materialization succeeds when one unit is outstanding, the loser sees zero outstanding, and the final quantity and asset count remain one.

These tests use `TransactionTestCase` and separate database connections. They must remain serial with the rest of the suite; pytest-xdist is not supported.

## Currency contract

A purchase order has one currency. Every line delegates its currency to that parent, so a purchase-order total cannot combine line currencies. Assets materialized from a receipt copy both `unit_price` and the purchase-order currency. Cross-order or cross-contract aggregation must continue to bucket values by explicit currency; mixed currencies must never be silently summed.

## Deliberately absent surfaces

Procurement currently has no:

- GraphQL schema or root registration;
- search index;
- django-q task module or discoverable background task;
- CSV/import form;
- bulk, clone, or requisition view;
- procurement signal module.

Qualification does not invent these surfaces. Adding one is a separate product and compatibility decision with its own tenant, permission, audit, schema, and concurrency review.
