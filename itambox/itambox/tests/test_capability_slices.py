"""U1/U3/U6/U8: the shipped capability declarations and their consistency.

``test_capabilities.py`` proves the substrate. This module proves what the
domain actually declared through it: that ownership resolves, that the declared
grades match the tracked documentation, that an inactive or broken capability
is harmless, and that the deprecated app-level adapters still answer.
"""

import re
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from django.apps import apps
from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from model_bakery import baker

from core.features import BETA, STABLE, is_beta_module, module_maturity
from itambox.apps import _plugin_activation_probe
from itambox.capabilities import (
    ALWAYS_ON,
    EXPERIMENTAL,
    OPT_IN,
    SOURCE_ALWAYS,
    SOURCE_CONFIGURED,
    SOURCE_OBJECT_ENABLED,
    SOURCE_OPERATOR_FLAG,
    ActivationState,
    registry,
)
from itambox.tests.capability_harness import deactivatable_keys, deactivated, half_registered, probe_failing
from procurement import apps as procurement_apps

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_ROOT = REPO_ROOT / "itambox" / "docs"
MATURITY_DOC = DOCS_ROOT / "development" / "module-maturity.md"
REGISTRY_DOC = DOCS_ROOT / "development" / "capability-registry.md"
FALLBACK_DOC = DOCS_ROOT / "development" / "capability-fallbacks.md"
README = REPO_ROOT / "README.md"

# The slice this issue declares. Written out rather than derived so a silently
# dropped or reclassified registration fails here instead of passing vacuously.
DECLARED = {
    "subscriptions.tracking": (STABLE, ALWAYS_ON, SOURCE_ALWAYS),
    "procurement.core": (STABLE, ALWAYS_ON, SOURCE_ALWAYS),
    "procurement.requisition_seam": (BETA, "enabled", SOURCE_CONFIGURED),
    "reporting.curated": (STABLE, ALWAYS_ON, SOURCE_ALWAYS),
    "reporting.designer": (BETA, OPT_IN, SOURCE_OPERATOR_FLAG),
    "reporting.scheduled": (BETA, OPT_IN, SOURCE_OPERATOR_FLAG),
    "alerting.inbox": (STABLE, ALWAYS_ON, SOURCE_ALWAYS),
    "alerting.rules": (BETA, "enabled", SOURCE_OBJECT_ENABLED),
    "organization.role_grants": (STABLE, ALWAYS_ON, SOURCE_ALWAYS),
    "organization.resource_grants": (STABLE, ALWAYS_ON, SOURCE_ALWAYS),
    "automation.webhooks": (BETA, OPT_IN, SOURCE_OBJECT_ENABLED),
    "users.scim_provisioning": (BETA, OPT_IN, SOURCE_OBJECT_ENABLED),
    "platform.plugins": (EXPERIMENTAL, OPT_IN, SOURCE_OPERATOR_FLAG),
}


def area_labels():
    """The repository's ``area:*`` labels, read from the architecture policy."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from scripts.architecture_policy import AREA_LABELS

    return AREA_LABELS


class TestDeclaredSlice:
    def test_every_declared_capability_is_registered(self):
        assert set(registry.keys()) == set(DECLARED)

    @pytest.mark.parametrize("key", sorted(DECLARED))
    def test_the_declared_grade_mode_and_source_are_registered(self, key):
        capability = registry.get(key)
        expected = DECLARED[key]
        assert (capability.maturity, capability.activation, capability.activation_source) == expected

    def test_only_authorization_boundaries_are_security_critical(self):
        critical = {capability.key for capability in registry.all() if capability.security_critical}
        assert critical == {
            "organization.resource_grants",
            "organization.role_grants",
        }

    def test_every_entry_carries_the_current_contract_version(self):
        assert {capability.contract_version for capability in registry.all()} == {1}


class TestOwnership:
    """U1: ownership is total, exclusive, and resolvable."""

    def test_no_owned_reference_is_unresolved(self):
        unresolved = [(row.key, row.reference, row.reason) for row in registry.unresolved_references()]
        assert unresolved == []

    def test_ownership_is_exclusive(self):
        owners = {}
        for capability in registry.all():
            for reference in capability.owns:
                assert reference not in owners, f"{reference} owned by {owners.get(reference)} and {capability.key}"
                owners[reference] = capability.key

    @pytest.mark.parametrize(
        "reference,expected",
        [
            ("subscriptions.Subscription", "subscriptions.tracking"),
            ("procurement.PurchaseOrder", "procurement.core"),
            ("procurement.FulfillmentLink", "procurement.requisition_seam"),
            ("extras.ReportTemplate", "reporting.designer"),
            ("extras.ScheduledReport", "reporting.scheduled"),
            ("extras.AlertRule", "alerting.rules"),
            ("extras.AlertLog", "alerting.inbox"),
            ("extras.WebhookEndpoint", "automation.webhooks"),
            ("extras.EventRule", "automation.webhooks"),
            ("organization.RoleGrant", "organization.role_grants"),
            ("organization.TenantResourceGrant", "organization.resource_grants"),
        ],
    )
    def test_a_model_resolves_to_its_owning_capability(self, reference, expected):
        assert registry.owner_of(reference).key == expected

    def test_an_unowned_model_has_no_owner(self):
        assert registry.owner_of("assets.Asset") is None

    def test_every_owning_area_is_a_repository_area_label(self):
        labels = area_labels()
        for capability in registry.all():
            assert capability.owning_area in labels, f"{capability.key} names {capability.owning_area}"


class TestActivationDefaults:
    """U3: what a fresh deployment sees, and that nothing there is a surprise."""

    def test_every_stable_capability_is_active(self):
        for capability in registry.all():
            if capability.maturity == STABLE:
                assert registry.is_active(capability.key) is True, capability.key

    def test_every_opt_in_capability_is_inert_on_a_fresh_deployment(self, db):
        """``db``: the object-backed probes must *answer* here, not fail closed.

        Without database access a probe that counts rows raises and the registry
        reports it inactive, which is the same answer this test is looking for --
        so the assertion would pass without ever reaching an empty table. The
        ``probe_error`` check makes that difference visible.
        """
        for capability in registry.all():
            if capability.activation == OPT_IN:
                state = registry.state(capability.key)
                assert state.probe_error == "", capability.key
                assert state.active is False, capability.key

    def test_the_unconfigured_requisition_seam_is_inert(self):
        state = registry.state("procurement.requisition_seam")
        assert state == ActivationState(active=False, value_present=False)

    @override_settings(ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS={"accessory": 3, "consumable": 5})
    def test_the_configured_requisition_seam_reports_presence_without_values(self):
        state = registry.state("procurement.requisition_seam")
        assert state == ActivationState(active=True, value_present=True)
        assert "accessory" not in repr(state)
        assert "consumable" not in repr(state)

    @override_settings(ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS={})
    def test_an_empty_threshold_object_is_present_but_keeps_the_seam_inactive(self):
        state = registry.state("procurement.requisition_seam")

        assert state == ActivationState(active=False, value_present=True)

    @override_settings(
        ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS=None,
        REQUISITION_AUTO_APPROVAL_THRESHOLDS={"accessory": 2},
    )
    def test_the_legacy_threshold_setting_keeps_the_seam_active_for_1x(self):
        state = registry.state("procurement.requisition_seam")

        assert state == ActivationState(active=True, value_present=True)
        assert "accessory" not in repr(state)

    @override_settings(
        ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS=None,
        REQUISITION_AUTO_APPROVAL_THRESHOLDS={"accessory": 2},
    )
    def test_a_legacy_django_setting_emits_the_startup_deprecation_warning(self):
        warning_hook = getattr(procurement_apps, "_warn_legacy_auto_approval_setting", None)
        assert callable(warning_hook), "legacy Django-setting warning hook is missing"

        with pytest.warns(UserWarning, match="ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS"):
            warning_hook()

    @override_settings(PLUGINS=["demo_plugin"])
    def test_an_operator_flag_reports_a_configured_value_without_naming_it(self):
        state = registry.state("platform.plugins")
        assert (state.active, state.value_present) == (True, True)
        assert "demo_plugin" not in repr(state)

    @override_settings(
        PLUGINS=("broken_plugin",),
        PLUGINS_ACTIVE=(),
        PLUGINS_DIAGNOSTICS=({"plugin": "broken_plugin"},),
    )
    def test_a_failed_plugin_does_not_keep_the_platform_capability_active(self):
        assert _plugin_activation_probe() == ActivationState(active=False, value_present=True)


@pytest.mark.django_db
class TestSCIMActivationSource:
    """What actually switches SCIM provisioning on for a deployment.

    There is no SCIM settings key to read. Both SCIM mounts authenticate a
    ``Bearer`` API token scoped to the tenant named in the URL, so the token is
    the activation source and these tests pin the probe to it: present once an
    operator has minted the credential, active only while one of them could
    drive a provisioning write right now.
    """

    def test_a_deployment_with_no_token_reports_nothing_configured(self):
        assert registry.state("users.scim_provisioning") == ActivationState(active=False, value_present=False)

    def test_no_scim_settings_key_is_consulted(self):
        """The old probe read a setting that has never existed in this project."""
        assert not hasattr(settings, "TENANT_SCIM_CONFIGS")

    def test_a_write_enabled_tenant_token_activates_scim(self):
        baker.make("users.Token", write_enabled=True)
        state = registry.state("users.scim_provisioning")
        assert (state.active, state.value_present) == (True, True)

    def test_a_read_only_token_is_configured_but_cannot_provision(self):
        baker.make("users.Token", write_enabled=False)
        state = registry.state("users.scim_provisioning")
        assert (state.active, state.value_present) == (False, True)

    def test_an_expired_token_no_longer_activates_scim(self):
        baker.make("users.Token", write_enabled=True, expires=timezone.now() - timedelta(days=1))
        state = registry.state("users.scim_provisioning")
        assert (state.active, state.value_present) == (False, True)

    def test_a_token_held_by_a_deactivated_account_does_not_activate_scim(self):
        baker.make("users.Token", write_enabled=True, user__is_active=False)
        state = registry.state("users.scim_provisioning")
        assert (state.active, state.value_present) == (False, True)

    def test_a_token_in_a_soft_deleted_tenant_is_not_configuration_at_all(self):
        """The soft-delete boundary holds: a recycled workspace configures nothing."""
        baker.make("users.Token", write_enabled=True, tenant__deleted_at=timezone.now())
        assert registry.state("users.scim_provisioning") == ActivationState(active=False, value_present=False)

    def test_a_live_tenant_token_is_seen_from_outside_any_tenant_context(self):
        """No tenant is in scope during diagnostics, and the answer is still yes."""
        baker.make("users.Token", write_enabled=True)
        rows = {row["key"]: row for row in registry.diagnostics()}
        assert rows["users.scim_provisioning"]["active"] is True

    def test_the_probe_publishes_no_part_of_the_credential(self):
        token = baker.make("users.Token", write_enabled=True)
        row = next(row for row in registry.diagnostics() if row["key"] == "users.scim_provisioning")
        rendered = repr(registry.state("users.scim_provisioning")) + repr(row)
        assert token.key_preview and token.key_preview not in rendered
        assert token.digest not in rendered


class TestRegistrationIdempotence:
    """``ready()`` runs twice whenever a test swaps ``INSTALLED_APPS``."""

    @pytest.mark.parametrize(
        "app_label",
        ["extras", "itambox", "organization", "procurement", "subscriptions", "users"],
    )
    def test_re_running_ready_does_not_raise_or_duplicate(self, app_label):
        before = registry.keys()
        app_config = apps.get_app_config(app_label)
        app_config._register_capabilities()
        app_config.ready()
        assert registry.keys() == before

    @pytest.mark.parametrize("app_label", ["extras", "procurement"])
    def test_a_declaration_that_failed_halfway_is_completed_by_the_next_run(self, app_label):
        """The multipart apps must be *finishable*, not merely repeatable.

        A guard that returns as soon as the first key is present cannot tell a
        completed declaration from one that died after entry one, so it freezes
        the registry in the half-registered state instead of repairing it.
        """
        app_config = apps.get_app_config(app_label)
        with half_registered(app_label) as dropped:
            assert dropped, f"{app_label} declares only one capability"
            assert not set(dropped) & set(registry.keys())
            app_config._register_capabilities()
            assert set(dropped) <= set(registry.keys())


class TestExistingDeploymentCompatibility:
    """An opt-in slice is inert on a fresh install and live on a used one."""

    def test_an_object_enabled_capability_is_inactive_with_no_rows(self, db):
        assert registry.state("automation.webhooks") == ActivationState(active=False, value_present=False)

    def test_an_existing_enabled_row_keeps_the_capability_active(self, db):
        baker.make("extras.EventRule", enabled=True)
        state = registry.state("automation.webhooks")
        assert (state.active, state.value_present) == (True, True)

    def test_rows_that_are_all_switched_off_read_as_configured_but_inactive(self, db):
        baker.make("extras.EventRule", enabled=False)
        state = registry.state("automation.webhooks")
        assert (state.active, state.value_present) == (False, True)

    def test_a_soft_deleted_row_is_not_configuration_at_all(self, db):
        """The recycle bin is not a deployment state: a deleted rule configures nothing."""
        rule = baker.make("extras.EventRule", enabled=True)
        rule.soft_delete()
        assert registry.state("automation.webhooks") == ActivationState(active=False, value_present=False)

    def test_a_live_row_still_counts_beside_a_soft_deleted_one(self, db):
        baker.make("extras.EventRule", enabled=True).soft_delete()
        baker.make("extras.EventRule", enabled=True)
        state = registry.state("automation.webhooks")
        assert (state.active, state.value_present) == (True, True)

    @override_settings(REPORT_DESIGNER_ENABLED=False)
    def test_scheduled_reports_are_inactive_when_the_designer_flag_is_off(self, db):
        template = baker.make("extras.ReportTemplate")
        baker.make("extras.ScheduledReport", report=template, is_active=True)

        state = registry.state("reporting.scheduled")

        assert (state.active, state.value_present) == (False, True)

    @override_settings(REPORT_DESIGNER_ENABLED=True)
    def test_scheduled_reports_report_the_flag_but_wait_for_an_enabled_row(self, db):
        state = registry.state("reporting.scheduled")

        assert (state.active, state.value_present) == (False, True)

    @override_settings(REPORT_DESIGNER_ENABLED=True)
    def test_scheduled_reports_are_active_when_the_flag_and_a_row_are_enabled(self, db):
        template = baker.make("extras.ReportTemplate")
        baker.make("extras.ScheduledReport", report=template, is_active=True)

        state = registry.state("reporting.scheduled")

        assert (state.active, state.value_present) == (True, True)


class TestInactiveSafety:
    """U3/U6: an inactive or broken capability never becomes an exception."""

    @pytest.mark.parametrize("key", deactivatable_keys())
    def test_a_deactivated_capability_reports_inactive_and_stays_registered(self, key):
        with deactivated(key):
            assert registry.is_active(key) is False
            assert registry.get(key).maturity == DECLARED[key][0]
            assert key in registry
        assert registry.is_active(key) == registry.state(key).active

    @pytest.mark.parametrize("key", deactivatable_keys())
    def test_a_failing_probe_fails_closed_without_leaking_its_message(self, key):
        with probe_failing(key):
            state = registry.state(key)
            assert state.active is False
            assert state.value_present is False
            assert state.probe_error == "RuntimeError"
            assert "hunter2" not in repr(state)

    @pytest.mark.parametrize("key", deactivatable_keys())
    def test_diagnostics_stay_complete_while_a_probe_is_failing(self, key, db):
        with probe_failing(key):
            rows = {row["key"]: row for row in registry.diagnostics()}
        assert set(rows) == set(DECLARED)
        assert rows[key]["probe_error"] == "RuntimeError"
        assert "hunter2" not in repr(rows[key])
        # ``db``: one broken probe must not make the rest of the table look
        # broken. Without database access every row-counting probe would error
        # too and this isolation would go unproven.
        assert [other for other, row in rows.items() if other != key and row["probe_error"]] == []

    @pytest.mark.parametrize(
        "key",
        ["organization.resource_grants", "organization.role_grants"],
    )
    def test_a_security_critical_capability_has_no_deactivation_path(self, key):
        assert key not in deactivatable_keys()
        assert registry.is_active(key) is True


class TestDeprecatedAdapters:
    """The one-release adapters answer from the registry, not from a literal map."""

    def test_module_maturity_is_registry_backed(self):
        assert not hasattr(sys.modules["core.features"], "MODULE_MATURITY")

    @pytest.mark.parametrize("app_label", ["assets", "extras", "users", "organization", "procurement"])
    def test_a_partly_owned_app_is_not_graded_wholesale(self, app_label):
        assert module_maturity(app_label) == STABLE
        assert is_beta_module(app_label) is False

    def test_an_unknown_app_label_is_stable(self):
        assert module_maturity("no_such_app") == STABLE
        assert is_beta_module("no_such_app") is False

    def test_is_beta_module_reports_every_non_stable_grade(self):
        assert is_beta_module("assets") is False


class TestDocumentationConsistency:
    """U8: the tracked docs and README agree with the registry."""

    def test_every_docs_url_points_at_a_tracked_document(self):
        for capability in registry.all():
            target = DOCS_ROOT / capability.docs_url
            assert target.is_file(), f"{capability.key} documents itself at a missing {capability.docs_url}"

    def test_the_registry_document_lists_every_capability(self):
        text = REGISTRY_DOC.read_text(encoding="utf-8")
        for capability in registry.all():
            assert f"`{capability.key}`" in text, f"{capability.key} is undocumented"

    def test_optional_fallback_matrix_is_published_with_security_boundaries(self):
        text = FALLBACK_DOC.read_text(encoding="utf-8")
        for marker in (
            "unresolved_references()",
            "CapabilityRegistry.state()",
            "PluginConfig.ready()",
            "validate_file_attachment()",
            "validate_image_attachment()",
            "authentication, authorization, tenant isolation",
            "Content-Type",
            "SCIM",
        ):
            assert marker in text, f"fallback contract is missing {marker!r}"

    def test_the_registry_document_records_the_declared_contract(self):
        rows = _documented_contracts(REGISTRY_DOC)
        assert rows == {
            capability.key: (capability.maturity, capability.activation, capability.activation_source)
            for capability in registry.all()
        }

    def test_the_registry_document_explains_the_enforced_designer_flag(self):
        text = REGISTRY_DOC.read_text(encoding="utf-8")
        installation = (DOCS_ROOT / "operations" / "installation.md").read_text(encoding="utf-8")
        readme = README.read_text(encoding="utf-8")
        assert "ITAMBOX_FEATURE_REPORT_DESIGNER" in text
        assert "ITAMBOX_FEATURE_REPORT_DESIGNER" in installation
        assert "ITAMBOX_FEATURE_REPORT_DESIGNER" in readme
        phrase = "scheduled delivery of existing report templates"
        assert phrase in " ".join(text.lower().split())
        assert phrase in " ".join(installation.lower().split())
        assert phrase in " ".join(readme.lower().split())
        assert "registry.register_all" in text
        assert "returns early when its first key" not in text

    def test_the_maturity_document_points_at_the_registry(self):
        text = MATURITY_DOC.read_text(encoding="utf-8")
        assert "capability-registry.md" in text
        assert "MODULE_MATURITY" not in text

    def test_the_readme_links_the_registry_document(self):
        text = README.read_text(encoding="utf-8")
        assert "itambox/docs/development/capability-registry.md" in text

    def test_the_readme_does_not_grade_a_promoted_module_beta(self):
        text = README.read_text(encoding="utf-8")
        assert "Subscriptions and procurement — Beta" not in text

    def test_every_non_stable_capability_declares_at_least_one_limitation(self):
        for capability in registry.all():
            if capability.maturity != STABLE:
                assert capability.limitations, f"{capability.key} declares no limitation"


def _documented_contracts(path):
    """Parse grade, activation mode, and source from registry document rows."""
    contracts = {}
    pattern = re.compile(r"^\|\s*`([a-z0-9_.]+)`\s*\|.*$")
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if not match:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        contracts[match.group(1)] = tuple(cell.lower() for cell in cells[2:5])
    return contracts
