# Asset Maintenances

An **Asset Maintenance** logs repair tickets, hardware upgrades, support calls, calibration schedules, or vendor maintenance services performed on a physical asset.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Asset** | The physical serialized system receiving the maintenance. | Foreign Key | Yes |
| **Title** | A short, descriptive title of the maintenance task (e.g. `Laptop Battery Swap`). | String | Yes |
| **Maintenance Type** | Choice of: `Upgrade`, `Repair`, `Calibration`, `Software Support`, `Hardware Support`. | Choice | Yes |
| **Status** | State of work: `Scheduled`, `In Progress`, `Completed`, `Cancelled`. | Choice | Yes |
| **Supplier** | The external vendor performing the maintenance service. | Foreign Key | No |
| **Performed By** | Name of the specific engineer or entity doing the work. | String | No |
| **Cost** | Direct monetary cost of the service. | Decimal | No |
| **Start Date** | Date the maintenance work began. | Date | Yes |
| **Completion Date** | Date the maintenance was completed. | Date | No |
| **Notes** | Detailed log notes. | Text | No |

## Downtime Calculation
ITAMbox automatically calculates and displays the total downtime duration in days as the difference between the `Completion Date` and the `Start Date`.
