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
    def test_library_identity_composition_and_category_defaults_are_relational(self):
        library = AssetTypeLibrary.objects.create(namespace="acme", release="2026.09")
        manufacturer = Manufacturer.objects.create(name="Example Networks", slug="example-networks")
        first = CustomFieldset.objects.create(
            name="Product",
            namespace="local",
            slug="product",
            label="Product",
        )
        second = CustomFieldset.objects.create(
            name="Networking",
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

        asset_type.library_definition_key = "renamed-definition"
        with self.assertRaises(ValidationError):
            asset_type.save()

        asset_type.refresh_from_db()
        asset_type.fieldset_memberships.all().delete()
        asset_type.delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            AssetType.objects.create(
                manufacturer=manufacturer,
                model="Switch 48P replacement",
                slug="example-networks-switch-48p-replacement",
                library=library,
                library_definition_key="switch-48p-rev-b",
            )
