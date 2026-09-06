import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class MigrationConflict(RuntimeError):
    pass


def reverse_refused(apps, schema_editor):
    raise MigrationConflict("issue479:t07:reverse_refused")


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
        ("assets", "0115_issue479_t07_provenance_bridge"),
        ("extras", "0119_issue479_t07_provenance_schema"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="customfield",
            name="managed_paths",
        ),
        migrations.RemoveField(
            model_name="customfield",
            name="source_checksum",
        ),
        migrations.RemoveField(
            model_name="customfield",
            name="last_reconciled_at",
        ),
        migrations.RemoveField(
            model_name="customfieldset",
            name="managed_paths",
        ),
        migrations.RemoveField(
            model_name="customfieldset",
            name="source_checksum",
        ),
        migrations.RemoveField(
            model_name="customfieldset",
            name="last_reconciled_at",
        ),
        migrations.RemoveField(
            model_name="customfieldchoiceset",
            name="managed_paths",
        ),
        migrations.RemoveField(
            model_name="customfieldchoiceset",
            name="source_checksum",
        ),
        migrations.RemoveField(
            model_name="customfieldchoiceset",
            name="last_reconciled_at",
        ),
        migrations.RemoveField(
            model_name="customfieldchoice",
            name="management_kind",
        ),
        migrations.RemoveField(
            model_name="customfieldchoice",
            name="managed_paths",
        ),
        migrations.RemoveField(
            model_name="customfieldchoice",
            name="source_checksum",
        ),
        migrations.RemoveField(
            model_name="customfieldchoice",
            name="last_reconciled_at",
        ),
        migrations.AlterField(
            model_name="customfield",
            name="namespace",
            field=models.CharField(
                default="local",
                max_length=62,
                validators=[django.core.validators.RegexValidator(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")],
            ),
        ),
        migrations.AlterField(
            model_name="customfieldset",
            name="namespace",
            field=models.CharField(
                default="local",
                max_length=62,
                validators=[django.core.validators.RegexValidator(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")],
            ),
        ),
        migrations.AlterField(
            model_name="customfieldchoiceset",
            name="namespace",
            field=models.CharField(
                max_length=62,
                validators=[django.core.validators.RegexValidator(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")],
            ),
        ),
        migrations.AddConstraint(
            model_name="customfield",
            constraint=models.CheckConstraint(
                condition=(
                    (models.Q(management_kind="library") & models.Q(library__isnull=False))
                    | (models.Q(management_kind__in=["core", "local"]) & models.Q(library__isnull=True))
                ),
                name="customfield_library_management_coherence",
            ),
        ),
        migrations.AddConstraint(
            model_name="customfieldset",
            constraint=models.CheckConstraint(
                condition=(
                    (models.Q(management_kind="library") & models.Q(library__isnull=False))
                    | (models.Q(management_kind__in=["core", "local"]) & models.Q(library__isnull=True))
                ),
                name="customfieldset_library_management_coherence",
            ),
        ),
        migrations.AddConstraint(
            model_name="customfieldchoiceset",
            constraint=models.CheckConstraint(
                condition=(
                    (models.Q(management_kind="library") & models.Q(library__isnull=False))
                    | (models.Q(management_kind__in=["core", "local"]) & models.Q(library__isnull=True))
                ),
                name="customfieldchoiceset_library_management_coherence",
            ),
        ),
        migrations.RunPython(migrations.RunPython.noop, reverse_code=reverse_refused),
        migrations.RunSQL(
            FINAL_PROVENANCE_GUARDS_SQL,
            reverse_sql=FINAL_PROVENANCE_GUARDS_REVERSE_SQL,
        ),
    ]
