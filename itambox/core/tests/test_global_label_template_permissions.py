from types import SimpleNamespace

from django.test import SimpleTestCase

from itambox.views.features import LabelTemplateDeleteView, LabelTemplateEditView


class GlobalLabelTemplatePermissionTests(SimpleTestCase):
    def test_global_label_template_writes_are_superuser_only(self):
        for view_class in (LabelTemplateEditView, LabelTemplateDeleteView):
            view = view_class()
            view.request = SimpleNamespace(user=SimpleNamespace(is_superuser=False))
            self.assertFalse(view.has_permission())

            view.request = SimpleNamespace(user=SimpleNamespace(is_superuser=True))
            self.assertTrue(view.has_permission())
