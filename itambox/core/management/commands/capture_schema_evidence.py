import json
import re

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


_INDEX_DEFINITION_RE = re.compile(
    r'^(CREATE (?:UNIQUE )?INDEX) (?:(?:"(?:[^"]|"")*")|\S+) (ON .+)$',
)
_SQL_STRING_LITERAL = r"'(?:''|[^'])*'"
_SQL_STRING_LITERAL_RE = re.compile(_SQL_STRING_LITERAL)
_VARCHAR_LITERAL = (
    rf'\(?{_SQL_STRING_LITERAL}::character varying\)?(?:::text)?'
)
_VARCHAR_LITERAL_ARRAY = (
    rf'ARRAY\[{_VARCHAR_LITERAL}(?:,\s*{_VARCHAR_LITERAL})*\]'
)
_VARCHAR_LITERAL_ARRAY_RE = re.compile(
    rf'\({_VARCHAR_LITERAL_ARRAY}\)::text\[\]'
    rf'|{_VARCHAR_LITERAL_ARRAY}(?:::text\[\])?',
)

_CATALOG_QUERIES = (
    (
        'columns',
        """
            SELECT c.table_name, c.column_name, c.udt_name, c.is_nullable,
                   c.column_default, c.collation_name, c.is_identity,
                   c.identity_generation, c.is_generated,
                   c.generation_expression,
                   pg_catalog.format_type(a.atttypid, a.atttypmod),
                   a.attndims
            FROM information_schema.columns c
            JOIN pg_namespace n ON n.nspname = c.table_schema
            JOIN pg_class t
              ON t.relnamespace = n.oid AND t.relname = c.table_name
            JOIN pg_attribute a
              ON a.attrelid = t.oid AND a.attname = c.column_name
             AND a.attnum > 0 AND NOT a.attisdropped
            WHERE c.table_schema = 'public'
            ORDER BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12
        """,
    ),
    (
        'constraints',
        """
            SELECT c.conrelid::regclass::text, c.conname,
                   pg_get_constraintdef(c.oid, true), c.convalidated
            FROM pg_constraint c
            JOIN pg_namespace n ON n.oid = c.connamespace
            WHERE n.nspname = 'public'
            ORDER BY 1, 2, 3, 4
        """,
    ),
    (
        'indexes',
        """
            SELECT tbl.relname, idx.relname, pg_get_indexdef(i.indexrelid),
                   i.indisvalid, i.indisready, i.indislive
            FROM pg_index i
            JOIN pg_class idx ON idx.oid = i.indexrelid
            JOIN pg_class tbl ON tbl.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = tbl.relnamespace
            WHERE n.nspname = 'public'
            ORDER BY 1, 2, 3, 4, 5, 6
        """,
    ),
    (
        'extensions',
        """
            SELECT extname, extversion
            FROM pg_extension
            ORDER BY 1, 2
        """,
    ),
    (
        'content_types',
        """
            SELECT app_label, model
            FROM django_content_type
            ORDER BY 1, 2
        """,
    ),
    (
        'permissions',
        """
            SELECT c.app_label, c.model, p.codename
            FROM auth_permission p
            JOIN django_content_type c ON c.id = p.content_type_id
            ORDER BY 1, 2, 3
        """,
    ),
)


def canonicalize_index_definition(definition):
    """Remove the physical index name while preserving its semantics."""
    match = _INDEX_DEFINITION_RE.fullmatch(definition)
    if not match:
        raise ValueError(f'Unsupported PostgreSQL index definition: {definition!r}')
    return f'{match.group(1)} {match.group(2)}'


def canonicalize_catalog_expression(expression):
    """Normalize equivalent PostgreSQL casts for string-literal arrays."""
    def canonicalize_array(match):
        literals = _SQL_STRING_LITERAL_RE.findall(match.group(0))
        return f"ARRAY[{', '.join(f'{item}::text' for item in literals)}]"

    return _VARCHAR_LITERAL_ARRAY_RE.sub(canonicalize_array, expression)


class Command(BaseCommand):
    help = (
        'Emit a stable, read-only PostgreSQL schema inventory for recovery '
        'and upgrade parity checks.'
    )

    def handle(self, *args, **options):
        if connection.vendor != 'postgresql':
            raise CommandError('capture_schema_evidence requires PostgreSQL')

        rows = {}
        with connection.cursor() as cursor:
            for name, sql in _CATALOG_QUERIES:
                cursor.execute(sql)
                rows[name] = cursor.fetchall()

        columns = sorted([list(row) for row in rows['columns']])
        constraints = sorted([
            [
                table_name,
                canonicalize_catalog_expression(definition),
                validated,
            ]
            for table_name, _physical_name, definition, validated
            in rows['constraints']
        ])
        indexes = sorted([
            [
                table_name,
                canonicalize_catalog_expression(
                    canonicalize_index_definition(definition),
                ),
                valid,
                ready,
                live,
            ]
            for (
                table_name, _physical_name, definition,
                valid, ready, live,
            ) in rows['indexes']
        ])
        extensions = sorted([list(row) for row in rows['extensions']])
        content_types = sorted([list(row) for row in rows['content_types']])
        permissions = sorted([list(row) for row in rows['permissions']])

        evidence = {
            'schema_version': 1,
            'postgresql_version_num': connection.pg_version,
            'columns': columns,
            'content_types': content_types,
            'constraints': constraints,
            'indexes': indexes,
            'extensions': extensions,
            'permissions': permissions,
            'summary': {
                'columns': len(columns),
                'content_types': len(content_types),
                'constraints': len(constraints),
                'extensions': len(extensions),
                'indexes': len(indexes),
                'permissions': len(permissions),
            },
        }
        self.stdout.write(json.dumps(
            evidence,
            sort_keys=True,
            separators=(',', ':'),
        ))
