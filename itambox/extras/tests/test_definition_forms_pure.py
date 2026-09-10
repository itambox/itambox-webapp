from django.test import SimpleTestCase

from extras.definition_forms import (
    ChoiceRetireForm,
    ChoiceSetRetireForm,
    ChoiceSetUpdateForm,
    ChoiceUpdateForm,
)


class DefinitionRevisionFormTests(SimpleTestCase):
    def test_edit_and_retire_forms_collect_rendered_revision_tokens(self):
        form_classes = (
            ChoiceSetUpdateForm,
            ChoiceSetRetireForm,
            ChoiceUpdateForm,
            ChoiceRetireForm,
        )

        for form_class in form_classes:
            with self.subTest(form=form_class.__name__):
                form = form_class(expected_resource_revision="probe-rendered-revision")
                self.assertIn("expected_resource_revision", form.fields)
                self.assertTrue(form.fields["expected_resource_revision"].required)
                self.assertEqual(
                    form.fields["expected_resource_revision"].initial,
                    "probe-rendered-revision",
                )

    def test_submitted_revision_is_not_recomputed_by_form_cleaning(self):
        form = ChoiceUpdateForm(
            data={
                "expected_resource_revision": "submitted-revision",
                "label": "Updated label",
                "position": "20",
                "replacement_identity": "",
            }
        )

        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["expected_resource_revision"], "submitted-revision")
