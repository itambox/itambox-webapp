from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from assets.models import (
    AssetType,
    AssetTypeFieldset,
    AssetTypeLibrary,
    Category,
    CategoryDefaultFieldset,
    Manufacturer,
)
from extras.models import CustomFieldset


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

        asset_type.library_definition_key = "renamed-definition"
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
