"""Characterization of the global Django form patches installed by ``CoreConfig.ready()``.

Issue #584 (WP0). These tests pin the CURRENT behavior of the three runtime
patches so the explicit replacements can be proven parity-neutral before any
form is migrated and before the patches are deleted:

* patch 1  - ``ModelChoiceField.queryset`` re-applies ``filter_by_tenant()`` on
  every read (render-time choices and bound validation);
* patch 2a - ``BaseForm.__init__`` marks a ``tenant`` field required when any
  ``Tenant`` row exists, except for ``tenant_group`` forms and class names
  containing ``Filter`` / ``BulkEdit``;
* patch 2b - ``BaseForm.__init__`` adds ``data-tom-select`` to select widgets,
  except radio/checkbox widgets, listboxes (``size``), column pickers and
  ``*TableConfig*`` classes.

The forms below are plain module-level forms whose querysets are frozen at
import time (no tenant context), which is exactly the hazard patch 1 covers.
Nothing here imports the patch mechanics; every assertion is on observable
form behavior so the same suite must stay green with the patches removed.
"""

from django import forms
from django.contrib.auth import get_user_model
from django.test import TestCase

from core.managers import (
    set_current_all_accessible,
    set_current_tenant,
    set_current_tenant_group,
)
from core.tests.mixins import grant
from itambox.middleware import _current_user
from organization.models import Location, Role, Site, Tenant, TenantGroup

User = get_user_model()


class _LocationPickerForm(forms.Form):
    location = forms.ModelChoiceField(queryset=Location.objects.all(), required=False)


class _LocationMultiPickerForm(forms.Form):
    locations = forms.ModelMultipleChoiceField(queryset=Location.objects.all(), required=False)


class _ScopedChoicesBase(TestCase):
    """Two tenants in one group, one foreign tenant, a location in each."""

    def setUp(self):
        self.group = TenantGroup.objects.create(name="Group A", slug="grp-a")
        self.a1 = Tenant.objects.create(name="A1", slug="t-a1", group=self.group)
        self.a2 = Tenant.objects.create(name="A2", slug="t-a2", group=self.group)
        self.other = Tenant.objects.create(name="Other", slug="t-other")
        self.site = Site.objects.create(name="HQ", slug="hq")
        self.loc_a1 = Location.objects.create(name="Loc A1", slug="loc-a1", site=self.site, tenant=self.a1)
        self.loc_a2 = Location.objects.create(name="Loc A2", slug="loc-a2", site=self.site, tenant=self.a2)
        self.loc_other = Location.objects.create(name="Loc Other", slug="loc-other", site=self.site, tenant=self.other)

        self.member = User.objects.create_user(username="member", password="pw")
        self.superuser = User.objects.create_superuser(username="root", email="r@x.com", password="pw")
        for tenant in (self.a1, self.a2):
            grant(self.member, tenant, Role.objects.create(tenant=tenant, name="R", permissions=[]))

    def _choice_pks(self, form_class, field="location", data=None):
        form = form_class(data=data) if data is not None else form_class()
        return set(form.fields[field].queryset.values_list("pk", flat=True))

    def _bound_is_valid(self, pk):
        return _LocationPickerForm(data={"location": pk}).is_valid()


class ModelChoiceFieldQuerysetScopingTests(_ScopedChoicesBase):
    """Patch 1: scope matrix x {choice reads, bound validation}."""

    def test_no_context_reads_are_unscoped(self):
        _current_user.set(None)
        self.assertEqual(
            self._choice_pks(_LocationPickerForm),
            {self.loc_a1.pk, self.loc_a2.pk, self.loc_other.pk},
        )
        self.assertTrue(self._bound_is_valid(self.loc_other.pk))

    def test_single_tenant_member_reads_only_the_active_tenant(self):
        _current_user.set(self.member)
        set_current_tenant(self.a1)
        self.assertEqual(self._choice_pks(_LocationPickerForm), {self.loc_a1.pk})

    def test_single_tenant_member_validation_rejects_foreign_ids(self):
        _current_user.set(self.member)
        set_current_tenant(self.a1)
        self.assertTrue(self._bound_is_valid(self.loc_a1.pk))
        self.assertFalse(self._bound_is_valid(self.loc_a2.pk))
        self.assertFalse(self._bound_is_valid(self.loc_other.pk))

    def test_tenant_group_member_reads_the_group_tenants_only(self):
        _current_user.set(self.member)
        set_current_tenant(None)
        set_current_tenant_group(self.group)
        self.assertEqual(self._choice_pks(_LocationPickerForm), {self.loc_a1.pk, self.loc_a2.pk})

    def test_tenant_group_member_validation(self):
        _current_user.set(self.member)
        set_current_tenant(None)
        set_current_tenant_group(self.group)
        self.assertTrue(self._bound_is_valid(self.loc_a1.pk))
        self.assertTrue(self._bound_is_valid(self.loc_a2.pk))
        self.assertFalse(self._bound_is_valid(self.loc_other.pk))

    def test_all_accessible_member_reads_every_accessible_tenant(self):
        _current_user.set(self.member)
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_all_accessible(True)
        self.assertEqual(self._choice_pks(_LocationPickerForm), {self.loc_a1.pk, self.loc_a2.pk})

    def test_all_accessible_member_validation(self):
        _current_user.set(self.member)
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_all_accessible(True)
        self.assertTrue(self._bound_is_valid(self.loc_a2.pk))
        self.assertFalse(self._bound_is_valid(self.loc_other.pk))

    def test_superuser_with_active_tenant_is_scoped_to_it(self):
        _current_user.set(self.superuser)
        set_current_tenant(self.a1)
        self.assertEqual(self._choice_pks(_LocationPickerForm), {self.loc_a1.pk})
        self.assertFalse(self._bound_is_valid(self.loc_other.pk))

    def test_superuser_without_scope_reads_everything(self):
        _current_user.set(self.superuser)
        self.assertEqual(
            self._choice_pks(_LocationPickerForm),
            {self.loc_a1.pk, self.loc_a2.pk, self.loc_other.pk},
        )

    def test_scope_narrowed_at_construction_intersects_with_later_scope(self):
        """Form construction deep-copies the field through the patched getter.

        The instance's stored queryset is already narrowed to the construction
        scope, and every later read intersects it with the then-ambient scope:
        switching tenants yields an empty choice set, while a form built under
        the new scope sees that scope's rows.
        """
        _current_user.set(self.member)
        set_current_tenant(self.a1)
        form = _LocationPickerForm()
        set_current_tenant(self.a2)
        self.assertEqual(set(form.fields["location"].queryset.values_list("pk", flat=True)), set())
        self.assertEqual(self._choice_pks(_LocationPickerForm), {self.loc_a2.pk})

    def test_multiple_choice_field_is_scoped_too(self):
        _current_user.set(self.member)
        set_current_tenant(self.a1)
        self.assertEqual(self._choice_pks(_LocationMultiPickerForm, field="locations"), {self.loc_a1.pk})
        ok = _LocationMultiPickerForm(data={"locations": [self.loc_a1.pk]})
        self.assertTrue(ok.is_valid())
        foreign = _LocationMultiPickerForm(data={"locations": [self.loc_a1.pk, self.loc_other.pk]})
        self.assertFalse(foreign.is_valid())
        self.assertIn("locations", foreign.errors)

    def test_queryset_without_tenant_support_is_left_alone(self):
        """A queryset type without ``filter_by_tenant`` is returned as given."""
        _current_user.set(self.member)
        set_current_tenant(self.a1)
        extra_user = User.objects.create_user(username="extra", password="pw")

        class _UserPickerForm(forms.Form):
            user = forms.ModelChoiceField(queryset=User.objects.all(), required=False)

        pks = set(_UserPickerForm().fields["user"].queryset.values_list("pk", flat=True))
        self.assertIn(extra_user.pk, pks)
        self.assertIn(self.superuser.pk, pks)


class TenantRequiredRuleTests(TestCase):
    """Patch 2a: the global ``tenant`` requiredness rule and its exclusions."""

    @staticmethod
    def _form_class(name, with_group=False):
        attrs = {"tenant": forms.ModelChoiceField(queryset=Tenant.objects.all(), required=False)}
        if with_group:
            attrs["tenant_group"] = forms.ModelChoiceField(queryset=TenantGroup.objects.all(), required=False)
        return type(name, (forms.Form,), attrs)

    def test_no_tenant_row_keeps_the_field_optional(self):
        self.assertFalse(Tenant.objects.exists())
        self.assertFalse(self._form_class("AssetThingForm")().fields["tenant"].required)

    def test_any_tenant_row_makes_the_field_required(self):
        Tenant.objects.create(name="A1", slug="t-a1")
        self.assertTrue(self._form_class("AssetThingForm")().fields["tenant"].required)

    def test_filter_class_name_is_excluded(self):
        Tenant.objects.create(name="A1", slug="t-a1")
        self.assertFalse(self._form_class("AssetThingFilterForm")().fields["tenant"].required)

    def test_bulk_edit_class_name_is_excluded(self):
        Tenant.objects.create(name="A1", slug="t-a1")
        self.assertFalse(self._form_class("AssetThingBulkEditForm")().fields["tenant"].required)

    def test_tenant_group_sibling_field_is_excluded(self):
        Tenant.objects.create(name="A1", slug="t-a1")
        self.assertFalse(self._form_class("SupplierThingForm", with_group=True)().fields["tenant"].required)

    def test_form_without_tenant_field_is_untouched(self):
        Tenant.objects.create(name="A1", slug="t-a1")
        form = type("PlainForm", (forms.Form,), {"name": forms.CharField(required=False)})()
        self.assertNotIn("tenant", form.fields)
        self.assertFalse(form.fields["name"].required)

    def test_bound_empty_tenant_is_rejected_once_a_tenant_exists(self):
        Tenant.objects.create(name="A1", slug="t-a1")
        form = self._form_class("AssetThingForm")(data={})
        self.assertFalse(form.is_valid())
        self.assertIn("tenant", form.errors)

    def test_bound_empty_tenant_is_accepted_for_excluded_forms(self):
        Tenant.objects.create(name="A1", slug="t-a1")
        self.assertTrue(self._form_class("AssetThingFilterForm")(data={}).is_valid())
        self.assertTrue(self._form_class("SupplierThingForm", with_group=True)(data={}).is_valid())


class TomSelectInjectionTests(TestCase):
    """Patch 2b: ``data-tom-select`` injection and every exclusion."""

    @staticmethod
    def _form(name="ThingForm", **fields):
        return type(name, (forms.Form,), fields)()

    def test_select_widget_gets_the_attribute(self):
        form = self._form(a=forms.ChoiceField(choices=[("1", "One")]))
        self.assertEqual(form.fields["a"].widget.attrs["data-tom-select"], "")

    def test_select_multiple_widget_gets_the_attribute(self):
        form = self._form(a=forms.MultipleChoiceField(choices=[("1", "One")]))
        self.assertEqual(form.fields["a"].widget.attrs["data-tom-select"], "")

    def test_model_choice_field_gets_the_attribute(self):
        form = self._form(a=forms.ModelChoiceField(queryset=Tenant.objects.all()))
        self.assertIn("data-tom-select", form.fields["a"].widget.attrs)

    def test_existing_attribute_value_is_preserved(self):
        widget = forms.Select(attrs={"data-tom-select": "custom"})
        form = self._form(a=forms.ChoiceField(choices=[("1", "One")], widget=widget))
        self.assertEqual(form.fields["a"].widget.attrs["data-tom-select"], "custom")

    def test_radio_select_is_excluded(self):
        form = self._form(a=forms.ChoiceField(choices=[("1", "One")], widget=forms.RadioSelect))
        self.assertNotIn("data-tom-select", form.fields["a"].widget.attrs)

    def test_checkbox_select_multiple_is_excluded(self):
        form = self._form(
            a=forms.MultipleChoiceField(choices=[("1", "One")], widget=forms.CheckboxSelectMultiple),
        )
        self.assertNotIn("data-tom-select", form.fields["a"].widget.attrs)

    def test_listbox_with_size_attribute_is_excluded(self):
        widget = forms.SelectMultiple(attrs={"size": "8"})
        form = self._form(a=forms.MultipleChoiceField(choices=[("1", "One")], widget=widget))
        self.assertNotIn("data-tom-select", form.fields["a"].widget.attrs)

    def test_column_picker_classes_are_excluded(self):
        for css in ("available-columns", "selected-columns", "form-select available-columns"):
            widget = forms.SelectMultiple(attrs={"class": css})
            form = self._form(a=forms.MultipleChoiceField(choices=[("1", "One")], widget=widget))
            self.assertNotIn("data-tom-select", form.fields["a"].widget.attrs, css)

    def test_table_config_class_name_is_excluded(self):
        form = self._form(name="AssetTableConfigForm", a=forms.ChoiceField(choices=[("1", "One")]))
        self.assertNotIn("data-tom-select", form.fields["a"].widget.attrs)

    def test_non_select_widgets_are_untouched(self):
        form = self._form(a=forms.CharField(), b=forms.BooleanField(), c=forms.DateField())
        for name in ("a", "b", "c"):
            self.assertNotIn("data-tom-select", form.fields[name].widget.attrs)
