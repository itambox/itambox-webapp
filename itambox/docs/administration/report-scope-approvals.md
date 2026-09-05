# Report Scope Approvals

Scheduled reports can outlive the browser session in which they were created. ITAMbox therefore treats cross-tenant scheduled scope as a durable authorization decision rather than assuming the creator's old workspace remains sufficient forever.

## When Approval Is Required

A single-tenant schedule does not need cross-tenant scope approval. A schedule that aggregates tenant-group or broader cross-tenant data can require an approval by a user with the cross-tenant report permission.

Without the required approval, delivery fails closed with an authorization error instead of silently expanding scope.

## Approval And Revocation

Review the schedule, intended scope, and delivery destination before approving it. Revoke approval when the report no longer needs broad scope, ownership changes, or recipients change.

Authorization is still checked when the scheduled work runs. Approval is not a permanent bypass for later tenant-access changes.

Generated output can be stored as a Job attachment and remains protected by attachment authorization. See [Background Jobs](../features/background-jobs.md).
