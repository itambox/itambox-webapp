"""Normalized #479 definition and provenance guards.

Installs the final database guards for the reusable specification vocabulary:
permanent definition identities, immutable definition/choice identities, the
fieldset/activation coupling, and the Specification Library / legacy provenance
archives.
"""

from django.db import migrations


DEFINITION_GUARDS_SQL = """
CREATE OR REPLACE FUNCTION extras_permanent_definition_delete_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'Reusable definition identities are permanent; deprecate the row instead'
        USING ERRCODE = 'check_violation';
    RETURN OLD;
END;
$$;

CREATE TRIGGER extras_customfield_permanent_delete_guard
BEFORE DELETE ON extras_customfield FOR EACH ROW
EXECUTE FUNCTION extras_permanent_definition_delete_guard();
CREATE TRIGGER extras_customfieldset_permanent_delete_guard
BEFORE DELETE ON extras_customfieldset FOR EACH ROW
EXECUTE FUNCTION extras_permanent_definition_delete_guard();
CREATE TRIGGER extras_customfieldchoiceset_permanent_delete_guard
BEFORE DELETE ON extras_customfieldchoiceset FOR EACH ROW
EXECUTE FUNCTION extras_permanent_definition_delete_guard();
CREATE TRIGGER extras_customfieldchoice_permanent_delete_guard
BEFORE DELETE ON extras_customfieldchoice FOR EACH ROW
EXECUTE FUNCTION extras_permanent_definition_delete_guard();

CREATE OR REPLACE FUNCTION extras_customfieldchoice_identity_update_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.choice_set_id IS DISTINCT FROM NEW.choice_set_id
       OR OLD.key IS DISTINCT FROM NEW.key THEN
        RAISE EXCEPTION 'Choice identity is immutable after creation'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER extras_customfieldchoice_identity_update_guard
BEFORE UPDATE OF choice_set_id, key ON extras_customfieldchoice FOR EACH ROW
EXECUTE FUNCTION extras_customfieldchoice_identity_update_guard();

CREATE OR REPLACE FUNCTION extras_customfieldsetfield_global_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM 1
    FROM extras_customfield
    WHERE id = NEW.custom_field_id
    FOR NO KEY UPDATE;
    IF EXISTS (
        SELECT 1 FROM extras_customfield
        WHERE id = NEW.custom_field_id AND activation = 'global'
    ) THEN
        RAISE EXCEPTION 'Global Custom Fields cannot join Fieldsets'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER extras_customfieldsetfield_global_guard
BEFORE INSERT OR UPDATE OF custom_field_id ON extras_customfieldsetfield
FOR EACH ROW EXECUTE FUNCTION extras_customfieldsetfield_global_guard();

CREATE OR REPLACE FUNCTION extras_customfield_activation_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.activation = 'global'
       AND EXISTS (
           SELECT 1 FROM extras_customfieldsetfield
           WHERE custom_field_id = NEW.id
       ) THEN
        RAISE EXCEPTION 'A Custom Field with memberships cannot become global'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER extras_customfield_activation_guard
BEFORE UPDATE OF activation ON extras_customfield
FOR EACH ROW EXECUTE FUNCTION extras_customfield_activation_guard();
"""

DEFINITION_GUARDS_REVERSE_SQL = """
DROP TRIGGER IF EXISTS extras_customfield_activation_guard ON extras_customfield;
DROP FUNCTION IF EXISTS extras_customfield_activation_guard();
DROP TRIGGER IF EXISTS extras_customfieldsetfield_global_guard ON extras_customfieldsetfield;
DROP FUNCTION IF EXISTS extras_customfieldsetfield_global_guard();
DROP TRIGGER IF EXISTS extras_customfieldchoice_identity_update_guard ON extras_customfieldchoice;
DROP FUNCTION IF EXISTS extras_customfieldchoice_identity_update_guard();
DROP TRIGGER IF EXISTS extras_customfieldchoice_permanent_delete_guard ON extras_customfieldchoice;
DROP TRIGGER IF EXISTS extras_customfieldchoiceset_permanent_delete_guard ON extras_customfieldchoiceset;
DROP TRIGGER IF EXISTS extras_customfieldset_permanent_delete_guard ON extras_customfieldset;
DROP TRIGGER IF EXISTS extras_customfield_permanent_delete_guard ON extras_customfield;
DROP FUNCTION IF EXISTS extras_permanent_definition_delete_guard();
"""

FINAL_PROVENANCE_GUARDS_SQL = """
DROP TRIGGER IF EXISTS extras_customfield_identity_update_guard ON extras_customfield;
DROP TRIGGER IF EXISTS extras_customfieldset_identity_update_guard ON extras_customfieldset;
DROP TRIGGER IF EXISTS extras_customfieldchoiceset_identity_update_guard ON extras_customfieldchoiceset;

CREATE OR REPLACE FUNCTION extras_customfield_identity_update_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.name IS DISTINCT FROM NEW.name
       OR OLD.namespace IS DISTINCT FROM NEW.namespace
       OR OLD.library_id IS DISTINCT FROM NEW.library_id
       OR OLD.connector_identity IS DISTINCT FROM NEW.connector_identity THEN
        RAISE EXCEPTION 'Custom Field identity or source link is immutable after creation'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER extras_customfield_identity_update_guard
BEFORE UPDATE OF name, namespace, library_id, connector_identity ON extras_customfield FOR EACH ROW
EXECUTE FUNCTION extras_customfield_identity_update_guard();

CREATE OR REPLACE FUNCTION extras_customfieldset_identity_update_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.namespace IS DISTINCT FROM NEW.namespace
       OR OLD.slug IS DISTINCT FROM NEW.slug
       OR OLD.library_id IS DISTINCT FROM NEW.library_id
       OR OLD.connector_identity IS DISTINCT FROM NEW.connector_identity THEN
        RAISE EXCEPTION 'Custom Fieldset identity or source link is immutable after creation'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER extras_customfieldset_identity_update_guard
BEFORE UPDATE OF namespace, slug, library_id, connector_identity ON extras_customfieldset FOR EACH ROW
EXECUTE FUNCTION extras_customfieldset_identity_update_guard();

CREATE OR REPLACE FUNCTION extras_customfieldchoiceset_identity_update_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.namespace IS DISTINCT FROM NEW.namespace
       OR OLD.slug IS DISTINCT FROM NEW.slug
       OR OLD.library_id IS DISTINCT FROM NEW.library_id
       OR OLD.connector_identity IS DISTINCT FROM NEW.connector_identity THEN
        RAISE EXCEPTION 'Choice Set identity or source link is immutable after creation'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER extras_customfieldchoiceset_identity_update_guard
BEFORE UPDATE OF namespace, slug, library_id, connector_identity ON extras_customfieldchoiceset FOR EACH ROW
EXECUTE FUNCTION extras_customfieldchoiceset_identity_update_guard();

CREATE OR REPLACE FUNCTION specificationlibrary_identity_delete_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Specification Library identities are permanent'
            USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.namespace IS DISTINCT FROM NEW.namespace THEN
        RAISE EXCEPTION 'Specification Library namespace is immutable after creation'
            USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.accepted_release_id IS DISTINCT FROM NEW.accepted_release_id THEN
        IF COALESCE(current_setting('itambox.specification_library_reconcile', true), 'off') <> 'on' THEN
            RAISE EXCEPTION 'Accepted Release changes require the library reconciliation path'
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.accepted_release_id IS NOT NULL
           AND NOT EXISTS (
               SELECT 1
               FROM extras_specificationlibraryrelease
               WHERE id = NEW.accepted_release_id
                 AND library_id = NEW.id
           ) THEN
            RAISE EXCEPTION 'Accepted Release must belong to the same Specification Library'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER specificationlibrary_identity_delete_guard
BEFORE UPDATE OF namespace, accepted_release_id OR DELETE
ON extras_specificationlibrary
FOR EACH ROW
EXECUTE FUNCTION specificationlibrary_identity_delete_guard();

CREATE OR REPLACE FUNCTION specificationlibraryrelease_immutable_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'Specification Library Release rows are immutable and retained'
        USING ERRCODE = 'check_violation';
    RETURN OLD;
END;
$$;
CREATE TRIGGER specificationlibraryrelease_immutable_guard
BEFORE UPDATE OR DELETE ON extras_specificationlibraryrelease
FOR EACH ROW
EXECUTE FUNCTION specificationlibraryrelease_immutable_guard();

CREATE OR REPLACE FUNCTION legacy_provenance_archive_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'Legacy provenance rows are immutable transition evidence'
        USING ERRCODE = 'check_violation';
    RETURN OLD;
END;
$$;
CREATE TRIGGER legacy_provenance_archive_guard
BEFORE UPDATE OR DELETE ON extras_specificationlibrarylegacyprovenance
FOR EACH ROW
EXECUTE FUNCTION legacy_provenance_archive_guard();

ALTER TABLE extras_specificationlibraryrelease
    ADD CONSTRAINT specificationlibraryrelease_digest_format
        CHECK (semantic_digest ~ '^sha256:[0-9a-f]{64}$'),
    ADD CONSTRAINT specificationlibraryrelease_document_object
        CHECK (jsonb_typeof(source_document) = 'object');
ALTER TABLE extras_specificationlibrarylegacyprovenance
    ADD CONSTRAINT specificationlibrarylegacyprovenance_owner_kind_valid
        CHECK (owner_kind IN ('library', 'asset_type', 'custom_field', 'custom_fieldset', 'choice_set', 'choice')),
    ADD CONSTRAINT specificationlibrarylegacyprovenance_disposition_valid
        CHECK (disposition IN ('uninitialized', 'unreconciled'));
"""

FINAL_PROVENANCE_GUARDS_REVERSE_SQL = """
ALTER TABLE extras_specificationlibrarylegacyprovenance
    DROP CONSTRAINT IF EXISTS specificationlibrarylegacyprovenance_disposition_valid,
    DROP CONSTRAINT IF EXISTS specificationlibrarylegacyprovenance_owner_kind_valid;
ALTER TABLE extras_specificationlibraryrelease
    DROP CONSTRAINT IF EXISTS specificationlibraryrelease_document_object,
    DROP CONSTRAINT IF EXISTS specificationlibraryrelease_digest_format;
DROP TRIGGER IF EXISTS legacy_provenance_archive_guard ON extras_specificationlibrarylegacyprovenance;
DROP FUNCTION IF EXISTS legacy_provenance_archive_guard();
DROP TRIGGER IF EXISTS specificationlibraryrelease_immutable_guard ON extras_specificationlibraryrelease;
DROP FUNCTION IF EXISTS specificationlibraryrelease_immutable_guard();
DROP TRIGGER IF EXISTS specificationlibrary_identity_delete_guard ON extras_specificationlibrary;
DROP FUNCTION IF EXISTS specificationlibrary_identity_delete_guard();
DROP TRIGGER IF EXISTS extras_customfieldchoiceset_identity_update_guard ON extras_customfieldchoiceset;
DROP FUNCTION IF EXISTS extras_customfieldchoiceset_identity_update_guard();
DROP TRIGGER IF EXISTS extras_customfieldset_identity_update_guard ON extras_customfieldset;
DROP FUNCTION IF EXISTS extras_customfieldset_identity_update_guard();
DROP TRIGGER IF EXISTS extras_customfield_identity_update_guard ON extras_customfield;
DROP FUNCTION IF EXISTS extras_customfield_identity_update_guard();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("extras", "0116_asset_type_definition_cutover"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.RunSQL(DEFINITION_GUARDS_SQL, reverse_sql=DEFINITION_GUARDS_REVERSE_SQL),
        migrations.RunSQL(FINAL_PROVENANCE_GUARDS_SQL, reverse_sql=FINAL_PROVENANCE_GUARDS_REVERSE_SQL),
    ]
