import graphene
import importlib
import logging
from django.apps import apps
from django.conf import settings
import assets.schema
import software.schema
import licenses.schema
import inventory.schema
import subscriptions.schema

logger = logging.getLogger(__name__)

query_bases = [
    assets.schema.Query,
    software.schema.Query,
    licenses.schema.Query,
    inventory.schema.Query,
    subscriptions.schema.Query,
]

mutation_bases = [
    assets.schema.Mutation,
    software.schema.Mutation,
    licenses.schema.Mutation,
    inventory.schema.Mutation,
    subscriptions.schema.Mutation,
]

# Dynamically import plugin schemas
for plugin_name in getattr(settings, 'PLUGINS', []):
    try:
        plugin_config = apps.get_app_config(plugin_name)
        graphql_schema_path = getattr(plugin_config, 'graphql_schema', None)
        if graphql_schema_path:
            schema_module = importlib.import_module(graphql_schema_path)
            if hasattr(schema_module, 'Query'):
                query_bases.append(getattr(schema_module, 'Query'))
            if hasattr(schema_module, 'Mutation'):
                mutation_bases.append(getattr(schema_module, 'Mutation'))
    except (LookupError, ImportError) as e:
        logger.warning("Failed to load GraphQL schema for plugin %s: %s", plugin_name, e)

query_bases.append(graphene.ObjectType)
mutation_bases.append(graphene.ObjectType)

Query = type('Query', tuple(query_bases), {})
Mutation = type('Mutation', tuple(mutation_bases), {})

schema = graphene.Schema(query=Query, mutation=Mutation)
