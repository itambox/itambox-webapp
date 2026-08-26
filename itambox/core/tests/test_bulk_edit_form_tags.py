from django import forms
from django.test import TestCase

from core.forms import BulkEditForm
from extras.models import Tag
from organization.models import Location


class BulkEditFormTagTests(TestCase):
    def test_tag_fields_keep_default_manager_querysets_and_widgets(self):
        active = Tag.objects.create(name="Active tag", slug="active-tag")
        deleted = Tag.objects.create(name="Deleted tag", slug="deleted-tag")
        deleted.delete()

        form = BulkEditForm(model=Location)

        for field_name in ("add_tags", "remove_tags"):
            field = form.fields[field_name]
            self.assertIsInstance(field, forms.ModelMultipleChoiceField)
            self.assertFalse(field.required)
            self.assertEqual(list(field.queryset), [active])
            self.assertEqual(field.widget.attrs["class"], "form-select")
            self.assertIn("data-tom-select", field.widget.attrs)
