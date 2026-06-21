# Jobs

A **Job** represents a background task execution, such as data synchronization, report generation, or scheduled actions. It tracks execution status, input arguments, logs, and results.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Completed** | Timestamp when the job execution finished. | Date Time | No |
| **Created** | Timestamp when the job record was generated. | Date Time | No |
| **Data** | Input data or arguments provided to the job task. | JSON | No |
| **Logs** | Standard output or error logs captured during execution. | Text | No |
| **Model** | ContentType reference if the job is associated with a specific database object. | Foreign Key | No |
| **Name** | Name of the job task being executed. | String | Yes |
| **Object ID** | Database primary key of the associated object. | Integer | No |
| **Result** | Any returned results or execution summaries. | JSON | No |
| **Scheduled For** | Future scheduled execution timestamp, if applicable. | Date Time | No |
| **Started** | Timestamp when the job execution began. | Date Time | No |
| **Status** | The execution status of the job (e.g., Pending, Running, Completed, Failed, Scheduled). | Choice | Yes |
| **Tenant** | Tenant context under which the job executes. | Foreign Key | No |

## Features & Validation

* **Asynchronous Execution**: Tracks tasks executed outside the main web request thread (e.g., Celery tasks).
* **Audit Logs**: The logs field acts as a persistent record of background execution runs for diagnostics and verification.
* **Polymorphic Reference**: Uses Django generic foreign keys to link jobs to any model instances in the system.
