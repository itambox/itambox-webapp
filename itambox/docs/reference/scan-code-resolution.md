# Scan Code Resolution

Scanning normalizes common scanner output before resolving a target. Whitespace, supported ITAMbox prefixes, and quoted scanner payloads are handled before lookup.

For asset resolution, ITAMbox can use:

1. ITAMbox-generated asset identifiers/URLs where supported;
2. Asset Tag;
3. Serial Number;
4. product code such as EAN/GTIN/UPC through the associated Asset Type.

A product code is not guaranteed to identify one physical item. If several assets are valid matches, the scan is treated as ambiguous instead of choosing one record arbitrarily.

**Find by Scan** resolves against records the user is permitted to access across tenants. Audit and bulk-action scan resolution stays bound to the tenant of the workflow.

For ordinary use, see [Scanning Assets](../usage/scanning.md).
