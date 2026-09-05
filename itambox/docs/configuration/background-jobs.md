# Background Job Configuration

Jobs require the worker service used by the deployment. A healthy web process can still accept a request while workers are unavailable, leaving newly queued Jobs Pending.

Use the repository-supported deployment configuration for the worker/cluster rather than running ad-hoc processes with different settings. Keep the application and workers on compatible code and configuration during upgrades.

## Operational Requirements

- workers must be running whenever queued capabilities are expected to progress;
- workers need the same database and application configuration required by the task;
- scheduled capabilities additionally need the scheduler configuration described by the deployment;
- failed-task retention is controlled by the documented retention settings.

For the complete environment-variable reference, see [Installing ITAMbox](../operations/installation.md). For user-visible Job behavior, see [Background Jobs](../features/background-jobs.md). For diagnosis and recovery, see [Background Job Administration](../administration/background-jobs.md).
