# Tenant Invitations

A **Tenant Invitation** manages the onboarding of new users into a specific Tenant scope. Tenant administrators send invitations via email, which generates a unique security token. When the recipient accepts, the system automatically maps them to a local Tenant Membership and hooks them to any existing Asset Holder profiles matching their email address.

---

## Attributes & Fields

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Accepted At** | Timestamp when the user accepted the invitation. | DateTime | No |
| **Email** | The email address of the user being invited. | Email | Yes |
| **Expires At** | Timestamp when the invitation token expires. | DateTime | Yes |
| **Invited By** | The User who created and sent the invitation. | Foreign Key | No |
| **Role** | The Tenant Role they will be assigned. | Foreign Key | Yes |
| **Tenant** | The Tenant they will be added to upon acceptance. | Foreign Key | Yes |
| **Token** | A unique security UUID token (automatically generated). | UUID | Yes |

## Acceptance Workflow

When a user accepts a valid, unexpired invitation:
1. **Tenant Membership Creation**: A new `TenantMembership` record is created linking the User, Tenant, and pre-configured Tenant Role.
2. **Invitation Marking**: The invitation's `Accepted At` field is updated with the current timestamp.
3. **Asset Holder Sync**: The system searches for an existing `AssetHolder` record matching the invited email within that Tenant. If an unlinked profile is found, it automatically links it to the newly registered User, preserving physical asset custody history.
