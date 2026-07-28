from unittest.mock import Mock, patch

from django.http import Http404
from django.test import SimpleTestCase

from extras.views import ReportTemplateDownloadView, ReportTriggerImmediateView


class ReportObjectPermissionTests(SimpleTestCase):
    def _assert_scoped_miss_denies(self, view_class):
        view = view_class()
        view.request = Mock()
        view.request.user = Mock()
        view.kwargs = {"pk": 42}

        with patch("extras.views.get_object_or_404", side_effect=Http404):
            self.assertFalse(view.has_permission())

        view.request.user.has_perms.assert_not_called()

    def test_scheduled_report_scoped_miss_does_not_fall_back_to_model_permission(self):
        self._assert_scoped_miss_denies(ReportTriggerImmediateView)

    def test_report_template_scoped_miss_does_not_fall_back_to_model_permission(self):
        self._assert_scoped_miss_denies(ReportTemplateDownloadView)
