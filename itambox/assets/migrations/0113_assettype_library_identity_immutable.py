from django.db import migrations

# Source identity (`library_id`, `library_definition_key`) is immutable after
# creation. Release and checksum are controlled *reconciliation state*: they may
# only change through the library reconciliation path, which opts the write into
# the transaction-local `itambox.assettype_reconcile` setting. Direct
# QuerySet/SQL writes that change release/checksum fail closed; identity
# rewrites fail closed unconditionally, including under the reconciliation flag.
FORWARD_SQL = """
CREATE OR REPLACE FUNCTION assets_assettype_library_identity_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.library_id IS DISTINCT FROM NEW.library_id
       OR OLD.library_definition_key IS DISTINCT FROM NEW.library_definition_key THEN
        RAISE EXCEPTION 'Asset Type library identity is immutable after creation'
            USING ERRCODE = 'check_violation';
    END IF;
    IF (OLD.library_release IS DISTINCT FROM NEW.library_release
        OR OLD.source_checksum IS DISTINCT FROM NEW.source_checksum)
       AND COALESCE(current_setting('itambox.assettype_reconcile', true), 'off') <> 'on' THEN
        RAISE EXCEPTION 'Asset Type library reconciliation state changes require the reconciliation path'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER assets_assettype_library_identity_guard
BEFORE UPDATE OF library_id, library_definition_key, library_release, source_checksum
ON assets_assettype
FOR EACH ROW
EXECUTE FUNCTION assets_assettype_library_identity_guard();
"""

REVERSE_SQL = """
DROP TRIGGER IF EXISTS assets_assettype_library_identity_guard ON assets_assettype;
DROP FUNCTION IF EXISTS assets_assettype_library_identity_guard();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("assets", "0112_alter_assettype_library_definition_key_and_more"),
        ("extras", "0117_alter_customfieldchoice_choice_set_and_more"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]