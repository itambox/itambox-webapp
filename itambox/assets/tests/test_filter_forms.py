from django.test import TestCase
from django.urls import reverse
from organization.models import Location, Tenant, Site, AssetHolder
from assets.models import Asset, AssetType, Manufacturer, Supplier, StatusLabel, Category, AssetAssignment
from extras.models import Tag
from assets.forms.filter_forms import AssetFilterForm

class AssetFilterFormTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test Tenant", slug="test-tenant")
        self.site = Site.objects.create(name="Test Site", slug="test-site", status="active", tenant=self.tenant)
        self.location1 = Location.objects.create(name="Location 1", slug="loc-1", site=self.site, tenant=self.tenant)
        self.location2 = Location.objects.create(name="Location 2", slug="loc-2", site=self.site, tenant=self.tenant)
        
        self.manufacturer = Manufacturer.objects.create(name="Dell", slug="dell")
        self.category = Category.objects.create(name="Laptops", slug="laptops")
        self.asset_type = AssetType.objects.create(manufacturer=self.manufacturer, model="Latitude 5420", category=self.category)
        self.supplier = Supplier.objects.create(name="Dell Supplier", slug="dell-supplier")
        self.status = StatusLabel.objects.create(name="In Storage", slug="in-storage")
        
        self.tag1 = Tag.objects.create(name="Laptop", slug="laptop")
        self.tag2 = Tag.objects.create(name="Hardware", slug="hardware")

        self.asset = Asset.objects.create(
            name="Laptop 1",
            asset_tag="TAG-1",
            asset_type=self.asset_type,
            status=self.status,
            location=self.location1,
            tenant=self.tenant,
            supplier=self.supplier
        )
        self.asset.tags.add(self.tag1)

        self.holder = AssetHolder.objects.create(
            first_name="John",
            last_name="Doe",
            upn="john.doe@example.com",
            tenant=self.tenant
        )
        self.assignment = AssetAssignment.objects.create(
            asset=self.asset,
            assigned_user=self.holder,
            is_active=True
        )

    def test_empty_form_ajax_attributes_and_empty_querysets(self):
        """Verify that when no filters are selected, querysets are empty but Tom Select attrs are set."""
        form = AssetFilterForm(data={})
        
        ajax_fields = ['location', 'asset_type', 'manufacturer', 'supplier', 'tenant', 'tags', 'category', 'assigned_to']
        for field_name in ajax_fields:
            field = form.fields[field_name]
            
            # Check widgets have Tom Select attributes
            self.assertIn('data-tom-select', field.widget.attrs)
            self.assertIn('data-tom-select-url', field.widget.attrs)
            
            # Check queryset is empty
            self.assertEqual(field.queryset.count(), 0)

    def test_bound_form_filters_queryset_to_selected_values(self):
        """Verify that when a value is selected, the field's queryset only contains that value."""
        data = {
            'location': str(self.location1.pk),
            'tags': [self.tag1.slug],
            'category': str(self.category.pk),
            'assigned_to': str(self.holder.pk),
        }
        form = AssetFilterForm(data=data)
        
        # Location queryset should only have location1
        self.assertEqual(form.fields['location'].queryset.count(), 1)
        self.assertEqual(form.fields['location'].queryset.first(), self.location1)
        
        # Tags queryset should only have tag1
        self.assertEqual(form.fields['tags'].queryset.count(), 1)
        self.assertEqual(form.fields['tags'].queryset.first(), self.tag1)
        
        # Category queryset should only have category
        self.assertEqual(form.fields['category'].queryset.count(), 1)
        self.assertEqual(form.fields['category'].queryset.first(), self.category)

        # Assigned_to queryset should only have holder
        self.assertEqual(form.fields['assigned_to'].queryset.count(), 1)
        self.assertEqual(form.fields['assigned_to'].queryset.first(), self.holder)
        
        # Others should still be empty
        self.assertEqual(form.fields['asset_type'].queryset.count(), 0)
        self.assertEqual(form.fields['manufacturer'].queryset.count(), 0)

        # Form should validate successfully
        self.assertTrue(form.is_valid())
        
        # Verify filtered results contain correct queryset
        qs = form.search()
        self.assertIn(self.asset, qs)

    def test_bound_form_filters_exclude_unmatching(self):
        """Verify that mismatching filter values exclude the asset from search results."""
        # Category mismatch
        other_category = Category.objects.create(name="Monitors", slug="monitors")
        form = AssetFilterForm(data={'category': str(other_category.pk)})
        self.assertTrue(form.is_valid())
        self.assertNotIn(self.asset, form.search())

        # Assigned_to mismatch
        other_holder = AssetHolder.objects.create(
            first_name="Jane",
            last_name="Smith",
            upn="jane.smith@example.com",
            tenant=self.tenant
        )
        form = AssetFilterForm(data={'assigned_to': str(other_holder.pk)})
        self.assertTrue(form.is_valid())
        self.assertNotIn(self.asset, form.search())

    def test_non_ajax_select_fields_have_tom_select(self):
        """Verify that non-AJAX select/dropdown fields also get the data-tom-select attribute for consistency."""
        form = AssetFilterForm(data={})
        for field_name in ['status', 'asset_role']:
            field = form.fields[field_name]
            self.assertIn('data-tom-select', field.widget.attrs)
            self.assertNotIn('data-tom-select-url', field.widget.attrs)


from assets.forms.assettype_form import AssetTypeForm

class AssetTypeFormTest(TestCase):
    def test_asset_type_form_select_fields_have_tom_select(self):
        """Verify that all select fields in AssetTypeForm get the data-tom-select attribute automatically."""
        form = AssetTypeForm()
        select_fields = ['manufacturer', 'category', 'asset_role', 'custom_fieldset', 'depreciation', 'tags']
        for field_name in select_fields:
            field = form.fields[field_name]
            self.assertIn('data-tom-select', field.widget.attrs)
