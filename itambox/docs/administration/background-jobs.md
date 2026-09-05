# Background Job Administration

Use **Jobs** to determine whether background work is progressing before investigating individual feature code or retrying the same operation repeatedly.

## Diagnose By Status

**Pending** means the Job has not started. A growing queue of unrelated Pending Jobs suggests a worker/queue problem rather than bad input in one workflow.

**Running** means a worker accepted the Job. A Job that remains Running unusually long should be investigated using its timestamps/logs and worker health.

**Completed** means the task reached its completion path. Review row-level results for bulk operations.

**Failed** records a terminal failure or a pending Job that was cancelled before a worker started it.

## Generated Files

If a report or label Job completed but no expected attachment is available, inspect the Job result/log first. Do not bypass attachment authorization by copying files directly from server storage for ordinary users.

## Recovery

Before re-running a failed operation:

1. identify whether the failure is input-specific or infrastructure-wide;
2. confirm the worker service is healthy;
3. correct the underlying cause;
4. retry through the supported product workflow where possible;
5. verify the new Job rather than assuming the retry succeeded.

Use [Background Job Configuration](../configuration/background-jobs.md) for deployment setup and [Management Commands](../operations/management-commands.md) for supported administrative commands.
