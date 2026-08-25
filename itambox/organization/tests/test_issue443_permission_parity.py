"""Current-base characterization and #443 policy parity tests."""

import pytest
from django.apps import apps
from django.contrib.auth.models import Permission

from core.auth.ldap import MultiTenantLDAPBackend
from core.auth.oidc import TenantOIDCBackend
from core.auth.saml import TenantSaml2Backend
from organization.forms import MATRIX_MODELS as PACKAGE_MATRIX_MODELS
from organization.forms.role_form import MATRIX_MODELS as ROLE_FORM_MATRIX_MODELS

EXPECTED_TARGETS = (
    ("asset", "assets", "asset"),
    ("assetrequest", "assets", "assetrequest"),
    ("purchaseorder", "procurement", "purchaseorder"),
    ("auditsession", "compliance", "auditsession"),
    ("assetaudit", "compliance", "assetaudit"),
    ("accessory", "inventory", "accessory"),
    ("consumable", "inventory", "consumable"),
    ("kit", "inventory", "kit"),
    ("component", "inventory", "component"),
    ("license", "licenses", "license"),
    ("software", "software", "software"),
    ("subscription", "subscriptions", "subscription"),
    ("subscriptionassignment", "subscriptions", "subscriptionassignment"),
    ("location", "organization", "location"),
    ("site", "organization", "site"),
    ("assetholder", "organization", "assetholder"),
    ("role", "organization", "role"),
    ("membership", "organization", "membership"),
    ("rolegrant", "organization", "rolegrant"),
    ("tenantresourcegrant", "organization", "tenantresourcegrant"),
    ("region", "organization", "region"),
    ("sitegroup", "organization", "sitegroup"),
    ("tenantgroup", "organization", "tenantgroup"),
    ("contact", "organization", "contact"),
    ("contactrole", "organization", "contactrole"),
    ("manufacturer", "assets", "manufacturer"),
    ("supplier", "assets", "supplier"),
    ("provider_sub", "subscriptions", "provider"),
    ("statuslabel", "assets", "statuslabel"),
    ("category", "assets", "category"),
    ("depreciation", "assets", "depreciation"),
    ("assettype", "assets", "assettype"),
    ("customfield", "extras", "customfield"),
    ("tag", "extras", "tag"),
    ("reporttemplate", "extras", "reporttemplate"),
    ("scheduledreport", "extras", "scheduledreport"),
    ("alertrule", "extras", "alertrule"),
    ("alertlog", "extras", "alertlog"),
    ("notificationchannel", "extras", "notificationchannel"),
    ("exporttemplate", "extras", "exporttemplate"),
    ("webhookendpoint", "extras", "webhookendpoint"),
    ("eventrule", "extras", "eventrule"),
    ("labeltemplate", "extras", "labeltemplate"),
    ("recyclebin", "core", "recyclebin"),
    ("custodytemplate", "compliance", "custodytemplate"),
    ("custodyreceipt", "compliance", "custodyreceipt"),
    ("assetmaintenance", "assets", "assetmaintenance"),
    ("user", "users", "user"),
    ("token", "users", "token"),
    ("usergroup", "users", "usergroup"),
    ("warranty", "assets", "warranty"),
    ("assetdisposal", "assets", "assetdisposal"),
    ("assetreservation", "assets", "assetreservation"),
    ("contract", "procurement", "contract"),
    ("installedsoftware", "software", "installedsoftware"),
    ("assetrole", "assets", "assetrole"),
    ("costcenter", "organization", "costcenter"),
    ("journalentry", "extras", "journalentry"),
    ("configcontext", "extras", "configcontext"),
    ("docusignenvelope", "itambox_esign", "docusignenvelope"),
)

DASHBOARD_PERMISSIONS = frozenset(
    {
        "extras.view_dashboard",
        "extras.add_dashboard",
        "extras.change_dashboard",
        "extras.delete_dashboard",
    }
)

OLD_BACKENDS = (MultiTenantLDAPBackend, TenantOIDCBackend, TenantSaml2Backend)


def _independent_live_permissions(role_name):
    actions = {
        "Admin": ("view", "add", "change", "delete"),
        "Manager": ("view", "add", "change"),
        "Member": ("view", "add", "change"),
    }[role_name]
    candidates = {
        f"{app}.{action}_{model}"
        for _key, app, model in EXPECTED_TARGETS
        if app != "itambox_esign" or apps.is_installed("itambox_esign")
        for action in actions
    }
    candidates.update(DASHBOARD_PERMISSIONS)
    live = set(Permission.objects.values_list("content_type__app_label", "codename"))
    live = {f"{app}.{codename}" for app, codename in live}
    return candidates.intersection(live)


@pytest.mark.django_db
def test_current_old_three_backend_methods_match_independent_expected_sets():
    for role_name, expected_count in (("Admin", 235), ("Manager", 177), ("Member", 177)):
        expected = _independent_live_permissions(role_name)
        outputs = {frozenset(object.__new__(backend).get_permissions_for_role(role_name)) for backend in OLD_BACKENDS}
        assert outputs == {frozenset(expected)}
        assert len(expected) == expected_count


@pytest.mark.django_db
def test_current_old_three_backend_methods_preserve_manager_member_dashboard_shape():
    manager = {frozenset(object.__new__(backend).get_permissions_for_role("Manager")) for backend in OLD_BACKENDS}
    member = {frozenset(object.__new__(backend).get_permissions_for_role("Member")) for backend in OLD_BACKENDS}
    assert manager == member
    assert len(manager) == 1
    assert next(iter(manager)).issuperset(DASHBOARD_PERMISSIONS)


def test_current_both_matrix_surfaces_are_identical_ordered_59_key_dicts():
    assert not apps.is_installed("itambox_esign")
    expected_keys = [key for key, app, _model in EXPECTED_TARGETS if app != "itambox_esign"]
    assert len(expected_keys) == 59
    assert list(ROLE_FORM_MATRIX_MODELS) == expected_keys
    assert list(PACKAGE_MATRIX_MODELS) == expected_keys
    assert ROLE_FORM_MATRIX_MODELS == PACKAGE_MATRIX_MODELS
    for value in ROLE_FORM_MATRIX_MODELS.values():
        assert tuple(value) == ("label", "app", "model_name", "group")


@pytest.mark.django_db
def test_new_semantic_policy_matches_all_three_old_methods_before_deletion():
    from organization.services.role_permission_policy import permissions_for_sso_role

    for role_name in ("Admin", "Manager", "Member"):
        expected = _independent_live_permissions(role_name)
        actual = set(permissions_for_sso_role(role_name))
        assert actual == expected
        for backend in OLD_BACKENDS:
            assert actual == set(object.__new__(backend).get_permissions_for_role(role_name))


@pytest.mark.django_db
def test_new_policy_has_exact_absent_branch_counts_and_dashboard_set():
    from organization.services.role_permission_policy import DASHBOARD_SSO_PERMISSIONS, permissions_for_sso_role

    assert not apps.is_installed("itambox_esign")
    action_map = {
        "Admin": ("view", "add", "change", "delete"),
        "Manager": ("view", "add", "change"),
        "Member": ("view", "add", "change"),
    }
    for role_name, expected_count in (("Admin", 235), ("Manager", 177), ("Member", 177)):
        actual = set(permissions_for_sso_role(role_name))
        crud = {
            f"{app}.{action}_{model}"
            for _key, app, model in EXPECTED_TARGETS
            if app != "itambox_esign"
            for action in action_map[role_name]
        }
        assert len(actual) == expected_count
        assert actual.difference(crud) == set(DASHBOARD_SSO_PERMISSIONS)
    assert set(permissions_for_sso_role("Manager")) == set(permissions_for_sso_role("Member"))


def test_semantic_and_presentation_declarations_have_exact_60_key_order():
    from organization.forms.role_matrix import ROLE_PERMISSION_PRESENTATION
    from organization.services.role_permission_policy import ROLE_PERMISSION_TARGETS

    expected = tuple(
        (key, app, model, "itambox_esign" if app == "itambox_esign" else None) for key, app, model in EXPECTED_TARGETS
    )
    actual = tuple((target.key, target.app, target.model, target.required_app) for target in ROLE_PERMISSION_TARGETS)
    assert len(ROLE_PERMISSION_TARGETS) == 60
    assert actual == expected
    assert list(ROLE_PERMISSION_PRESENTATION) == [key for key, _app, _model, _required in expected]
    assert len(ROLE_PERMISSION_PRESENTATION) == 60


def test_semantic_targets_are_presentation_free_and_policy_does_not_import_forms():
    import inspect
    from dataclasses import fields

    import organization.services.role_permission_policy as policy

    assert tuple(field.name for field in fields(policy.RolePermissionTarget)) == (
        "key",
        "app",
        "model",
        "required_app",
    )
    assert "organization.forms" not in inspect.getsource(policy)
    assert "gettext" not in inspect.getsource(policy)


@pytest.mark.django_db
def test_present_and_absent_optional_plugin_projection_and_effective_counts(monkeypatch):
    from django.contrib.contenttypes.models import ContentType

    import organization.forms.role_matrix as presentation
    import organization.services.role_permission_policy as policy

    content_type, _created = ContentType.objects.get_or_create(
        app_label="itambox_esign",
        model="docusignenvelope",
    )
    for codename in (
        "view_docusignenvelope",
        "add_docusignenvelope",
        "change_docusignenvelope",
        "delete_docusignenvelope",
    ):
        Permission.objects.get_or_create(
            content_type=content_type,
            codename=codename,
            defaults={"name": codename.replace("_", " ").title()},
        )

    monkeypatch.setattr(apps, "is_installed", lambda label: False)
    assert len(policy.active_role_permission_targets()) == 59
    assert len(presentation.build_matrix_models()) == 59
    assert [target.key for target in policy.active_role_permission_targets()][-1] == "configcontext"
    assert len(policy.permissions_for_sso_role("Admin")) == 235
    assert len(policy.permissions_for_sso_role("Manager")) == 177
    assert len(policy.permissions_for_sso_role("Member")) == 177

    monkeypatch.setattr(apps, "is_installed", lambda label: label == "itambox_esign")
    assert len(policy.active_role_permission_targets()) == 60
    present_matrix = presentation.build_matrix_models()
    assert len(present_matrix) == 60
    assert [target.key for target in policy.active_role_permission_targets()][-1] == "docusignenvelope"
    expected_present = []
    for key, label, group in EXPECTED_PRESENTATION_ROWS:
        target = next(target for target in EXPECTED_TARGETS if target[0] == key)
        expected_present.append((key, label, target[1], target[2], group))
    actual_present = [
        (key, str(info["label"]), info["app"], info["model_name"], str(info["group"]))
        for key, info in present_matrix.items()
    ]
    assert actual_present == expected_present
    assert len(policy.permissions_for_sso_role("Admin")) == 239
    assert len(policy.permissions_for_sso_role("Manager")) == 180
    assert len(policy.permissions_for_sso_role("Member")) == 180


@pytest.mark.django_db
def test_active_matrix_keeps_both_legacy_surfaces_exactly_compatible():
    from organization.forms.role_matrix import MATRIX_MODELS as PRESENTATION_MATRIX_MODELS

    assert list(ROLE_FORM_MATRIX_MODELS) == list(PACKAGE_MATRIX_MODELS) == list(PRESENTATION_MATRIX_MODELS)
    assert ROLE_FORM_MATRIX_MODELS == PACKAGE_MATRIX_MODELS == PRESENTATION_MATRIX_MODELS
    for key, info in ROLE_FORM_MATRIX_MODELS.items():
        assert list(info) == ["label", "app", "model_name", "group"]
        expected = {
            "label": info["label"],
            "app": info["app"],
            "model_name": info["model_name"],
            "group": info["group"],
        }
        assert info == expected
        assert key in [target[0] for target in EXPECTED_TARGETS]
        assert key != "docusignenvelope"


@pytest.mark.parametrize("mutation,expected_fragment", (("missing", "missing="), ("extra", "extra=")))
def test_presentation_validation_is_loud_for_missing_and_extra_keys(monkeypatch, mutation, expected_fragment):
    import organization.forms.role_matrix as presentation

    declared = dict(presentation.ROLE_PERMISSION_PRESENTATION)
    if mutation == "missing":
        declared.pop(next(iter(declared)))
    else:
        declared["future_only"] = {
            "label": "Future only",
            "app": "extras",
            "model_name": "futureonly",
            "group": "Future",
        }
    monkeypatch.setattr(presentation, "ROLE_PERMISSION_PRESENTATION", declared)

    with pytest.raises(RuntimeError, match=expected_fragment):
        presentation.build_matrix_models()


def test_future_semantic_target_requires_a_presentation_entry(monkeypatch):
    import organization.forms.role_matrix as presentation
    from organization.services.role_permission_policy import ROLE_PERMISSION_TARGETS, RolePermissionTarget

    future_target = RolePermissionTarget("futureonly", "extras", "futureonly")
    monkeypatch.setattr(presentation, "ROLE_PERMISSION_TARGETS", ROLE_PERMISSION_TARGETS + (future_target,))

    with pytest.raises(RuntimeError, match="missing=.*futureonly"):
        presentation.build_matrix_models()


def test_role_name_contract_is_local_and_does_not_import_missing_port():
    import inspect
    from typing import get_args

    import organization.services.role_permission_policy as policy

    assert get_args(policy.CustomerRoleName) == ("Admin", "Manager", "Member")
    assert "core.identity_provisioning" not in inspect.getsource(policy)


EXPECTED_PRESENTATION_ROWS = (
    ("asset", "Assets", "Inventory " + chr(38) + " Hardware"),
    ("assetrequest", "Asset Requests", "Inventory " + chr(38) + " Hardware"),
    ("purchaseorder", "Purchase Orders", "Inventory " + chr(38) + " Hardware"),
    ("auditsession", "Audit Sessions", "Compliance " + chr(38) + " Custody"),
    ("assetaudit", "Asset Audits", "Compliance " + chr(38) + " Custody"),
    ("accessory", "Accessories", "Inventory " + chr(38) + " Hardware"),
    ("consumable", "Consumables", "Inventory " + chr(38) + " Hardware"),
    ("kit", "Kits", "Inventory " + chr(38) + " Hardware"),
    ("component", "Components", "Inventory " + chr(38) + " Hardware"),
    ("license", "Licenses", "Software " + chr(38) + " Subscriptions"),
    ("software", "Software", "Software " + chr(38) + " Subscriptions"),
    ("subscription", "Subscriptions", "Software " + chr(38) + " Subscriptions"),
    ("subscriptionassignment", "Subscription Assignments", "Software " + chr(38) + " Subscriptions"),
    ("location", "Locations", "Organization " + chr(38) + " Structure"),
    ("site", "Sites", "Organization " + chr(38) + " Structure"),
    ("assetholder", "Asset Holders", "Organization " + chr(38) + " Structure"),
    ("role", "Roles " + chr(38) + " Permissions", "Organization " + chr(38) + " Structure"),
    ("membership", "Memberships", "Organization " + chr(38) + " Structure"),
    ("rolegrant", "Role Grants", "Organization " + chr(38) + " Structure"),
    ("tenantresourcegrant", "Resource Grants", "Organization " + chr(38) + " Structure"),
    ("region", "Regions", "Organization " + chr(38) + " Structure"),
    ("sitegroup", "Site Groups", "Organization " + chr(38) + " Structure"),
    ("tenantgroup", "Tenant Groups", "Organization " + chr(38) + " Structure"),
    ("contact", "Contacts", "Organization " + chr(38) + " Structure"),
    ("contactrole", "Contact Roles", "Organization " + chr(38) + " Structure"),
    ("manufacturer", "Manufacturers", "Metadata " + chr(38) + " Settings"),
    ("supplier", "Suppliers (Hardware)", "Metadata " + chr(38) + " Settings"),
    ("provider_sub", "Providers (Subscription)", "Metadata " + chr(38) + " Settings"),
    ("statuslabel", "Status Labels", "Metadata " + chr(38) + " Settings"),
    ("category", "Categories", "Metadata " + chr(38) + " Settings"),
    ("depreciation", "Depreciation Schedules", "Metadata " + chr(38) + " Settings"),
    ("assettype", "Asset Types", "Metadata " + chr(38) + " Settings"),
    ("customfield", "Custom Fields", "Metadata " + chr(38) + " Settings"),
    ("tag", "Tags", "Metadata " + chr(38) + " Settings"),
    ("reporttemplate", "Report Templates", "System " + chr(38) + " Reporting"),
    ("scheduledreport", "Scheduled Reports", "System " + chr(38) + " Reporting"),
    ("alertrule", "Alert Rules", "System " + chr(38) + " Reporting"),
    ("alertlog", "Alert Logs", "System " + chr(38) + " Reporting"),
    ("notificationchannel", "Notification Channels", "System " + chr(38) + " Reporting"),
    ("exporttemplate", "Export Templates", "System " + chr(38) + " Reporting"),
    ("webhookendpoint", "Webhook Endpoints", "System " + chr(38) + " Reporting"),
    ("eventrule", "Event Rules", "System " + chr(38) + " Reporting"),
    ("labeltemplate", "Label Templates", "System " + chr(38) + " Reporting"),
    ("recyclebin", "Recycle Bin", "System " + chr(38) + " Reporting"),
    ("custodytemplate", "Custody Templates", "Compliance " + chr(38) + " Custody"),
    ("custodyreceipt", "Custody Receipts", "Compliance " + chr(38) + " Custody"),
    ("assetmaintenance", "Asset Maintenances", "Inventory " + chr(38) + " Hardware"),
    ("user", "Users", "User Management"),
    ("token", "API Tokens", "User Management"),
    ("usergroup", "User Groups", "User Management"),
    ("warranty", "Warranties", "Inventory " + chr(38) + " Hardware"),
    ("assetdisposal", "Asset Disposals", "Inventory " + chr(38) + " Hardware"),
    ("assetreservation", "Asset Reservations", "Inventory " + chr(38) + " Hardware"),
    ("contract", "Contracts", "Inventory " + chr(38) + " Hardware"),
    ("installedsoftware", "Installed Software", "Software " + chr(38) + " Subscriptions"),
    ("assetrole", "Asset Roles", "Metadata " + chr(38) + " Settings"),
    ("costcenter", "Cost Centers", "Organization " + chr(38) + " Structure"),
    ("journalentry", "Journal Entries", "System " + chr(38) + " Reporting"),
    ("configcontext", "Config Contexts", "System " + chr(38) + " Reporting"),
    ("docusignenvelope", "DocuSign Envelopes", "Plugins"),
)


def test_presentation_values_match_the_established_60_row_contract():
    expected = []
    for key, label, group in EXPECTED_PRESENTATION_ROWS:
        target = next(target for target in EXPECTED_TARGETS if target[0] == key)
        expected.append((key, label, target[1], target[2], group))

    actual = [
        (key, str(info["label"]), info["app"], info["model_name"], str(info["group"]))
        for key, info in PACKAGE_MATRIX_MODELS.items()
    ]
    assert len(EXPECTED_PRESENTATION_ROWS) == 60
    assert actual == expected[:-1]
