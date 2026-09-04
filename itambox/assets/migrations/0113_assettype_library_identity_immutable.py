from django.db import migrations


FORWARD_SQL = """
CREATE OR REPLACE FUNCTION assets_assettype_library_identity_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.library_id IS DISTINCT FROM NEW.library_id
       OR OLD.library_definition_key IS DISTINCT FROM NEW.library_definition_key
       OR OLD.library_release IS DISTINCT FROM NEW.library_release
       OR OLD.source_checksum IS DISTINCT FROM NEW.source_checksum THEN
        RAISE EXCEPTION 'Asset Type library identity is immutable after creation'
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
