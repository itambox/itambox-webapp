# Procurement And Contracts

Purchase Orders record the acquisition workflow. Contracts record longer-lived commercial or service agreements. They can relate to the same supplier without being the same object.

## Purchase Orders

Purchase Orders support Draft, Approved, Ordered, Partially Received, Received, and Cancelled states.

A creator cannot approve their own Purchase Order. This separation protects the approval workflow from becoming a self-approval checkbox.

Receiving behavior depends on the line type. Asset lines can create Assets. Component, Accessory, and Consumable lines can add stock. License lines follow license-specific behavior rather than behaving like ordinary warehouse stock.

Partial receipt is a normal state. Record what actually arrived instead of marking an incomplete order fully received.

## Request Fulfillment

Approved Asset Requests can be linked to procurement when inventory is not available. Fulfillment links connect the purchasing activity to the original need and can participate in advancing the request workflow.

See [Asset Requests and Reservations](asset-requests-and-reservations.md).

## Contracts

Use Contracts for supplier agreements, support/renewal dates, costs, and related assets or services where supported. A contract record tracks the agreement; it does not itself make the external vendor renew, cancel, invoice, or perform a service.

## Operational Practice

Keep approvers separate from requesters where practical, record partial receipts accurately, and review renewal dates before they become emergency work. See [Lifecycle Data Quality](../best-practices/lifecycle-data-quality.md).

Field reference: [Purchase Order](../models/procurement/purchaseorder.md), [Purchase Order Line](../models/procurement/purchaseorderline.md), and [Contract](../models/procurement/contract.md).
