# Asset Tag Sequences

**Asset Tag Sequences** govern auto-incrementing serial structures when creating new physical assets, removing the need for manual numbering.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Category** | The asset category this sequence applies to. Null represents default sequences. | Foreign Key | No |
| **Is Active** | The is active of the asset tag sequence. | Boolean | Yes |
| **Next Value** | The next positive integer value that will be generated. | Integer | Yes |
| **Prefix** | The prefix text preceding the auto-increment number (e.g. `ASSET-`). | String | Yes |
| **Tenant** | The tenant owning this sequence. Null represents system-wide/global sequences. | Foreign Key | No |
| **Zero Padding** | Width configuration to pad zero numbers (e.g. `6` pads `42` to `000042`). | Integer | Yes |

## Sequence Preview
The sequence displays a real-time preview (e.g. `ASSET-000042`) so administrators know what the next generated tag looks like prior to creation.
