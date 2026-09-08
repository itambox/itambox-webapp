from contextlib import contextmanager

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import RequestFactory, TestCase

from core.context import override_current_tenant_scope, set_current_all_accessible
from extras.definition_forms import ChoiceSetUpdateForm, ChoiceUpdateForm
from extras.definition_views import (
    ChoiceSetDetailView,
    ChoiceSetRetireView,
    ChoiceSetUpdateView,
    ChoiceUpdateView,
)
from extras.models import CustomField, CustomFieldChoice, CustomFieldChoiceSet
from organization.models import Tenant

User = get_user_model()


class DefinitionManagementUITests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="definition-ui-editor")
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.choice_set = CustomFieldChoiceSet.objects.create(
            namespace="local",
            slug="display-modes",
            label="Display modes",
        )
        self.first = CustomFieldChoice.objects.create(
            choice_set=self.choice_set,
            key="compact",
            label="Compact",
            position=10,
        )
        self.second = CustomFieldChoice.objects.create(
            choice_set=self.choice_set,
            key="detailed",
            label="Detailed",
            position=20,
        )

    def tearDown(self):
        with override_current_tenant_scope(None):
            pass
        super().tearDown()

    def _request(self, method, path, data=None):
        request = getattr(self.factory, method)(path, data=data or {})
        request.user = self.user
        request.active_tenant = None
        request.active_tenant_group = None
        return request

    @contextmanager
    def _global_configuration_scope(self):
        with override_current_tenant_scope(None):
            set_current_all_accessible(True)
            yield

    def test_update_forms_never_expose_identity_keys(self):
        choice_set_form = ChoiceSetUpdateForm(instance=self.choice_set)
        choice_form = ChoiceUpdateForm(instance=self.first)

        self.assertNotIn("namespace", choice_set_form.fields)
        self.assertNotIn("slug", choice_set_form.fields)
        self.assertNotIn("key", choice_form.fields)

    def test_choice_update_rejects_stale_revision_and_keeps_key(self):
        request = self._request(
            "post",
            f"/choice-sets/{self.choice_set.pk}/choices/{self.first.pk}/edit/",
            {"label": "Compact view", "position": "30", "expected_resource_revision": "stale"},
        )
        with self._global_configuration_scope():
            response = ChoiceUpdateView.as_view()(request, choice_set_pk=self.choice_set.pk, pk=self.first.pk)

        self.assertEqual(response.status_code, 200)
        self.first.refresh_from_db()
        self.assertEqual(self.first.key, "compact")
        self.assertEqual(self.first.label, "Compact")

    def test_choice_update_changes_label_and_presentation_position_only(self):
        request = self._request(
            "post",
            f"/choice-sets/{self.choice_set.pk}/choices/{self.first.pk}/edit/",
            {
                "label": "Compact view",
                "position": "30",
                "expected_resource_revision": "placeholder",
            },
        )
        with self._global_configuration_scope():
            revision = ChoiceUpdateView.current_revision(self.first)
            request.POST = request.POST.copy()
            request.POST["expected_resource_revision"] = revision
            response = ChoiceUpdateView.as_view()(request, choice_set_pk=self.choice_set.pk, pk=self.first.pk)

        self.assertEqual(response.status_code, 302)
        self.first.refresh_from_db()
        self.assertEqual((self.first.key, self.first.label, self.first.position), ("compact", "Compact view", 30))

    def test_core_choice_set_is_read_only_for_browser_mutation(self):
        core = CustomFieldChoiceSet.objects.create(
            namespace="itambox",
            slug="core-modes",
            label="Core modes",
            management_kind=CustomFieldChoiceSet.MANAGEMENT_CORE,
        )
        request = self._request(
            "post",
            f"/choice-sets/{core.pk}/edit/",
            {"label": "Tampered", "expected_resource_revision": ChoiceSetUpdateView.current_revision(core)},
        )
        with self._global_configuration_scope():
            with self.assertRaises(PermissionDenied):
                ChoiceSetUpdateView.as_view()(request, pk=core.pk)
        core.refresh_from_db()
        self.assertEqual(core.label, "Core modes")

    def test_nested_choice_update_and_retire_bind_to_requested_parent(self):
        other_set = CustomFieldChoiceSet.objects.create(
            namespace="local",
            slug="other-display-modes",
            label="Other display modes",
        )
        update_request = self._request(
            "post",
            f"/choice-sets/{other_set.pk}/choices/{self.first.pk}/edit/",
            {
                "label": "Tampered",
                "position": "99",
                "expected_resource_revision": ChoiceUpdateView.current_revision(self.first),
            },
        )
        retire_request = self._request(
            "post",
            f"/choice-sets/{other_set.pk}/choices/{self.first.pk}/retire/",
            {
                "expected_resource_revision": ChoiceUpdateView.current_revision(self.first),
                "replacement_identity": "",
            },
        )
        with self._global_configuration_scope():
            with self.assertRaises(Http404):
                ChoiceUpdateView.as_view()(update_request, choice_set_pk=other_set.pk, pk=self.first.pk)
            with self.assertRaises(Http404):
                ChoiceRetireView.as_view()(retire_request, choice_set_pk=other_set.pk, pk=self.first.pk)

        self.first.refresh_from_db()
        self.assertEqual((self.first.key, self.first.label, self.first.position), ("compact", "Compact", 10))

    def test_unauthorized_nested_choice_requests_do_not_disclose_target_existence(self):
        tenant = Tenant.objects.create(name="Unauthorized scope", slug="unauthorized-definition-ui")
        limited_user = User.objects.create_user(username="definition-ui-unauthorized")
        for choice_pk in (self.first.pk, self.first.pk + 1000000):
            request = self._request(
                "post",
                f"/choice-sets/{self.choice_set.pk}/choices/{choice_pk}/edit/",
                {"label": "No access", "expected_resource_revision": "probe"},
            )
            request.user = limited_user
            request.active_tenant = tenant
            with override_current_tenant_scope(tenant):
                with self.assertRaises(PermissionDenied):
                    ChoiceUpdateView.as_view()(
                        request,
                        choice_set_pk=self.choice_set.pk,
                        pk=choice_pk,
                    )

    def test_tenant_limited_user_cannot_see_unscoped_usage_or_configure(self):
        tenant = Tenant.objects.create(name="Scoped tenant", slug="scoped-definition-ui")
        field = CustomField.objects.create(
            name="scoped_mode",
            namespace="local",
            label="Scoped mode",
            field_type=CustomField.FIELD_TYPE_SINGLE_SELECT,
            activation=CustomField.ACTIVATION_GLOBAL,
            choice_set=self.choice_set,
            max_values=1,
        )
        self.assertEqual(field.choice_set_id, self.choice_set.pk)
        limited_user = User.objects.create_user(username="definition-ui-limited")
        request = self._request("get", f"/choice-sets/{self.choice_set.pk}/")
        request.user = limited_user
        request.active_tenant = tenant
        with override_current_tenant_scope(tenant):
            view = ChoiceSetDetailView()
            view.request = request
            view.object = self.choice_set
            context = view.get_context_data(object=self.choice_set)
        self.assertFalse(context["show_usage_impact"])
        self.assertIsNone(context["usage_impact"])

        post = self._request(
            "post",
            f"/choice-sets/{self.choice_set.pk}/edit/",
            {
                "label": "No global edit",
                "expected_resource_revision": ChoiceSetUpdateView.current_revision(self.choice_set),
            },
        )
        post.user = limited_user
        post.active_tenant = tenant
        with override_current_tenant_scope(tenant):
            with self.assertRaises(PermissionDenied):
                ChoiceSetUpdateView.as_view()(post, pk=self.choice_set.pk)

    def test_retirement_uses_dependency_command_and_keeps_set_active_on_rejection(self):
        CustomField.objects.create(
            name="dependent_mode",
            namespace="local",
            label="Dependent mode",
            field_type=CustomField.FIELD_TYPE_SINGLE_SELECT,
            activation=CustomField.ACTIVATION_GLOBAL,
            choice_set=self.choice_set,
            max_values=1,
        )
        request = self._request(
            "post",
            f"/choice-sets/{self.choice_set.pk}/retire/",
            {
                "expected_resource_revision": ChoiceSetUpdateView.current_revision(self.choice_set),
                "replacement_identity": "",
            },
        )
        with self._global_configuration_scope():
            response = ChoiceSetRetireView.as_view()(request, pk=self.choice_set.pk)

        self.assertEqual(response.status_code, 200)
        self.choice_set.refresh_from_db()
        self.assertEqual(self.choice_set.lifecycle, CustomFieldChoiceSet.LIFECYCLE_ACTIVE)
