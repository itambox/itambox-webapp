# Populating Data

ITAMbox supports several ways to build an inventory. Choose the simplest method that preserves ownership, identifiers, and relationships accurately.

## Web Interface

The web interface is best for initial configuration, small batches, and records that require judgment. Create the supporting objects first so assets can be assigned the correct tenant, type, location, holder, and financial data when they are entered.

## Bulk Import

Bulk import is intended for structured datasets such as exports from an existing ITAM system or spreadsheets maintained during a migration. Start with a small representative file, confirm the mapping, and then import the larger set.

See [Bulk CSV Import](../integration/bulk_import_guide.md). If you are moving from Snipe-IT, see [Migrate from Snipe-IT](../integration/migrate-from-snipe-it.md).

## APIs

REST and GraphQL are appropriate when another system should maintain ITAMbox continuously. API access is subject to the token, permission, and tenant scope of the caller. A broad workspace in the browser does not imply unrestricted API mutation rights.

See [REST and GraphQL](../integration/developer_guide.md) and [API Tokens and SCIM](../usage/api-tokens-and-scim.md).

## Discovery

Discovery integrations can create or update records from external inventory sources. Treat discovered data as one input to your operating model. Decide which system is authoritative for ownership, lifecycle state, procurement data, and human assignments before enabling recurring synchronization.

See [Discovery Sync](../integration/discovery-sync.md).

## Validate After Import

After the first substantial load, sample records across several tenants and check:

- tenant ownership;
- asset tags and serial numbers;
- Asset Type and Manufacturer relationships;
- Sites and Locations;
- current assignments;
- status labels;
- purchase and warranty data;
- duplicate or ambiguous identifiers.

Use [Search, Filters, and Tables](../features/search-tables.md) to review subsets and export them for reconciliation.
