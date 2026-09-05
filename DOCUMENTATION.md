# ITAMbox Documentation Style Guide

The public documentation explains product behavior first. Source implementation belongs in contributor documentation unless it is part of a public integration or deployment contract.

## Audiences

- **Getting Started**: new operators planning and populating ITAMbox.
- **Features**: users performing product workflows.
- **Configuration**: deployment-controlled settings.
- **Customization**: administrator-defined objects and behavior inside ITAMbox.
- **Integrations**: public external contracts such as REST, GraphQL, SCIM, and webhooks.
- **Administration**: ongoing operation, permissions, Jobs, recovery, and management commands.
- **Data Model**: canonical object and field semantics.
- Contributor implementation detail stays outside the public MkDocs tree.

## Writing Rules

- Write `ITAMbox` exactly.
- Use American English and the current UI label when referring to a UI action.
- Prefer `Open **Find by Scan**` to a raw route instruction.
- Do not put `.py` or `.ts` filenames, private classes/functions, ORM mechanics, migration names, or internal call chains in Getting Started or feature pages.
- REST methods, GraphQL, HTTP status behavior, ETags, webhook signatures, environment variables, configuration keys, API payloads, authentication headers, and supported management commands are appropriate in the correct technical family.
- Avoid em dashes and en dashes in ordinary prose.
- Do not duplicate capability maturity labels. Link to the canonical Capability Maturity page and mention only operationally important limitations locally.
- Do not claim legal effect merely because ITAMbox stores evidence.
- Use examples that resemble real MSP or IT operations.

## Data Model Pages

Define the object in business terms, then describe fields under `## Fields` with one `### Field` heading per field. Explain relationships and validation when they affect how a human uses the object. Do not present Django field classes as business meaning.

## Links And Compatibility

Existing model-reference URLs are context-sensitive application behavior. Preserve them unless a migration includes verified compatibility handling. Run the strict MkDocs build and documentation-policy tests before merging documentation changes.
