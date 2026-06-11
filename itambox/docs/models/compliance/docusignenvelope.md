# DocuSign Envelopes

A **DocuSign Envelope** represents an out-of-band electronic signature request managed via the DocuSign integration plugin (`itambox_esign`). When checking out high-value or restricted assets, the system can dynamically trigger a DocuSign envelope containing the custody agreement/EULA, tracking its status directly within ITAMbox.

---

## E-Signature Statuses
The envelope transitions through the following statuses synced from DocuSign webhooks:
- **Sent**: The signature request email has been dispatched to the recipient.
- **Delivered**: The recipient has opened/viewed the document in DocuSign.
- **Completed**: The recipient signed the custody document. The signed PDF is pulled back and saved.
- **Declined**: The recipient refused to sign the terms.

---

## Attributes & Fields

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Asset** | The physical hardware Checked Out. | Foreign Key | Yes |
| **Envelope ID** | The unique UUID identifier representing the document pack in DocuSign. | String | Yes |
| **Status** | The active status (`sent`, `delivered`, `completed`, `declined`). | String | Yes |
| **Recipient Name** | The full name of the recipient signing for the asset. | String | Yes |
| **Recipient Email** | The email address of the recipient. | Email | Yes |
| **Sent At** | Timestamp when the DocuSign envelope was created and dispatched. | DateTime | Yes (Auto) |
| **Completed At** | Timestamp when the document was signed and finalized. | DateTime | No |
| **Signed Document** | Reference to the saved signed document PDF attachment in ITAMbox. | Foreign Key | No |
