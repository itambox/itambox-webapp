"""Normalized #479 Asset Type specification identity guards.

Installs the final database guard: Asset Type specification identity columns are
immutable after creation and core/library identities cannot be hard-deleted.
"""

from django.db import migrations


ASSET_TYPE_GUARD_SQL = """
CREATE OR REPLACE FUNCTION assettype_specification_identity_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND (OLD.library_id IS DISTINCT FROM NEW.library_id
            OR OLD.library_definition_key IS DISTINCT FROM NEW.library_definition_key
            OR OLD.connector_identity IS DISTINCT FROM NEW.connector_identity) THEN
        RAISE EXCEPTION 'Asset Type specification identity is immutable after creation'
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'DELETE' AND OLD.management_kind IN ('core', 'library') THEN
        RAISE EXCEPTION 'Core and library Asset Type identities cannot be hard-deleted'
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER assettype_specification_identity_guard
BEFORE UPDATE OF library_id, library_definition_key, connector_identity OR DELETE
ON assets_assettype
FOR EACH ROW
EXECUTE FUNCTION assettype_specification_identity_guard();
"""

ASSET_TYPE_GUARD_REVERSE_SQL = """
DROP TRIGGER IF EXISTS assettype_specification_identity_guard ON assets_assettype;
DROP FUNCTION IF EXISTS assettype_specification_identity_guard();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0106_asset_type_composition_cutover"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.RunSQL(ASSET_TYPE_GUARD_SQL, reverse_sql=ASSET_TYPE_GUARD_REVERSE_SQL),
    ]
