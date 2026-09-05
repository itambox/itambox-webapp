# Users

A **User** represents an account that can sign in to ITAMbox. It stores identity details, account state, security settings, and permission relationships.

## Fields

### Date Joined

The date and time when the account was registered.

**Required:** Yes.

### Email Address

Email contact address for notifications.

### First Name

User's first name.

### Groups

Security groups the user belongs to.

### Active

Flag specifying if the account is active. Accounts should be deactivated instead of deleted to preserve audit trails.

**Required:** Yes.

### Staff Status

Designates whether the user can access administrative portals.

**Required:** Yes.

### Superuser Status

Grants all system permissions without explicit assignment.

**Required:** Yes.

### Last Login

Most recent successful sign-in time for the user.

### Last Name

User's last name.

### Password

Hashed credentials for user login.

**Required:** Yes.

### User Permissions

Direct, granular security permissions assigned to the user.

### Username

Unique alphanumeric login username.

**Required:** Yes.


## Features & Validation

* **Self-Lockout Prevention**: System guards prevent users from deactivating themselves or revoking their own staff/superuser status in bulk edits.
* **Audit Trails**: All modifications, creations, and deletions are captured in the system changelog for security compliance.
