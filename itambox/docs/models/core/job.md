# Jobs

A **Job** represents a background task execution, such as imports, bulk actions, report generation, or synchronization work. It tracks execution status, input arguments, logs, and results.

## Fields

### Completed

Timestamp when the job execution finished.

### Created

Timestamp when the job record was generated.

### Data

Input data or arguments provided to the job task.

### Logs

Standard output or error logs captured during execution.

### Model

The ITAMbox object associated with the Job, when the background operation belongs to a specific record.

### Name

Name of the job task being executed.

**Required:** Yes.

### Object ID

Database primary key of the associated object.

### Result

Any returned results or execution summaries.

### Scheduled For

Future scheduled execution timestamp, if applicable.

### Started

Timestamp when the job execution began.

### Status

The current Job state: Pending, Running, Completed, or Failed.

**Required:** Yes.

### Tenant

Tenant context under which the job executes.


## Features & Validation

* **Asynchronous Execution**: Tracks tasks executed outside the main web request thread (for example imports or generated-output work).
* **Audit Logs**: The logs field acts as a persistent record of background execution runs for diagnostics and verification.
* **Polymorphic Reference**: Can optionally reference the object associated with the work.
