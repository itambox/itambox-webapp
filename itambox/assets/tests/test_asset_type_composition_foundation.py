from datetime import timedelta
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.utils import timezone

from assets.models import (
    AssetType,
    AssetTypeFieldset,
    AssetTypeLibrary,
    Category,
    CategoryDefaultFieldset,
    Manufacturer,
)
from assets.models.catalog import _library_reconciliation_opt_in
from extras.models import CustomFieldset


def _attempt_identity_rewrite_with_reconcile_flag(asset_type_pk, replacement_key):
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('itambox.assettype_reconcile', 'on', true)")
    AssetType._base_manager.filter(pk=asset_type_pk).update(library_definition_key=replacement_key)


class AssetTypeCompositionFoundationTests(TestCase):
    def test_management_kind_requires_coherent_library_identity(self):
        with self.assertRaises(ValidationError):
            AssetTypeLibrary.objects.create(namespace="Bad Namespace", release="2026.09")

        library = AssetTypeLibrary.objects.create(namespace="identity", release="2026.09")
        manufacturer = Manufacturer.objects.create(name="Identity Manufacturer", slug="identity-manufacturer")
        invalid_variants = (
            {
                "management_kind": AssetType.MANAGEMENT_LOCAL,
                "library": library,
                "library_definition_key": "local-with-library",
                "library_release": "2026.09",
            },
            {
                "management_kind": AssetType.MANAGEMENT_LIBRARY,
                "library_definition_key": None,
                "library_release": "2026.09",
            },
            {
                "management_kind": AssetType.MANAGEMENT_LIBRARY,
                "library": library,
                "library_definition_key": "",
                "library_release": "2026.09",
            },
            {
                "management_kind": AssetType.MANAGEMENT_LIBRARY,
                "library": library,
                "library_definition_key": "missing-release",
                "library_release": None,
            },
            {
                "management_kind": AssetType.MANAGEMENT_LIBRARY,
                "library": library,
                "library_definition_key": "invalid key",
                "library_release": "2026.09",
            },
            {
                "management_kind": AssetType.MANAGEMENT_LIBRARY,
                "library": library,
                "library_definition_key": "valid-key",
                "library_release": "invalid release",
            },
        )

        for index, variant in enumerate(invalid_variants):
            with self.subTest(index=index), self.assertRaises(ValidationError):
                AssetType.objects.create(
                    manufacturer=manufacturer,
                    model=f"Invalid identity {index}",
                    slug=f"invalid-identity-{index}",
                    **variant,
                )

    def test_library_identity_db_constraint_rejects_queryset_update(self):
        library = AssetTypeLibrary.objects.create(namespace="db-identity", release="2026.09")
        manufacturer = Manufacturer.objects.create(name="DB Identity Manufacturer", slug="db-identity-manufacturer")
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="DB identity type",
            slug="db-identity-type",
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            AssetType._base_manager.filter(pk=asset_type.pk).update(
                library_id=library.pk,
                library_definition_key="invalid-local-state",
                library_release="2026.09",
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            AssetType._base_manager.filter(pk=asset_type.pk).update(
                library_id=library.pk,
                library_definition_key="invalid-local-state",
                library_release="",
            )

    def test_library_identity_db_constraint_rejects_valid_identity_rewrite(self):
        library = AssetTypeLibrary.objects.create(namespace="immutable-db", release="2026.09")
        manufacturer = Manufacturer.objects.create(name="Immutable DB Manufacturer", slug="immutable-db-manufacturer")
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Immutable DB type",
            slug="immutable-db-type",
            management_kind=AssetType.MANAGEMENT_LIBRARY,
            library=library,
            library_definition_key="original-definition",
            library_release="2026.09",
        )

        replacement_library = AssetTypeLibrary.objects.create(namespace="immutable-db-other", release="2026.09")
        attempted_updates = (
            {"library_id": replacement_library.pk},
            {"library_definition_key": "replacement-definition"},
            {"library_release": "2026.10"},
            {"source_checksum": "sha256:" + "1" * 64},
        )
        for update in attempted_updates:
            with self.assertRaises(IntegrityError), transaction.atomic():
                AssetType._base_manager.filter(pk=asset_type.pk).update(**update)
            asset_type.refresh_from_db()

        self.assertEqual(asset_type.library_definition_key, "original-definition")

    def test_library_identity_composition_and_category_defaults_are_relational(self):
        library = AssetTypeLibrary.objects.create(namespace="acme", release="2026.09")
        manufacturer = Manufacturer.objects.create(name="Example Networks", slug="example-networks")
        first = CustomFieldset.objects.create(
            namespace="local",
            slug="product",
            label="Product",
        )
        second = CustomFieldset.objects.create(
            namespace="local",
            slug="networking",
            label="Networking",
        )
        category = Category.objects.create(name="Switch", slug="switch")
        CategoryDefaultFieldset.objects.create(category=category, fieldset=first, position=10)
        CategoryDefaultFieldset.objects.create(category=category, fieldset=second, position=20)
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Switch 48P",
            slug="example-networks-switch-48p",
            category=category,
            management_kind=AssetType.MANAGEMENT_LIBRARY,
            library=library,
            library_definition_key="switch-48p-rev-b",
            library_release="2026.09",
            region="global",
            configuration="rev-b",
        )
        AssetTypeFieldset.objects.create(asset_type=asset_type, fieldset=first, position=10)
        AssetTypeFieldset.objects.create(asset_type=asset_type, fieldset=second, position=20)

        self.assertEqual(
            list(asset_type.fieldset_memberships.values_list("fieldset__slug", "position")),
            [("product", 10), ("networking", 20)],
        )
        self.assertEqual(
            list(category.default_fieldset_memberships.values_list("fieldset__slug", "position")),
            [("product", 10), ("networking", 20)],
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            AssetType.objects.create(
                manufacturer=manufacturer,
                model="Duplicate Switch 48P",
                slug="duplicate-switch-48p",
                management_kind=AssetType.MANAGEMENT_LIBRARY,
                library=library,
                library_definition_key="switch-48p-rev-b",
                library_release="2026.09",
            )

        for field_name, replacement in (
            ("library_definition_key", "renamed-definition"),
            ("library_release", "2026.10"),
            ("source_checksum", "sha256:" + "1" * 64),
        ):
            setattr(asset_type, field_name, replacement)
            with self.assertRaises(ValidationError):
                asset_type.save()
            asset_type.refresh_from_db()
        asset_type.delete()
        asset_type.refresh_from_db()
        self.assertIsNotNone(asset_type.deleted_at)
        self.assertEqual(
            list(asset_type.fieldset_memberships.values_list("fieldset_id", "position")),
            [(first.pk, 10), (second.pk, 20)],
        )
        asset_type.restore()
        asset_type.refresh_from_db()
        self.assertIsNone(asset_type.deleted_at)
        self.assertEqual(asset_type.fieldset_memberships.count(), 2)
        asset_type_pk = asset_type.pk
        asset_type.delete(force_hard_delete=True)
        self.assertFalse(AssetType._base_manager.filter(pk=asset_type_pk).exists())
        self.assertFalse(AssetTypeFieldset.objects.filter(asset_type_id=asset_type_pk).exists())
        replacement = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Switch 48P replacement",
            slug="example-networks-switch-48p-replacement",
            management_kind=AssetType.MANAGEMENT_LIBRARY,
            library=library,
            library_definition_key="switch-48p-rev-b",
            library_release="2026.09",
        )
        self.assertEqual(replacement.library_definition_key, "switch-48p-rev-b")

    def test_library_reconciliation_updates_release_and_checksum_through_controlled_path(self):
        library = AssetTypeLibrary.objects.create(namespace="reconcile-path", release="2026.09")
        manufacturer = Manufacturer.objects.create(name="Reconcile Manufacturer", slug="reconcile-manufacturer")
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Reconciled type",
            slug="reconciled-type",
            management_kind=AssetType.MANAGEMENT_LIBRARY,
            library=library,
            library_definition_key="router-x",
            library_release="2026.09",
            source_checksum="sha256:" + "a" * 64,
        )
        reconciled_at = timezone.now().replace(microsecond=0)

        asset_type.apply_library_reconciliation(
            library_release="2026.10",
            source_checksum="sha256:" + "b" * 64,
            reconciled_at=reconciled_at,
        )

        asset_type.refresh_from_db()
        self.assertEqual(asset_type.library_release, "2026.10")
        self.assertEqual(asset_type.source_checksum, "sha256:" + "b" * 64)
        self.assertEqual(asset_type.last_reconciled_at, reconciled_at)
        self.assertEqual(asset_type.library_id, library.pk)
        self.assertEqual(asset_type.library_definition_key, "router-x")

    def test_library_reconciliation_rejects_invalid_provenance_fail_closed(self):
        library = AssetTypeLibrary.objects.create(namespace="reconcile-invalid", release="2026.09")
        manufacturer = Manufacturer.objects.create(
            name="Reconcile Invalid Manufacturer", slug="reconcile-invalid-manufacturer"
        )
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Reconcile invalid type",
            slug="reconcile-invalid-type",
            management_kind=AssetType.MANAGEMENT_LIBRARY,
            library=library,
            library_definition_key="router-y",
            library_release="2026.09",
            source_checksum="sha256:" + "c" * 64,
        )

        for library_release, source_checksum in (
            (None, "sha256:" + "d" * 64),
            ("2026.10", "not-a-checksum"),
            ("invalid release!", "sha256:" + "d" * 64),
        ):
            with self.subTest(release=library_release, checksum=source_checksum):
                with self.assertRaises(ValidationError):
                    asset_type.apply_library_reconciliation(
                        library_release=library_release,
                        source_checksum=source_checksum,
                    )
                # Neither the database nor the caller's instance may carry the
                # failed target state.
                self.assertEqual(asset_type.library_release, "2026.09")
                self.assertEqual(asset_type.source_checksum, "sha256:" + "c" * 64)
                asset_type.refresh_from_db()
                self.assertEqual(asset_type.library_release, "2026.09")
                self.assertEqual(asset_type.source_checksum, "sha256:" + "c" * 64)

    def test_reconciliation_state_change_fails_closed_at_db_boundary_without_session_flag(self):
        library = AssetTypeLibrary.objects.create(namespace="reconcile-db", release="2026.09")
        manufacturer = Manufacturer.objects.create(name="Reconcile DB Manufacturer", slug="reconcile-db-manufacturer")
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Reconcile db type",
            slug="reconcile-db-type",
            management_kind=AssetType.MANAGEMENT_LIBRARY,
            library=library,
            library_definition_key="router-z",
            library_release="2026.09",
            source_checksum="sha256:" + "e" * 64,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            AssetType._base_manager.filter(pk=asset_type.pk).update(library_release="2026.11")
        asset_type.refresh_from_db()
        self.assertEqual(asset_type.library_release, "2026.09")

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('itambox.assettype_reconcile', 'on', true)")
            AssetType._base_manager.filter(pk=asset_type.pk).update(
                library_release="2026.11",
                source_checksum="sha256:" + "f" * 64,
            )
        asset_type.refresh_from_db()
        self.assertEqual(asset_type.library_release, "2026.11")
        self.assertEqual(asset_type.source_checksum, "sha256:" + "f" * 64)

    def test_reconciliation_session_flag_does_not_unlock_immutable_identity(self):
        library = AssetTypeLibrary.objects.create(namespace="reconcile-identity", release="2026.09")
        manufacturer = Manufacturer.objects.create(
            name="Reconcile Identity Manufacturer", slug="reconcile-identity-manufacturer"
        )
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Reconcile identity type",
            slug="reconcile-identity-type",
            management_kind=AssetType.MANAGEMENT_LIBRARY,
            library=library,
            library_definition_key="router-id",
            library_release="2026.09",
        )

        asset_type.apply_library_reconciliation(
            library_release="2026.10",
            source_checksum="sha256:" + "a" * 64,
        )
        asset_type.refresh_from_db()
        self.assertEqual(asset_type.library_definition_key, "router-id")
        self.assertEqual(asset_type.library_id, library.pk)

        with self.assertRaises(IntegrityError), transaction.atomic():
            _attempt_identity_rewrite_with_reconcile_flag(asset_type.pk, "hacked-key")
        asset_type.refresh_from_db()
        self.assertEqual(asset_type.library_definition_key, "router-id")

    def test_reconciliation_opt_in_does_not_leak_into_outer_transaction(self):
        library = AssetTypeLibrary.objects.create(namespace="reconcile-leak", release="2026.09")
        manufacturer = Manufacturer.objects.create(
            name="Reconcile Leak Manufacturer", slug="reconcile-leak-manufacturer"
        )
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Reconcile leak type",
            slug="reconcile-leak-type",
            management_kind=AssetType.MANAGEMENT_LIBRARY,
            library=library,
            library_definition_key="router-leak",
            library_release="2026.09",
        )

        with transaction.atomic():
            asset_type.apply_library_reconciliation(
                library_release="2026.10",
                source_checksum="sha256:" + "b" * 64,
            )
            # A later Library v1 importer will wrap multiple reconciliation
            # operations in one outer transaction. A direct release/checksum
            # write in that same transaction must not inherit the previous
            # method's opt-in.
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    AssetType._base_manager.filter(pk=asset_type.pk).update(source_checksum="sha256:" + "c" * 64)

        asset_type.refresh_from_db()
        self.assertEqual(asset_type.library_release, "2026.10")
        self.assertEqual(asset_type.source_checksum, "sha256:" + "b" * 64)

    def test_reconciliation_opt_in_does_not_leak_after_outer_transaction_commit(self):
        library = AssetTypeLibrary.objects.create(namespace="reconcile-leak-commit", release="2026.09")
        manufacturer = Manufacturer.objects.create(
            name="Reconcile Leak Commit Manufacturer", slug="reconcile-leak-commit-manufacturer"
        )
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Reconcile leak commit type",
            slug="reconcile-leak-commit-type",
            management_kind=AssetType.MANAGEMENT_LIBRARY,
            library=library,
            library_definition_key="router-leak-commit",
            library_release="2026.09",
        )

        with transaction.atomic():
            asset_type.apply_library_reconciliation(
                library_release="2026.10",
                source_checksum="sha256:" + "b" * 64,
            )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AssetType._base_manager.filter(pk=asset_type.pk).update(source_checksum="sha256:" + "c" * 64)
        asset_type.refresh_from_db()
        self.assertEqual(asset_type.source_checksum, "sha256:" + "b" * 64)

    def test_reconciliation_method_rejects_identity_change_on_instance(self):
        library = AssetTypeLibrary.objects.create(namespace="reconcile-method-identity", release="2026.09")
        manufacturer = Manufacturer.objects.create(
            name="Reconcile Method Identity Manufacturer", slug="reconcile-method-identity-manufacturer"
        )
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Reconcile method identity type",
            slug="reconcile-method-identity-type",
            management_kind=AssetType.MANAGEMENT_LIBRARY,
            library=library,
            library_definition_key="router-method-id",
            library_release="2026.09",
        )

        asset_type.library_definition_key = "hacked-key"
        with self.assertRaises(ValidationError):
            asset_type.apply_library_reconciliation(
                library_release="2026.10",
                source_checksum="sha256:" + "b" * 64,
            )
        asset_type.refresh_from_db()
        self.assertEqual(asset_type.library_definition_key, "router-method-id")
        self.assertEqual(asset_type.library_release, "2026.09")
        self.assertIsNone(asset_type.source_checksum)
        self.assertIsNone(asset_type.last_reconciled_at)

    def test_reconciliation_preserves_preexisting_opt_in_state(self):
        library = AssetTypeLibrary.objects.create(namespace="reconcile-preserve", release="2026.09")
        manufacturer = Manufacturer.objects.create(
            name="Reconcile Preserve Manufacturer", slug="reconcile-preserve-manufacturer"
        )
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Reconcile preserve type",
            slug="reconcile-preserve-type",
            management_kind=AssetType.MANAGEMENT_LIBRARY,
            library=library,
            library_definition_key="router-preserve",
            library_release="2026.09",
        )

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('itambox.assettype_reconcile', 'on', true)")
            asset_type.apply_library_reconciliation(
                library_release="2026.10",
                source_checksum="sha256:" + "b" * 64,
            )
            # The helper restored the caller's pre-existing opt-in instead of
            # forcing it off, so a direct controlled write still succeeds.
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_setting('itambox.assettype_reconcile', true)")
                self.assertEqual(cursor.fetchone()[0], "on")
            AssetType._base_manager.filter(pk=asset_type.pk).update(library_release="2026.11")

        asset_type.refresh_from_db()
        self.assertEqual(asset_type.library_release, "2026.11")

    def test_reconciliation_write_failure_does_not_leak_opt_in(self):
        library = AssetTypeLibrary.objects.create(namespace="reconcile-fail-leak", release="2026.09")
        manufacturer = Manufacturer.objects.create(
            name="Reconcile Fail Leak Manufacturer", slug="reconcile-fail-leak-manufacturer"
        )
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Reconcile fail leak type",
            slug="reconcile-fail-leak-type",
            management_kind=AssetType.MANAGEMENT_LIBRARY,
            library=library,
            library_definition_key="router-fail-leak",
            library_release="2026.09",
        )

        with transaction.atomic():
            # The identity rewrite fails in the trigger even while the
            # reconciliation opt-in is active; the exception must not leave
            # the surrounding transaction opted in.
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    with _library_reconciliation_opt_in("default"):
                        AssetType._base_manager.filter(pk=asset_type.pk).update(library_definition_key="hacked-key")
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_setting('itambox.assettype_reconcile', true)")
                self.assertIn(cursor.fetchone()[0] or None, (None, "off"))
            # Functional proof: a release/checksum write is rejected again.
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    AssetType._base_manager.filter(pk=asset_type.pk).update(library_release="2026.12")

        asset_type.refresh_from_db()
        self.assertEqual(asset_type.library_definition_key, "router-fail-leak")
        self.assertEqual(asset_type.library_release, "2026.09")

    def test_stale_reconciliation_instance_fails_closed_and_keeps_state_coherent(self):
        library = AssetTypeLibrary.objects.create(namespace="reconcile-stale", release="2026.09")
        manufacturer = Manufacturer.objects.create(
            name="Reconcile Stale Manufacturer", slug="reconcile-stale-manufacturer"
        )
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Reconcile stale type",
            slug="reconcile-stale-type",
            management_kind=AssetType.MANAGEMENT_LIBRARY,
            library=library,
            library_definition_key="router-stale",
            library_release="2026.09",
            source_checksum="sha256:" + "a" * 64,
        )

        first = AssetType.objects.get(pk=asset_type.pk)
        second = AssetType.objects.get(pk=asset_type.pk)

        first.apply_library_reconciliation(
            library_release="2026.10",
            source_checksum="sha256:" + "b" * 64,
        )

        with self.assertRaises(ValidationError):
            second.apply_library_reconciliation(
                library_release="2026.11",
                source_checksum="sha256:" + "c" * 64,
            )

        # The stale caller instance was not mutated towards the failed target
        # state.
        self.assertEqual(second.library_release, "2026.09")
        self.assertEqual(second.source_checksum, "sha256:" + "a" * 64)
        self.assertIsNone(second.last_reconciled_at)

        # The database still holds the first caller's reconciliation result.
        second.refresh_from_db()
        self.assertEqual(second.library_release, "2026.10")
        self.assertEqual(second.source_checksum, "sha256:" + "b" * 64)
        self.assertIsNotNone(second.last_reconciled_at)

    def test_reconciliation_defaults_last_reconciled_at_to_now(self):
        library = AssetTypeLibrary.objects.create(namespace="reconcile-stamp", release="2026.09")
        manufacturer = Manufacturer.objects.create(
            name="Reconcile Stamp Manufacturer", slug="reconcile-stamp-manufacturer"
        )
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Reconcile stamp type",
            slug="reconcile-stamp-type",
            management_kind=AssetType.MANAGEMENT_LIBRARY,
            library=library,
            library_definition_key="router-stamp",
            library_release="2026.09",
        )

        before = timezone.now()
        asset_type.apply_library_reconciliation(
            library_release="2026.10",
            source_checksum="sha256:" + "b" * 64,
        )
        asset_type.refresh_from_db()
        self.assertIsNotNone(asset_type.last_reconciled_at)
        self.assertGreaterEqual(asset_type.last_reconciled_at, before - timedelta(seconds=10))
        self.assertLessEqual(asset_type.last_reconciled_at, timezone.now() + timedelta(seconds=10))
        self.assertEqual(asset_type.library_release, "2026.10")
        self.assertEqual(asset_type.source_checksum, "sha256:" + "b" * 64)

    def test_reconciliation_save_failure_restores_instance_and_keeps_unsaved_fields(self):
        library = AssetTypeLibrary.objects.create(namespace="reconcile-restore", release="2026.09")
        manufacturer = Manufacturer.objects.create(
            name="Reconcile Restore Manufacturer", slug="reconcile-restore-manufacturer"
        )
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Reconcile restore type",
            slug="reconcile-restore-type",
            management_kind=AssetType.MANAGEMENT_LIBRARY,
            library=library,
            library_definition_key="router-restore",
            library_release="2026.09",
            source_checksum="sha256:" + "a" * 64,
        )
        # Unrelated unsaved caller state must survive a failed reconciliation.
        asset_type.description = "unsaved caller field"

        with mock.patch.object(AssetType, "save", side_effect=IntegrityError("trigger refused the write")):
            with self.assertRaises(IntegrityError):
                asset_type.apply_library_reconciliation(
                    library_release="2026.10",
                    source_checksum="sha256:" + "b" * 64,
                )

        # The failed target state has been restored on the caller instance...
        self.assertEqual(asset_type.library_release, "2026.09")
        self.assertEqual(asset_type.source_checksum, "sha256:" + "a" * 64)
        self.assertIsNone(asset_type.last_reconciled_at)
        self.assertEqual(asset_type.description, "unsaved caller field")
        # ...and the database never saw the write.
        asset_type.refresh_from_db()
        self.assertEqual(asset_type.library_release, "2026.09")
        self.assertEqual(asset_type.source_checksum, "sha256:" + "a" * 64)

    def test_reconciliation_preflight_rejects_unsaved_instance(self):
        library = AssetTypeLibrary.objects.create(namespace="reconcile-unsaved", release="2026.09")
        manufacturer = Manufacturer.objects.create(
            name="Reconcile Unsaved Manufacturer", slug="reconcile-unsaved-manufacturer"
        )
        unsaved = AssetType(
            manufacturer=manufacturer,
            model="Reconcile unsaved type",
            management_kind=AssetType.MANAGEMENT_LIBRARY,
            library=library,
            library_definition_key="router-unsaved",
            library_release="2026.09",
        )

        with self.assertRaises(ValidationError):
            unsaved.apply_library_reconciliation(
                library_release="2026.10",
                source_checksum="sha256:" + "b" * 64,
            )
