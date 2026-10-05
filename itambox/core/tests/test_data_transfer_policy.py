"""Contract: every installed model carries an explicit import and export declaration."""

from django.apps import apps
from django.test import SimpleTestCase

from core.data_transfer import DECLARATIONS, DENIED, policy_for, policy_for_label
from core.importers.bulk_forms import get_registered_import_form, is_model_importable

# Lifecycle rows a domain service owns. A raw-field import could put them into
# states the service never produces, so they must never be importable.
SERVICE_OWNED = (
    "assets.assetassignment",
    "assets.assetdisposal",
    "assets.assetreservation",
    "compliance.custodyreceipt",
    "compliance.custodysigningsession",
    "inventory.accessoryassignment",
    "inventory.componentallocation",
    "inventory.consumableassignment",
    "licenses.licenseseatassignment",
    "subscriptions.subscriptionassignment",
)

# Authentication, RBAC, and generated or system data stay out of both directions
# of the generic importer.
SECURITY_SENSITIVE = (
    "organization.membership",
    "organization.role",
    "organization.rolegrant",
    "organization.rolegrantscope",
    "organization.tenant",
    "organization.tenantresourcegrant",
    "users.groupmembership",
    "users.token",
    "users.user",
    "users.usergroup",
)


class DataTransferPolicyContractTests(SimpleTestCase):
    def test_every_installed_model_has_an_explicit_declaration(self):
        labels = {model._meta.label_lower for model in apps.get_models()}
        undeclared = sorted(labels - set(DECLARATIONS))
        self.assertEqual(undeclared, [], "declare import and export for these models in core/data_transfer.py")

    def test_no_declaration_is_stale(self):
        labels = {model._meta.label_lower for model in apps.get_models()}
        self.assertEqual(sorted(set(DECLARATIONS) - labels), [])

    def test_undeclared_models_fail_closed(self):
        self.assertIs(policy_for_label("nowhere.ghost"), DENIED)
        self.assertIs(policy_for(None), DENIED)
        self.assertFalse(DENIED.import_)
        self.assertFalse(DENIED.export)

    def test_import_declaration_and_curated_form_agree_exactly(self):
        for model in apps.get_models():
            label = model._meta.label_lower
            with self.subTest(label=label):
                declared = policy_for(model).import_
                registered = get_registered_import_form(model) is not None
                self.assertEqual(declared, registered, f"{label}: import_ and @register_import_form must match")
                self.assertEqual(is_model_importable(model), declared)

    def test_service_owned_and_security_sensitive_models_are_never_importable(self):
        for label in SERVICE_OWNED + SECURITY_SENSITIVE:
            with self.subTest(label=label):
                self.assertFalse(policy_for_label(label).import_)
                self.assertFalse(is_model_importable(apps.get_model(label)))

    def test_every_curated_form_names_real_fields_and_a_declared_update_key(self):
        for model in apps.get_models():
            form = get_registered_import_form(model)
            if form is None:
                continue
            with self.subTest(label=model._meta.label_lower):
                for name in form.update_key:
                    field = model._meta.get_field(name)
                    self.assertFalse(field.primary_key, "an update key must be a natural key, never the pk")
                    columns = list(form.required_fields) + list(form.optional_fields)
                self.assertIn(name, columns, "an update key must be an importable column")
