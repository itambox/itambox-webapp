# Warranties

A **Warranty** represents a manufacturer or third-party warranty agreement covering one or more physical assets. It defines coverage dates, supplier references, and terms of service.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Asset** | The physical asset covered under this warranty. | Foreign Key | Yes |
| **Cost** | The cost of the warranty. | Decimal | No |
| **Currency** | ISO 4217 code. Leave blank to use the tenant default currency. | Choice | No |
| **End Date** | The date the warranty coverage expires. | Date | Yes |
| **Notes** | Optional comments or details on terms. | Text | No |
| **Provider** | e.g. "Dell ProSupport Plus" | String | No |
| **Reference** | Claim number, policy reference, or contract ID. | String | No |
| **Start Date** | The date the warranty coverage begins. | Date | Yes |
| **Terms** | The terms of the warranty. | Text | No |
| **Warranty Type** | The warranty type of the warranty. | Choice | Yes |

## Features & Validation

* **Coverage Checks**: Automatic warning flags when the warranty is expired or close to expiration.
* **Date Consistency**: Validates that `end_date` is on or after `start_date`.
