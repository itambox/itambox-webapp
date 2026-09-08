from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
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
