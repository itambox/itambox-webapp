# Asset Maintenances

An **Asset Maintenance** logs repair tickets, hardware upgrades, support calls, calibration schedules, or vendor maintenance services performed on a physical asset.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Asset** | The physical serialized system receiving the maintenance. | Foreign Key | Yes |
| **Completion Date** | Date the maintenance was completed. | Date | No |
| **Cost** | Direct monetary cost of the service. | Decimal | No |
| **Currency** | ISO 4217 code. Leave blank to use the tenant default currency. | Choice | No |
| **Description** | The description of the asset maintenance. | Text | No |
| **Maintenance Type** | Choice of: `Upgrade`, `Repair`, `Calibration`, `Software Support`, `Hardware Support`. | Choice | Yes |
| **Notes** | Detailed log notes. | Text | No |
| **Performed By** | Name of the specific engineer or entity doing the work. | String | No |
| **Start Date** | Date the maintenance work began. | Date | Yes |
| **Status** | State of work: `Scheduled`, `In Progress`, `Completed`, `Cancelled`. | Choice | Yes |
| **Supplier** | The external vendor performing the maintenance service. | Foreign Key | No |

## Downtime Calculation
ITAMbox automatically calculates and displays the total downtime duration in days as the difference between the `Completion Date` and the `Start Date`.
