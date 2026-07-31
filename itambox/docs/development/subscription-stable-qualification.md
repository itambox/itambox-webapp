# Subscription Stable qualification

This document records the bounded Subscription surface qualified for the 1.0 Stable contract. It freezes existing behavior; it does not add renewal history, automatic renewal, bulk clone, or another lifecycle state.

## Lifecycle contract

`Subscription.status` has exactly four persisted values: `active`, `suspended`, `cancelled`, and `expired`. Status is not editable through ordinary forms, bulk edit, REST create/update, GraphQL create/update, or imports. Transitions use the explicit suspend, resume, cancel, renew, and system-expire actions.

`Subscription.clean()` applies the same transition matrix to direct model writes while allowing harmless same-state and non-lifecycle updates. Updates lock the persisted subscription row before validation and save, so a stale UI, GraphQL, or direct-model writer cannot overwrite a concurrent terminal transition. A cancelled subscription cannot return to active. Retried actions that have already reached their target state are no-ops. `renewal_date` and `renewal_cost` describe the current term; `vendor_contract_auto_renews` is declarative and does not trigger renewal.

## Qualified surface matrix

| Surface | Lifecycle behavior | Tenant, permission, and concurrency evidence |
|---|---|---|
| UI | Dedicated suspend, resume, cancel, and renew action views | Tenant-scoped lookup, object permission checks, and action-only lifecycle writes |
| REST | Dedicated lifecycle actions; the compatibility status endpoint accepts only the identical current value | Required `If-Match`; PostgreSQL row lock followed by a second ETag check makes a lost race return 412 |
| GraphQL | Dedicated suspend and resume mutations; ordinary create/update rejects lifecycle input | Tenant-scoped node lookup and mutation permission checks |
| Model/service | Explicit transition methods plus `clean()` invariant | Legal, illegal, same-state, retry, audit, and cross-tenant assignment tests |
| CSV/YAML import and export | Import excludes lifecycle fields; the deprecated `auto_renewal` term maps to the canonical field; export retains both terms for 1.x | Registered import form controls the accepted field inventory |
| Search and filtering | Tenant-scoped search and closed status choices | Cross-tenant list/filter/search tests fail closed |
| Daily task | Expires overdue active subscriptions and sends 30/14/7-day reminders | Unscoped bootstrap only for enumeration; each row runs inside its tenant `TaskContext`; repeated same-date runs do not duplicate notifications or transitions |

## Assignment and seat-accounting contract

A `SubscriptionAssignment` target must exist and belong to the same tenant as its subscription. This invariant is enforced on the model in addition to UI, REST, and GraphQL validation, so ambient context cannot authorize a foreign GenericForeignKey target. If a previously valid target later leaves the tenant, UI and API representations fail closed instead of exposing that stale foreign target.

Seat totals are rolled up from active funded licenses. Assigned-seat counts include only active assignments whose asset or holder still exists and remains in the subscription tenant. Soft-deleting a target or moving a holder out of the tenant releases that seat from the count. Suspending a subscription does not rewrite its entitlement or assignment accounting.

## Task observability and idempotency

The daily job enumerates active, non-deleted subscriptions across tenants and enters a separate `TaskContext` for every subscription. It locks each candidate and rechecks its status and renewal date before expiring it or sending a reminder, so a concurrent renewal cannot receive stale effects. Notifications are limited to active staff members and owners who remain members of the subscription tenant. Automatic expiry is recorded as a tenant-attributed system change with no human actor. Notifications use their complete recipient, subject, message, level, and target identity as the idempotency key, so retrying the same daily run does not create duplicate effects. Invalid explicit tenant or principal identifiers fail rather than becoming an empty successful run.

## Deliberately absent surfaces

Subscriptions have no renewal-history ledger, automatic renewal execution, bulk clone workflow, or additional lifecycle state. The suite remains serial and PostgreSQL-backed; pytest-xdist is not supported.
