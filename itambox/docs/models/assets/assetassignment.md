# Asset Assignments

An **Asset Assignment** tracks the active checkout custody, historical duration, and return details for physical assets allocated to users, locations, or parent systems.

## Fields

### Asset

The physical serialized asset being assigned.

**Required:** Yes.

### Assigned Asset

Asset associated with this assignment.

### Assigned Location

Location associated with this assignment.

### Assigned User

Asset Holder associated with this assignment.

### Checked In At

Timestamp of return completion.

### Checked In By

The administrator who processed the return.

### Checked Out At

Timestamp of transaction activation.

**Required:** Yes.

### Checked Out By

The administrative user who authorized the checkout transaction.

### Due Date

Mandatory return date for loaner assets.

### Expected Checkin Date

Optional expected return deadline for temporary checkouts.

### Is Active

True if custody is current. Closed return actions switch this to False.

**Required:** Yes.

### Is Loan

Mark this assignment as a temporary loan with a mandatory return date.

**Required:** Yes.

### Notes

Transaction remarks.

### Pre Checkout Status

Preserved status label to revert to upon checkin.

### Returned At

Date the loaned asset was physically returned.


## Unique Active Assignment Constraint
An asset can have at most one active assignment at a time. ITAMbox rejects a second simultaneous active assignment.
