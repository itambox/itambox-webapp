# Background Jobs

ITAMbox uses Jobs for operations that should continue outside the original web request. This keeps large imports, bulk actions, report generation, label generation, and selected synchronization tasks from depending on one browser request remaining open.

## What A Job Records

A Job can show:

- **Pending**, **Running**, **Completed**, or **Failed** status;
- when it was created, started, and completed;
- progress or result information supplied by the operation;
- logs or failure details;
- generated attachments when the operation produces a file.

A completed Job does not necessarily mean every selected row succeeded. Bulk operations can record row-level failures in their result. Review the result summary before assuming the entire batch changed.

## Finding Jobs

Open **Jobs** to return to work that is still running or to inspect a previous result. Jobs are scope-aware. System-level Jobs without a tenant are restricted to superusers.

## Generated Attachments

Some Jobs produce files, for example label PDFs or report output. The file appears with the Job after generation succeeds.

Attachment access is checked when the file is downloaded. A user who no longer has the required tenant access or Job permission cannot rely on an old generated link to bypass current authorization.

## When A Job Fails

Read the result and logs first. User-correctable failures often identify a specific row, invalid state, missing permission, or missing prerequisite. If Jobs remain Pending or many unrelated Jobs stop progressing, the worker service may need administrator attention.

Operators should use [Background Job Administration](../administration/background-jobs.md). Deployment owners should use [Background Job Configuration](../configuration/background-jobs.md).
