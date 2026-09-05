# Software, Licenses, And Maintenance

ITAMbox tracks software products and license entitlement separately from physical Asset Maintenance. They meet on an asset record but represent different operational concerns.

## Software Catalog

Software records describe products and versions that may be installed or discovered. Keep the catalog normalized enough that discovery data and license records can refer to the same product consistently.

Discovery synchronization can supplement this catalog. See [Discovery Sync](../integration/discovery-sync.md).

## Licenses And Seats

A License represents an entitlement with quantity, dates, cost, and other contract information. Seat assignments connect entitlement to an Asset or holder where supported.

Use license utilization to compare owned entitlement with current allocation. Do not assume every discovered installation automatically proves entitlement or that every entitlement automatically maps to one installation.

## Asset Maintenance

Asset Maintenance records a service event for physical equipment. Use it for scheduled or completed maintenance, repairs, supplier work, cost, downtime, and supporting notes/evidence.

Maintenance states include Scheduled, In Progress, Completed, and Cancelled. A maintenance record is history for the asset; do not overwrite completed events to represent a later repair.

## Warranties

A Warranty records coverage for an Asset, including provider/terms/dates where available. Warranty coverage and maintenance history are complementary: a maintenance event can occur inside or outside warranty coverage.

See [Software](../models/software/software.md), [License](../models/licenses/license.md), [Asset Maintenance](../models/compliance/assetmaintenance.md), and [Warranty](../models/assets/warranty.md).
