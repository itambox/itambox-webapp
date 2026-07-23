import io
import json
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import SimpleTestCase

from core.management.commands.capture_schema_evidence import (
    canonicalize_catalog_expression,
    canonicalize_index_definition,
)


class SchemaEvidenceNormalizationTests(SimpleTestCase):
    def test_index_name_is_removed_without_losing_definition(self):
        definition = (
            'CREATE UNIQUE INDEX legacy_random_name '
            'ON public.assets_asset USING btree (asset_tag) '
            'WHERE (deleted_at IS NULL)'
        )

        canonical = canonicalize_index_definition(definition)

        self.assertEqual(
            canonical,
            'CREATE UNIQUE INDEX ON public.assets_asset '
            'USING btree (asset_tag) WHERE (deleted_at IS NULL)',
        )
        self.assertNotIn('legacy_random_name', canonical)

    def test_quoted_index_name_is_removed(self):
        definition = (
            'CREATE INDEX "legacy index" '
            'ON public.assets_asset USING btree (asset_tag)'
        )

        canonical = canonicalize_index_definition(definition)

        self.assertEqual(
            canonical,
            'CREATE INDEX ON public.assets_asset USING btree (asset_tag)',
        )

    def test_equivalent_literal_array_cast_forms_are_canonicalized(self):
        fresh = (
            "CHECK ((status::text = ANY "
            "(ARRAY['active'::character varying, "
            "'pending'::character varying]::text[])))"
        )
        upgraded = (
            "CHECK ((status::text = ANY "
            "(ARRAY['active'::character varying::text, "
            "'pending'::character varying::text])))"
        )

        self.assertEqual(
            canonicalize_catalog_expression(fresh),
            canonicalize_catalog_expression(upgraded),
        )

    def test_non_array_casts_are_not_canonicalized(self):
        definition = (
            "CHECK ((status = 'active'::character varying))"
        )

        self.assertEqual(
            canonicalize_catalog_expression(definition),
            definition,
        )

    @patch('core.management.commands.capture_schema_evidence.connection')
    def test_command_emits_canonical_catalog_and_preserves_multiplicity(
        self, connection,
    ):
        connection.vendor = 'postgresql'
        connection.pg_version = 160010
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.side_effect = [
            [(
                'assets_asset', 'asset_tag', 'varchar', 'NO', None,
                None, 'NO', None, 'NEVER', None,
                'character varying(64)', 0,
            )],
            [
                ('assets_asset', 'constraint_old_name',
                 'UNIQUE (asset_tag)', False),
            ],
            [
                ('assets_asset', 'legacy_index_a',
                 'CREATE INDEX legacy_index_a ON public.assets_asset '
                 'USING btree (asset_tag)', True, True, True),
                ('assets_asset', 'legacy_index_b',
                 'CREATE INDEX legacy_index_b ON public.assets_asset '
                 'USING btree (asset_tag)', True, True, True),
            ],
            [('btree_gist', '1.7')],
            [('assets', 'asset'), ('core', 'emailsettings')],
            [
                ('assets', 'asset', 'add_asset'),
                ('assets', 'asset', 'view_asset'),
            ],
        ]
        stdout = io.StringIO()

        call_command('capture_schema_evidence', stdout=stdout)

        evidence = json.loads(stdout.getvalue())
        executed_sql = ' '.join(
            call.args[0]
            for call in cursor.execute.call_args_list
        )
        self.assertIn('format_type', executed_sql)
        self.assertIn('attndims', executed_sql)
        self.assertEqual(evidence['schema_version'], 1)
        self.assertEqual(evidence['postgresql_version_num'], 160010)
        self.assertEqual(evidence['summary'], {
            'columns': 1,
            'content_types': 2,
            'constraints': 1,
            'extensions': 1,
            'indexes': 2,
            'permissions': 2,
        })
        self.assertEqual(evidence['columns'], [[
            'assets_asset', 'asset_tag', 'varchar', 'NO', None,
            None, 'NO', None, 'NEVER', None,
            'character varying(64)', 0,
        ]])
        self.assertEqual(evidence['content_types'], [
            ['assets', 'asset'],
            ['core', 'emailsettings'],
        ])
        self.assertEqual(evidence['constraints'], [
            ['assets_asset', 'UNIQUE (asset_tag)', False],
        ])
        self.assertEqual(evidence['indexes'], [
            [
                'assets_asset',
                'CREATE INDEX ON public.assets_asset USING btree (asset_tag)',
                True,
                True,
                True,
            ],
            [
                'assets_asset',
                'CREATE INDEX ON public.assets_asset USING btree (asset_tag)',
                True,
                True,
                True,
            ],
        ])
        self.assertEqual(evidence['permissions'], [
            ['assets', 'asset', 'add_asset'],
            ['assets', 'asset', 'view_asset'],
        ])
        self.assertNotIn('legacy_index_a', stdout.getvalue())
        self.assertNotIn('constraint_old_name', stdout.getvalue())
