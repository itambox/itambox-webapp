from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.http import QueryDict
from django.test import SimpleTestCase

from assets.forms.assettype_form import AssetTypeForm


class AssetTypeFormPreconditionTests(SimpleTestCase):
    def _form(self, *, resource="rendered-resource", definition="rendered-definition", selection=()):
        form = AssetTypeForm.__new__(AssetTypeForm)
        form.cleaned_data = {
            "expected_resource_revision": resource,
            "expected_definition_revision": definition,
        }
        form._create_selection = lambda: SimpleNamespace(presence="explicit", identities=selection)
        form._patch = lambda: object()
        return form

    def _render_form(self, *, bound, reload_request=False, data=None):
        form = AssetTypeForm.__new__(AssetTypeForm)
        form.instance = SimpleNamespace(pk=23)
        form.is_bound = bound
        form.data = data or QueryDict(mutable=True)
        form.request = SimpleNamespace(
            headers={"HX-Request": "true"} if reload_request else {},
        )
        form.fields = {
            "expected_resource_revision": SimpleNamespace(initial=""),
            "expected_definition_revision": SimpleNamespace(initial=""),
        }
        form.specification_definition_revision = ""
        return form

    def test_initial_render_issues_hidden_prospective_tokens(self):
        form = self._render_form(bound=False)
        selected = (SimpleNamespace(namespace="local", slug="second"), SimpleNamespace(namespace="local", slug="first"))
        plan = SimpleNamespace(resource_revision="resource-preview", definition_revision="definition-preview")

        with patch("assets.forms.assettype_form.prospective_specification_plan", return_value=plan) as preview:
            form._set_render_preconditions(selected)

        preview.assert_called_once()
        self.assertEqual(form.fields["expected_resource_revision"].initial, "resource-preview")
        self.assertEqual(form.fields["expected_definition_revision"].initial, "definition-preview")
        self.assertEqual(form.specification_definition_revision, "definition-preview")

    def test_hx_reload_replaces_hidden_tokens_for_changed_composition_preview(self):
        data = QueryDict(
            "_reload=1&expected_resource_revision=old-resource&expected_definition_revision=old-definition"
        )
        form = self._render_form(bound=True, reload_request=True, data=data)
        plan = SimpleNamespace(resource_revision="new-resource", definition_revision="new-definition")

        with patch("assets.forms.assettype_form.prospective_specification_plan", return_value=plan) as preview:
            form._set_render_preconditions((SimpleNamespace(namespace="local", slug="only"),))

        preview.assert_called_once()
        self.assertEqual(form.data["expected_resource_revision"], "new-resource")
        self.assertEqual(form.data["expected_definition_revision"], "new-definition")

    def test_normal_post_preserves_supplied_tokens_without_preview_refresh(self):
        data = QueryDict(
            "expected_resource_revision=submitted-resource&expected_definition_revision=submitted-definition"
        )
        form = self._render_form(bound=True, data=data)

        with patch("assets.forms.assettype_form.prospective_specification_plan") as preview:
            form._set_render_preconditions((SimpleNamespace(namespace="local", slug="changed"),))

        preview.assert_not_called()
        self.assertEqual(form.data["expected_resource_revision"], "submitted-resource")
        self.assertEqual(form.data["expected_definition_revision"], "submitted-definition")
        self.assertEqual(form.specification_definition_revision, "submitted-definition")

    def test_changed_order_uses_preview_tokens_without_rebuilding_them(self):
        form = self._form(selection=("local/second", "local/first"))
        owner = SimpleNamespace(pk=17)
        actor = object()

        with (
            patch("assets.forms.assettype_form.set_asset_type_composition") as command,
            patch("assets.forms.assettype_form.prospective_specification_plan") as prospective,
            patch("assets.forms.assettype_form.current_specification_plan") as current,
            patch("assets.forms.assettype_form.require_command_success"),
        ):
            form._command_update(owner, actor)

        kwargs = command.call_args.kwargs
        self.assertEqual(kwargs["expected_resource_revision"], "rendered-resource")
        self.assertEqual(kwargs["expected_definition_revision"], "rendered-definition")
        self.assertEqual(kwargs["fieldsets"].identities, ("local/second", "local/first"))
        prospective.assert_not_called()
        current.assert_not_called()

    def test_explicit_empty_composition_uses_preview_tokens_without_rebuilding_them(self):
        form = self._form(selection=())
        owner = SimpleNamespace(pk=18)
        actor = object()

        with (
            patch("assets.forms.assettype_form.set_asset_type_composition") as command,
            patch("assets.forms.assettype_form.prospective_specification_plan") as prospective,
            patch("assets.forms.assettype_form.current_specification_plan") as current,
            patch("assets.forms.assettype_form.require_command_success"),
        ):
            form._command_update(owner, actor)

        kwargs = command.call_args.kwargs
        self.assertEqual(kwargs["fieldsets"].identities, ())
        self.assertEqual(kwargs["expected_resource_revision"], "rendered-resource")
        self.assertEqual(kwargs["expected_definition_revision"], "rendered-definition")
        prospective.assert_not_called()
        current.assert_not_called()

    def test_missing_submitted_preconditions_are_rejected_without_refresh(self):
        form = self._form(resource="", definition="")
        owner = SimpleNamespace(pk=19)
        actor = object()

        with (
            patch("assets.forms.assettype_form.set_asset_type_composition") as command,
            patch("assets.forms.assettype_form.update_asset_type_specifications") as update,
            patch("assets.forms.assettype_form.prospective_specification_plan") as prospective,
            patch("assets.forms.assettype_form.current_specification_plan") as current,
            self.assertRaises(ValidationError),
        ):
            form._command_update(owner, actor)

        command.assert_not_called()
        update.assert_not_called()
        prospective.assert_not_called()
        current.assert_not_called()
