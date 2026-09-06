from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from assets.models import AssetType, AssetTypeFieldset, Category, CategoryDefaultFieldset, Manufacturer
from extras.models import (
    CustomFieldset,
    SpecificationLibrary,
    SpecificationLibraryRelease,
    _specification_library_reconcile_opt_in,
    release_document_digest,
)


def _release_document(library, sequence):
    """Build the normalized release object used by the current provenance contract."""
    return {
        "kind": "itambox.type-library.release",
        "library": {"namespace": library.namespace, "release": sequence},
        "schema_version": 1,
    }


def _release(library, sequence):
    document = _release_document(library, sequence)
    return SpecificationLibraryRelease.objects.create(
        library=library,
        sequence=sequence,
        semantic_digest=release_document_digest(document),
        source_document=document,
    )


class AssetTypeCompositionFoundationTests(TestCase):
    def test_management_kind_requires_coherent_library_identity(self):
        library = SpecificationLibrary.objects.create(namespace="identity")
        manufacturer = Manufacturer.objects.create(name="Identity Manufacturer", slug="identity-manufacturer")
        invalid_variants = (
            {
                "management_kind": AssetType.MANAGEMENT_LOCAL,
                "library": library,
                "library_definition_key": "local-with-library",
            },
            {
                "management_kind": AssetType.MANAGEMENT_LIBRARY,
                "library_definition_key": None,
            },
            {
                "management_kind": AssetType.MANAGEMENT_LIBRARY,
                "library": library,
                "library_definition_key": "",
            },
            {
                "management_kind": AssetType.MANAGEMENT_LIBRARY,
                "library": library,
                "library_definition_key": "invalid key",
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

    def test_library_identity_db_guard_rejects_direct_coherence_updates(self):
        library = SpecificationLibrary.objects.create(namespace="db-identity")
        manufacturer = Manufacturer.objects.create(name="DB Identity Manufacturer", slug="db-identity-manufacturer")
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="DB identity type",
            slug="db-identity-type",
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            AssetType._base_manager.filter(pk=asset_type.pk).update(management_kind=AssetType.MANAGEMENT_LIBRARY)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AssetType._base_manager.filter(pk=asset_type.pk).update(
                library_id=library.pk,
                library_definition_key="became-library",
            )

        asset_type.refresh_from_db()
        self.assertEqual(asset_type.management_kind, AssetType.MANAGEMENT_LOCAL)
        self.assertIsNone(asset_type.library_id)
        self.assertIsNone(asset_type.library_definition_key)

    def test_library_managed_type_identity_is_immutable_under_direct_updates(self):
        library = SpecificationLibrary.objects.create(namespace="immutable-db")
        replacement_library = SpecificationLibrary.objects.create(namespace="immutable-db-other")
        release = _release(library, 1)
        manufacturer = Manufacturer.objects.create(name="Immutable DB Manufacturer", slug="immutable-db-manufacturer")
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Immutable DB type",
            slug="immutable-db-type",
            management_kind=AssetType.MANAGEMENT_LIBRARY,
            library=library,
            library_definition_key="original-definition",
            connector_identity=release.semantic_digest,
        )
        replacement_identity = release_document_digest(_release_document(library, 2))
        attempted_updates = (
            {"library_id": replacement_library.pk},
            {"library_definition_key": "replacement-definition"},
            {"connector_identity": replacement_identity},
        )

        for update in attempted_updates:
            with self.subTest(update=update), self.assertRaises(IntegrityError), transaction.atomic():
                AssetType._base_manager.filter(pk=asset_type.pk).update(**update)
            asset_type.refresh_from_db()
            self.assertEqual(asset_type.library_id, library.pk)
            self.assertEqual(asset_type.library_definition_key, "original-definition")
            self.assertEqual(asset_type.connector_identity, release.semantic_digest)

    def test_library_identity_composition_and_category_defaults_are_relational(self):
        library = SpecificationLibrary.objects.create(namespace="acme")
        manufacturer = Manufacturer.objects.create(name="Example Networks", slug="example-networks")
        first = CustomFieldset.objects.create(namespace="local", slug="product", label="Product")
        second = CustomFieldset.objects.create(namespace="local", slug="networking", label="Networking")
        category = Category.objects.create(name="Switch", slug="switch")
        CategoryDefaultFieldset.objects.create(category=category, fieldset=first, position=1)
        CategoryDefaultFieldset.objects.create(category=category, fieldset=second, position=2)
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Switch 48P",
            slug="example-networks-switch-48p",
            category=category,
            management_kind=AssetType.MANAGEMENT_LIBRARY,
            library=library,
            library_definition_key="switch-48p-rev-b",
            region="global",
            configuration="rev-b",
        )
        AssetTypeFieldset.objects.create(asset_type=asset_type, fieldset=first, position=1)
        AssetTypeFieldset.objects.create(asset_type=asset_type, fieldset=second, position=2)

        self.assertEqual(
            list(asset_type.fieldset_memberships.values_list("fieldset__slug", "position")),
            [("product", 1), ("networking", 2)],
        )
        self.assertEqual(
            list(category.default_fieldset_memberships.values_list("fieldset__slug", "position")),
            [("product", 1), ("networking", 2)],
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            AssetType.objects.create(
                manufacturer=manufacturer,
                model="Duplicate Switch 48P",
                slug="duplicate-switch-48p",
                management_kind=AssetType.MANAGEMENT_LIBRARY,
                library=library,
                library_definition_key="switch-48p-rev-b",
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            AssetTypeFieldset.objects.create(asset_type=asset_type, fieldset=first, position=3)

        asset_type.delete()
        asset_type.refresh_from_db()
        self.assertIsNotNone(asset_type.deleted_at)
        self.assertEqual(
            list(asset_type.fieldset_memberships.values_list("fieldset_id", "position")),
            [(first.pk, 1), (second.pk, 2)],
        )
        asset_type.restore()
        asset_type.refresh_from_db()
        self.assertIsNone(asset_type.deleted_at)
        self.assertEqual(asset_type.fieldset_memberships.count(), 2)

    def test_library_managed_type_hard_delete_is_rejected_and_preserves_memberships(self):
        library = SpecificationLibrary.objects.create(namespace="protected-type")
        manufacturer = Manufacturer.objects.create(name="Protected Manufacturer", slug="protected-manufacturer")
        fieldset = CustomFieldset.objects.create(namespace="local", slug="protected", label="Protected")
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Protected model",
            slug="protected-model",
            management_kind=AssetType.MANAGEMENT_LIBRARY,
            library=library,
            library_definition_key="protected-definition",
        )
        membership = AssetTypeFieldset.objects.create(asset_type=asset_type, fieldset=fieldset, position=1)

        with self.assertRaises(IntegrityError), transaction.atomic():
            asset_type.delete(force_hard_delete=True)

        self.assertTrue(AssetType.all_objects.filter(pk=asset_type.pk).exists())
        self.assertTrue(AssetTypeFieldset.objects.filter(pk=membership.pk).exists())
        self.assertTrue(SpecificationLibrary.objects.filter(pk=library.pk).exists())

    def test_local_asset_type_hard_delete_cascades_owned_memberships(self):
        manufacturer = Manufacturer.objects.create(name="Local Manufacturer", slug="local-manufacturer")
        fieldset = CustomFieldset.objects.create(namespace="local", slug="local", label="Local")
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Local model",
            slug="local-model",
        )
        membership = AssetTypeFieldset.objects.create(asset_type=asset_type, fieldset=fieldset, position=1)
        asset_type_pk = asset_type.pk
        membership_pk = membership.pk

        asset_type.delete(force_hard_delete=True)

        self.assertFalse(AssetType.all_objects.filter(pk=asset_type_pk).exists())
        self.assertFalse(AssetTypeFieldset.objects.filter(pk=membership_pk).exists())
        self.assertTrue(CustomFieldset.objects.filter(pk=fieldset.pk).exists())

    def test_library_accept_release_advances_shared_pointer_with_real_digest(self):
        library = SpecificationLibrary.objects.create(namespace="acceptance")
        first = _release(library, 1)
        second = _release(library, 2)

        library.accept_release(first)
        library.accept_release(second)
        library.refresh_from_db()

        self.assertEqual(library.accepted_release_id, second.pk)
        self.assertEqual(library.accepted_release.sequence, 2)
        self.assertEqual(
            library.accepted_release.semantic_digest, release_document_digest(library.accepted_release.source_document)
        )

    def test_library_accept_release_requires_same_library_and_non_older_sequence(self):
        library = SpecificationLibrary.objects.create(namespace="monotonic")
        first = _release(library, 1)
        second = _release(library, 2)
        other_library = SpecificationLibrary.objects.create(namespace="other")
        foreign = _release(other_library, 1)
        library.accept_release(second)

        with self.assertRaises(ValidationError):
            library.accept_release(first)
        with self.assertRaises(ValidationError):
            library.accept_release(foreign)

        library.refresh_from_db()
        self.assertEqual(library.accepted_release_id, second.pk)

    def test_release_rejects_mismatched_real_digest(self):
        library = SpecificationLibrary.objects.create(namespace="digest-validation")
        first_document = _release_document(library, 1)
        second_document = _release_document(library, 2)
        first_digest = release_document_digest(first_document)
        second_digest = release_document_digest(second_document)

        self.assertNotEqual(first_digest, second_digest)
        with self.assertRaises(ValidationError):
            SpecificationLibraryRelease.objects.create(
                library=library,
                sequence=1,
                semantic_digest=first_digest,
                source_document=second_document,
            )
        self.assertFalse(SpecificationLibraryRelease.objects.filter(library=library, sequence=1).exists())

    def test_release_source_digest_and_sequence_are_immutable(self):
        library = SpecificationLibrary.objects.create(namespace="release-immutable")
        release = _release(library, 1)
        replacement_document = _release_document(library, 2)
        replacement_digest = release_document_digest(replacement_document)

        release.source_document = replacement_document
        with self.assertRaises(ValidationError):
            release.save()
        release.refresh_from_db()
        self.assertEqual(release.sequence, 1)

        attempted_updates = (
            {"sequence": 2},
            {"semantic_digest": replacement_digest},
            {"source_document": replacement_document},
        )
        for update in attempted_updates:
            with self.subTest(update=update), self.assertRaises(IntegrityError), transaction.atomic():
                SpecificationLibraryRelease._base_manager.filter(pk=release.pk).update(**update)
            release.refresh_from_db()
            self.assertEqual(release.sequence, 1)
            self.assertEqual(release.semantic_digest, release_document_digest(release.source_document))

        with self.assertRaises(IntegrityError), transaction.atomic():
            _release(library, 1)

    def test_library_pointer_model_and_database_guards_reject_direct_writes(self):
        library = SpecificationLibrary.objects.create(namespace="pointer-guards")
        first = _release(library, 1)
        second = _release(library, 2)
        library.accept_release(first)

        library.accepted_release = second
        with self.assertRaises(ValidationError):
            library.save(update_fields=["accepted_release", "updated_at"])
        library.refresh_from_db()
        self.assertEqual(library.accepted_release_id, first.pk)

        with self.assertRaises(IntegrityError), transaction.atomic():
            SpecificationLibrary._base_manager.filter(pk=library.pk).update(accepted_release_id=second.pk)
        library.refresh_from_db()
        self.assertEqual(library.accepted_release_id, first.pk)

    def test_reconcile_opt_in_restores_setting_after_success(self):
        library = SpecificationLibrary.objects.create(namespace="restore-success")
        first = _release(library, 1)
        second = _release(library, 2)
        library.accept_release(first)

        with transaction.atomic():
            with _specification_library_reconcile_opt_in("default"):
                SpecificationLibrary._base_manager.filter(pk=library.pk).update(accepted_release_id=second.pk)
                with connection.cursor() as cursor:
                    cursor.execute("SELECT current_setting('itambox.specification_library_reconcile', true)")
                    self.assertEqual(cursor.fetchone()[0], "on")
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_setting('itambox.specification_library_reconcile', true)")
                self.assertIn(cursor.fetchone()[0] or None, (None, "off"))

        library.refresh_from_db()
        self.assertEqual(library.accepted_release_id, second.pk)

    def test_reconcile_opt_in_does_not_leak_after_successful_acceptance(self):
        library = SpecificationLibrary.objects.create(namespace="nonleak-success")
        first = _release(library, 1)
        second = _release(library, 2)
        library.accept_release(first)

        with transaction.atomic():
            library.accept_release(second)
            with self.assertRaises(IntegrityError), transaction.atomic():
                SpecificationLibrary._base_manager.filter(pk=library.pk).update(accepted_release_id=first.pk)

        library.refresh_from_db()
        self.assertEqual(library.accepted_release_id, second.pk)

    def test_reconcile_opt_in_does_not_leak_after_failed_update(self):
        library = SpecificationLibrary.objects.create(namespace="nonleak-failure")
        first = _release(library, 1)
        second = _release(library, 2)
        other_library = SpecificationLibrary.objects.create(namespace="nonleak-foreign")
        foreign = _release(other_library, 1)
        library.accept_release(first)

        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    with _specification_library_reconcile_opt_in("default"):
                        SpecificationLibrary._base_manager.filter(pk=library.pk).update(accepted_release_id=foreign.pk)
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_setting('itambox.specification_library_reconcile', true)")
                self.assertIn(cursor.fetchone()[0] or None, (None, "off"))
            with self.assertRaises(IntegrityError), transaction.atomic():
                SpecificationLibrary._base_manager.filter(pk=library.pk).update(accepted_release_id=second.pk)

        library.refresh_from_db()
        self.assertEqual(library.accepted_release_id, first.pk)

    def test_reconcile_opt_in_does_not_unlock_namespace_identity(self):
        library = SpecificationLibrary.objects.create(namespace="namespace-immutable")

        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    with _specification_library_reconcile_opt_in("default"):
                        SpecificationLibrary._base_manager.filter(pk=library.pk).update(namespace="renamed")

        library.refresh_from_db()
        self.assertEqual(library.namespace, "namespace-immutable")

        library.namespace = "renamed"
        with self.assertRaises(ValidationError):
            library.save()
        library.refresh_from_db()
        self.assertEqual(library.namespace, "namespace-immutable")

    def test_reconcile_opt_in_preserves_preexisting_setting(self):
        library = SpecificationLibrary.objects.create(namespace="restore-preexisting")
        first = _release(library, 1)
        second = _release(library, 2)
        library.accept_release(first)

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('itambox.specification_library_reconcile', 'on', true)")
            with _specification_library_reconcile_opt_in("default"):
                SpecificationLibrary._base_manager.filter(pk=library.pk).update(accepted_release_id=second.pk)
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_setting('itambox.specification_library_reconcile', true)")
                self.assertEqual(cursor.fetchone()[0], "on")
            SpecificationLibrary._base_manager.filter(pk=library.pk).update(accepted_release_id=first.pk)

        library.refresh_from_db()
        self.assertEqual(library.accepted_release_id, first.pk)

    def test_accept_release_rejects_unsaved_library(self):
        saved_library = SpecificationLibrary.objects.create(namespace="saved-for-unsaved")
        release = _release(saved_library, 1)
        unsaved_library = SpecificationLibrary(namespace="unsaved-library")

        with self.assertRaises(ValidationError):
            unsaved_library.accept_release(release)

        self.assertIsNone(unsaved_library.pk)

    def test_failed_pointer_advance_preserves_in_memory_and_database_state(self):
        library = SpecificationLibrary.objects.create(namespace="failed-pointer")
        first = _release(library, 1)
        second = _release(library, 2)
        other_library = SpecificationLibrary.objects.create(namespace="failed-pointer-other")
        foreign = _release(other_library, 1)
        library.accept_release(second)

        with self.assertRaises(ValidationError):
            library.accept_release(first)
        self.assertEqual(library.accepted_release_id, second.pk)

        with self.assertRaises(ValidationError):
            library.accept_release(foreign)
        self.assertEqual(library.accepted_release_id, second.pk)

        library.refresh_from_db()
        self.assertEqual(library.accepted_release_id, second.pk)
