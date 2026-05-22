from django.test import TestCase
from extras.models import Tag, CustomField, CustomFieldset
from extras.filters import TagFilter, CustomFieldFilterSet, CustomFieldsetFilterSet

class TagFilterTest(TestCase):
    def setUp(self):
        self.tag1 = Tag.objects.create(name="Production", slug="prod", description="Production environment")
        self.tag2 = Tag.objects.create(name="Staging", slug="stage", description="Staging environment")
        self.tag3 = Tag.objects.create(name="DevOps", slug="devops", description="DevOps team tags")

    def test_tag_filter_empty_search(self):
        filterset = TagFilter(data={}, queryset=Tag.objects.all())
        self.assertEqual(filterset.qs.count(), 3)

    def test_tag_filter_search_name(self):
        filterset = TagFilter(data={'q': 'Prod'}, queryset=Tag.objects.all())
        self.assertEqual(filterset.qs.count(), 1)
        self.assertEqual(filterset.qs.first(), self.tag1)

    def test_tag_filter_search_slug(self):
        filterset = TagFilter(data={'q': 'stage'}, queryset=Tag.objects.all())
        self.assertEqual(filterset.qs.count(), 1)
        self.assertEqual(filterset.qs.first(), self.tag2)

    def test_tag_filter_search_description(self):
        filterset = TagFilter(data={'q': 'environment'}, queryset=Tag.objects.all())
        self.assertEqual(filterset.qs.count(), 2)
        self.assertIn(self.tag1, filterset.qs)
        self.assertIn(self.tag2, filterset.qs)


class CustomFieldFilterSetTest(TestCase):
    def setUp(self):
        self.cf1 = CustomField.objects.create(
            name="cost_center",
            label="Cost Center",
            field_type=CustomField.FIELD_TYPE_TEXT,
            required=True
        )
        self.cf2 = CustomField.objects.create(
            name="department",
            label="Department Info",
            field_type=CustomField.FIELD_TYPE_TEXT,
            required=False
        )

    def test_filter_empty_search(self):
        filterset = CustomFieldFilterSet(data={}, queryset=CustomField.objects.all())
        self.assertEqual(filterset.qs.count(), 2)

    def test_filter_search_name(self):
        filterset = CustomFieldFilterSet(data={'q': 'cost'}, queryset=CustomField.objects.all())
        self.assertEqual(filterset.qs.count(), 1)
        self.assertEqual(filterset.qs.first(), self.cf1)

    def test_filter_search_label(self):
        filterset = CustomFieldFilterSet(data={'q': 'Info'}, queryset=CustomField.objects.all())
        self.assertEqual(filterset.qs.count(), 1)
        self.assertEqual(filterset.qs.first(), self.cf2)

    def test_filter_by_field_type_and_required(self):
        filterset = CustomFieldFilterSet(data={'field_type': 'text', 'required': True}, queryset=CustomField.objects.all())
        self.assertEqual(filterset.qs.count(), 1)
        self.assertEqual(filterset.qs.first(), self.cf1)


class CustomFieldsetFilterSetTest(TestCase):
    def setUp(self):
        self.cfs1 = CustomFieldset.objects.create(name="Server Configuration")
        self.cfs2 = CustomFieldset.objects.create(name="Network Configuration")

    def test_fieldset_filter_empty_search(self):
        filterset = CustomFieldsetFilterSet(data={}, queryset=CustomFieldset.objects.all())
        self.assertEqual(filterset.qs.count(), 2)

    def test_fieldset_filter_search_name(self):
        filterset = CustomFieldsetFilterSet(data={'q': 'Server'}, queryset=CustomFieldset.objects.all())
        self.assertEqual(filterset.qs.count(), 1)
        self.assertEqual(filterset.qs.first(), self.cfs1)
