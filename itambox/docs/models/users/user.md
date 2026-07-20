# Users

A **User** represents a user account within the Django authentication system. It manages authentication credentials, user details, security status, and group/permission assignments.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Date Joined** | The date and time when the account was registered. | Date Time | Yes |
| **Email Address** | Email contact address for notifications. | String | No |
| **First Name** | User's first name. | String | No |
| **Groups** | Security groups the user belongs to. | Many-to-Many | No |
| **Active** | Flag specifying if the account is active. Accounts should be deactivated instead of deleted to preserve audit trails. | Boolean | Yes |
| **Staff Status** | Designates whether the user can access administrative portals. | Boolean | Yes |
| **Superuser Status** | Grants all system permissions without explicit assignment. | Boolean | Yes |
| **Last Login** | The last login of the user. | Date Time | No |
| **Last Name** | User's last name. | String | No |
| **Password** | Hashed credentials for user login. | String | Yes |
| **User Permissions** | Direct, granular security permissions assigned to the user. | Many-to-Many | No |
| **Username** | Unique alphanumeric login username. | String | Yes |

## Features & Validation

* **Self-Lockout Prevention**: System guards prevent users from deactivating themselves or revoking their own staff/superuser status in bulk edits.
* **Audit Trails**: All modifications, creations, and deletions are captured in the system changelog for security compliance.
