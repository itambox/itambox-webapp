# Bulk CSV Import Guide

This guide explains how to import assets in bulk using CSV files.

## Columns Supported
The import CSV file should contain the following headers:
- `name` (required): The name of the asset.
- `asset_tag` (required): A unique asset tag.
- `serial_number` (optional): Serial number of the asset.
- `description` (optional): Short description.
- `purchase_date` (optional): Purchase date in YYYY-MM-DD format.
- `purchase_cost` (optional): Price paid.
- `order_number` (optional): Associated purchase order.
- `notes` (optional): Extra information.

## Example Row
`MacBook Pro 16,ASSET-100201,C02F2345Q05D,Developer laptop,2026-01-15,2499.00,PO-90812,Standard dev setup`
