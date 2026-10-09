"""Unit tests for the explicit form scoping abstractions (#584, WP2).

These cover ``core.forms.scoping`` and ``core.forms.base``. The abstractions are
unused by domain forms and the global patches stay installed, so every assertion
is on observable behaviour and mirrors the WP0 characterization suite
(``test_form_patch_characterization.py``), which remains the parity contract.
"""

from unittest import mock

import django_filters
from django import forms
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.db import OperationalError, ProgrammingError
from django.test import RequestFactory, TestCase

from core.forms import (
    TenantScopedAdminFormMixin,
    TenantScopedFilterSetMixin,
    TenantScopedFormMixin,
    TenantScopedModelChoiceField,
    TenantScopedModelMultipleChoiceField,
    apply_tenant_scoped_choices,
    apply_tom_select,
    tenant_rows_exist,
)
from core.forms import base as forms_base
from core.forms.scoping import is_tenant_scoped_field, scope_field
from core.managers import (
    set_current_all_accessible,
    set_current_tenant,
    set_current_tenant_group,
)
from core.tests.mixins import grant
from itambox.middleware import _current_user
from organization.models import Location, Role, Site, Tenant, TenantGroup

User = get_user_model()


class _ScopedPickerForm(forms.Form):
    location = TenantScopedModelChoiceField(queryset=Location.objects.all(), required=False)


class _ScopedMultiPickerForm(forms.Form):
    locations = TenantScopedModelMultipleChoiceField(queryset=Location.objects.all(), required=False)


class _PlainPickerForm(forms.Form):
    location = forms.ModelChoiceField(queryset=Location.objects.all(), required=False)
    other = forms.ModelChoiceField(queryset=Location.objects.all(), required=False)


class _ScopeFixture(TestCase):
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

    @staticmethod
    def pks(form, field="location"):
        return set(form.fields[field].queryset.values_list("pk", flat=True))


class ScopedFieldClassTests(_ScopeFixture):
    """C1: reads follow the ambient scope; validation rejects foreign ids."""

    def test_no_context_reads_are_unscoped(self):
        _current_user.set(None)
        self.assertEqual(self.pks(_ScopedPickerForm()), {self.loc_a1.pk, self.loc_a2.pk, self.loc_other.pk})

    def test_single_tenant_member_reads_and_validates_against_the_active_tenant(self):
        _current_user.set(self.member)
        set_current_tenant(self.a1)
        self.assertEqual(self.pks(_ScopedPickerForm()), {self.loc_a1.pk})
        self.assertTrue(_ScopedPickerForm(data={"location": self.loc_a1.pk}).is_valid())
        self.assertFalse(_ScopedPickerForm(data={"location": self.loc_a2.pk}).is_valid())
        self.assertFalse(_ScopedPickerForm(data={"location": self.loc_other.pk}).is_valid())

    def test_tenant_group_scope(self):
        _current_user.set(self.member)
        set_current_tenant(None)
        set_current_tenant_group(self.group)
        self.assertEqual(self.pks(_ScopedPickerForm()), {self.loc_a1.pk, self.loc_a2.pk})
        self.assertTrue(_ScopedPickerForm(data={"location": self.loc_a2.pk}).is_valid())
        self.assertFalse(_ScopedPickerForm(data={"location": self.loc_other.pk}).is_valid())

    def test_all_accessible_scope(self):
        _current_user.set(self.member)
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_all_accessible(True)
        self.assertEqual(self.pks(_ScopedPickerForm()), {self.loc_a1.pk, self.loc_a2.pk})
        self.assertFalse(_ScopedPickerForm(data={"location": self.loc_other.pk}).is_valid())

    def test_superuser_without_scope_reads_everything(self):
        _current_user.set(self.superuser)
        self.assertEqual(self.pks(_ScopedPickerForm()), {self.loc_a1.pk, self.loc_a2.pk, self.loc_other.pk})

    def test_scope_narrowed_at_construction_intersects_with_later_scope(self):
        _current_user.set(self.member)
        set_current_tenant(self.a1)
        form = _ScopedPickerForm()
        set_current_tenant(self.a2)
        self.assertEqual(self.pks(form), set())
        self.assertEqual(self.pks(_ScopedPickerForm()), {self.loc_a2.pk})

    def test_scope_changed_after_construction_is_read_at_validation(self):
        """Read-then-validate: an unnarrowed stored queryset follows a later scope."""
        _current_user.set(None)
        form = _ScopedPickerForm(data={"location": self.loc_a2.pk})
        _current_user.set(self.member)
        set_current_tenant(self.a1)
        self.assertFalse(form.is_valid())

    def test_multiple_choice_field_is_scoped(self):
        _current_user.set(self.member)
        set_current_tenant(self.a1)
        self.assertEqual(self.pks(_ScopedMultiPickerForm(), "locations"), {self.loc_a1.pk})
        self.assertTrue(_ScopedMultiPickerForm(data={"locations": [self.loc_a1.pk]}).is_valid())
        foreign = _ScopedMultiPickerForm(data={"locations": [self.loc_a1.pk, self.loc_other.pk]})
        self.assertFalse(foreign.is_valid())
        self.assertIn("locations", foreign.errors)

    def test_queryset_without_tenant_support_is_left_alone(self):
        _current_user.set(self.member)
        set_current_tenant(self.a1)
        extra = User.objects.create_user(username="extra", password="pw")

        class _UserForm(forms.Form):
            user = TenantScopedModelChoiceField(queryset=User.objects.all(), required=False)

        pks = self.pks(_UserForm(), "user")
        self.assertIn(extra.pk, pks)
        self.assertIn(self.superuser.pk, pks)

    def test_none_queryset_is_tolerated(self):
        field = TenantScopedModelChoiceField(queryset=None)
        self.assertIsNone(field.queryset)

    def test_scoped_fields_are_marked(self):
        self.assertTrue(is_tenant_scoped_field(_ScopedPickerForm().fields["location"]))
        self.assertFalse(is_tenant_scoped_field(forms.CharField()))


class ApplyTenantScopedChoicesTests(_ScopeFixture):
    """C2/C3/C4: helper rewrite, explicit opt-out, auto-generated fields."""

    def test_rewrites_plain_fields_and_reports_names(self):
        form = _PlainPickerForm()
        self.assertEqual(apply_tenant_scoped_choices(form), ["location", "other"])
        self.assertTrue(is_tenant_scoped_field(form.fields["location"]))

    def test_rewritten_field_follows_the_scope_on_read_and_validation(self):
        _current_user.set(self.member)
        set_current_tenant(self.a1)
        form = _PlainPickerForm(data={"location": self.loc_a2.pk})
        apply_tenant_scoped_choices(form)
        self.assertEqual(self.pks(form), {self.loc_a1.pk})
        self.assertFalse(form.is_valid())

    def test_explicit_exclusion_keeps_the_field_plain(self):
        form = _PlainPickerForm()
        self.assertEqual(apply_tenant_scoped_choices(form, exclude=("other",)), ["location"])
        self.assertFalse(is_tenant_scoped_field(form.fields["other"]))

    def test_unknown_exclusion_name_raises(self):
        with self.assertRaisesMessage(ValueError, "unknown fields"):
            apply_tenant_scoped_choices(_PlainPickerForm(), exclude=("missing",))

    def test_idempotent(self):
        form = _PlainPickerForm()
        apply_tenant_scoped_choices(form)
        self.assertEqual(apply_tenant_scoped_choices(form), [])

    def test_constructor_state_is_preserved(self):
        class _F(forms.Form):
            location = forms.ModelChoiceField(
                queryset=Location.objects.all(),
                required=True,
                label="Where",
                help_text="help",
                empty_label="--none--",
                to_field_name="slug",
            )

        form = _F()
        apply_tenant_scoped_choices(form)
        field = form.fields["location"]
        self.assertTrue(field.required)
        self.assertEqual(field.label, "Where")
        self.assertEqual(field.help_text, "help")
        self.assertEqual(field.empty_label, "--none--")
        self.assertEqual(field.to_field_name, "slug")

    def test_unsupported_queryset_is_not_converted(self):
        class _F(forms.Form):
            user = forms.ModelChoiceField(queryset=User.objects.all())

        form = _F()
        self.assertEqual(apply_tenant_scoped_choices(form), [])
        self.assertFalse(scope_field(form.fields["user"]))

    def test_non_model_fields_are_ignored(self):
        class _F(forms.Form):
            name = forms.CharField()

        self.assertEqual(apply_tenant_scoped_choices(_F()), [])

    def test_auto_generated_modelform_fk_field_is_covered_without_declaration(self):
        class _LocationForm(TenantScopedFormMixin, forms.ModelForm):
            class Meta:
                model = Location
                fields = ["name", "slug", "site", "tenant", "parent"]

        _current_user.set(self.member)
        set_current_tenant(self.a1)
        form = _LocationForm()
        self.assertTrue(is_tenant_scoped_field(form.fields["parent"]))
        self.assertTrue(is_tenant_scoped_field(form.fields["tenant"]))
        self.assertEqual(self.pks(form, "parent"), {self.loc_a1.pk})


class TenantScopedFormMixinTests(_ScopeFixture):
    @staticmethod
    def _form(name="ThingForm", mixin_attrs=None, with_group=False, bases=()):
        attrs = {"tenant": forms.ModelChoiceField(queryset=Tenant.objects.all(), required=False)}
        if with_group:
            attrs["tenant_group"] = forms.ModelChoiceField(queryset=TenantGroup.objects.all(), required=False)
        attrs.update(mixin_attrs or {})
        return type(name, (TenantScopedFormMixin, *bases, forms.Form), attrs)

    def test_tenant_not_required_by_default(self):
        # The class name keeps the still-installed global patch out of the way.
        self.assertFalse(self._form("ThingFilterForm")().fields["tenant"].required)

    def test_required_once_a_tenant_exists(self):
        self.assertTrue(self._form(mixin_attrs={"tenant_required": True})().fields["tenant"].required)

    def test_not_required_without_tenant_rows(self):
        with mock.patch.object(Tenant.objects, "exists", return_value=False):
            form = self._form(mixin_attrs={"tenant_required": True})()
        self.assertFalse(form.fields["tenant"].required)

    def test_class_name_has_no_influence(self):
        for name in ("AssetThingFilterForm", "AssetThingBulkEditForm", "AssetThingForm"):
            form = self._form(name, {"tenant_required": True})()
            self.assertTrue(form.fields["tenant"].required, name)

    def test_tenant_group_sibling_field_is_excluded(self):
        form = self._form(mixin_attrs={"tenant_required": True}, with_group=True)()
        self.assertFalse(form.fields["tenant"].required)

    def test_bound_empty_tenant_is_rejected_when_required(self):
        form = self._form(mixin_attrs={"tenant_required": True})(data={})
        self.assertFalse(form.is_valid())
        self.assertIn("tenant", form.errors)

    def test_form_without_tenant_field_is_untouched(self):
        class _F(TenantScopedFormMixin, forms.Form):
            tenant_required = True
            name = forms.CharField(required=False)

        self.assertFalse(_F().fields["name"].required)

    def test_single_accessible_tenant_is_autoset_and_hidden(self):
        member = User.objects.create_user(username="solo", password="pw")
        grant(member, self.other, Role.objects.create(tenant=self.other, name="R2", permissions=[]))
        _current_user.set(member)
        set_current_tenant(self.other)
        form = self._form(mixin_attrs={"tenant_required": True, "tenant_autoset_when_single": True})()
        self.assertTrue(form.fields["tenant"].disabled)
        self.assertFalse(form.fields["tenant"].required)
        self.assertIsInstance(form.fields["tenant"].widget, forms.HiddenInput)
        self.assertEqual(form.initial["tenant"], self.other.pk)

    def test_autoset_is_off_unless_declared(self):
        _current_user.set(self.member)
        set_current_tenant(self.a1)
        self.assertFalse(self._form()().fields["tenant"].disabled)

    def test_choice_exclusion_is_explicit_by_field_name(self):
        class _F(TenantScopedFormMixin, forms.Form):
            tenant_scoped_choice_exclusions = ("other",)
            location = forms.ModelChoiceField(queryset=Location.objects.all(), required=False)
            other = forms.ModelChoiceField(queryset=Location.objects.all(), required=False)

        form = _F()
        self.assertTrue(is_tenant_scoped_field(form.fields["location"]))
        self.assertFalse(is_tenant_scoped_field(form.fields["other"]))

    def test_fields_added_after_init_are_not_rewritten_until_reapplied(self):
        class _F(TenantScopedFormMixin, forms.Form):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.fields["late"] = forms.ModelChoiceField(queryset=Location.objects.all(), required=False)

        form = _F()
        self.assertFalse(is_tenant_scoped_field(form.fields["late"]))
        self.assertEqual(apply_tenant_scoped_choices(form), ["late"])

    def test_tom_select_is_applied_and_can_be_disabled(self):
        class _On(TenantScopedFormMixin, forms.Form):
            a = forms.ChoiceField(choices=[("1", "One")])

        class _Off(_On):
            tom_select = False

        _Off.__name__ = "ThingTableConfigForm"  # keeps the still-installed global patch out
        self.assertEqual(_On().fields["a"].widget.attrs["data-tom-select"], "")
        self.assertNotIn("data-tom-select", _Off().fields["a"].widget.attrs)


class TenantRowsExistTests(TestCase):
    def test_reflects_rows(self):
        self.assertFalse(tenant_rows_exist())
        Tenant.objects.create(name="A1", slug="t-a1")
        self.assertTrue(tenant_rows_exist())

    def test_missing_table_errors_read_as_false(self):
        for exc in (ProgrammingError, OperationalError):
            with mock.patch.object(Tenant.objects, "exists", side_effect=exc("no table")):
                self.assertFalse(tenant_rows_exist(), exc)

    def test_other_errors_propagate(self):
        with mock.patch.object(Tenant.objects, "exists", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                tenant_rows_exist()

    def test_module_reexport(self):
        self.assertIs(forms_base.tenant_rows_exist, tenant_rows_exist)


class ApplyTomSelectTests(TestCase):
    @staticmethod
    def _fields(**fields):
        return type("ThingForm", (forms.Form,), fields)().fields

    def test_select_and_multiple_get_the_attribute(self):
        fields = self._fields(
            a=forms.ChoiceField(choices=[("1", "One")]),
            b=forms.MultipleChoiceField(choices=[("1", "One")]),
        )
        # The global patch may already have run; start from a clean slate.
        for f in fields.values():
            f.widget.attrs.pop("data-tom-select", None)
        self.assertEqual(apply_tom_select(fields), ["a", "b"])
        self.assertEqual(fields["a"].widget.attrs["data-tom-select"], "")

    def test_existing_value_is_preserved(self):
        widget = forms.Select(attrs={"data-tom-select": "custom"})
        fields = self._fields(a=forms.ChoiceField(choices=[("1", "One")], widget=widget))
        apply_tom_select(fields)
        self.assertEqual(fields["a"].widget.attrs["data-tom-select"], "custom")

    def test_exclusions(self):
        choices = [("1", "One")]
        cases = {
            "radio": forms.RadioSelect,
            "checkbox": forms.CheckboxSelectMultiple,
            "listbox": forms.SelectMultiple(attrs={"size": "8"}),
            "available": forms.SelectMultiple(attrs={"class": "form-select available-columns"}),
            "selected": forms.SelectMultiple(attrs={"class": "selected-columns"}),
        }
        for label, widget in cases.items():
            field = forms.MultipleChoiceField(choices=choices, widget=widget)
            field.widget.attrs.pop("data-tom-select", None)
            self.assertEqual(apply_tom_select([field]), [], label)
            self.assertNotIn("data-tom-select", field.widget.attrs, label)

    def test_non_select_widgets_are_untouched(self):
        fields = [forms.CharField(), forms.BooleanField(), forms.DateField()]
        self.assertEqual(apply_tom_select(fields), [])
        for f in fields:
            self.assertNotIn("data-tom-select", f.widget.attrs)

    def test_table_config_class_name_has_no_influence(self):
        class AssetTableConfigForm(forms.Form):
            a = forms.ChoiceField(choices=[("1", "One")])

        fields = AssetTableConfigForm().fields
        fields["a"].widget.attrs.pop("data-tom-select", None)
        self.assertEqual(apply_tom_select(fields), ["a"])


class TenantScopedFilterSetMixinTests(_ScopeFixture):
    @staticmethod
    def _filterset(**extra):
        attrs = {
            "location": django_filters.ModelChoiceFilter(queryset=Location.objects.all()),
            "locations": django_filters.ModelMultipleChoiceFilter(queryset=Location.objects.all()),
            "by_user": django_filters.ModelChoiceFilter(queryset=lambda request: User.objects.all()),
            "name": django_filters.CharFilter(),
            "Meta": type("Meta", (), {"model": Location, "fields": []}),
        }
        attrs.update(extra)
        return type("LocFilterSet", (TenantScopedFilterSetMixin, django_filters.FilterSet), attrs)

    def test_static_model_filters_follow_the_scope(self):
        _current_user.set(self.member)
        set_current_tenant(self.a1)
        fs = self._filterset()(data={"location": str(self.loc_a2.pk)})
        self.assertEqual(self.pks(fs.form), {self.loc_a1.pk})
        self.assertEqual(self.pks(fs.form, "locations"), {self.loc_a1.pk})
        self.assertFalse(fs.form.is_valid())

    def test_group_scope_bound_validation(self):
        _current_user.set(self.member)
        set_current_tenant(None)
        set_current_tenant_group(self.group)
        fs = self._filterset()(data={"location": str(self.loc_a2.pk)})
        self.assertTrue(fs.form.is_valid())
        fs = self._filterset()(data={"location": str(self.loc_other.pk)})
        self.assertFalse(fs.form.is_valid())

    def test_callable_queryset_is_left_alone(self):
        fs = self._filterset()(data={})
        self.assertFalse(fs.filters["by_user"].field_class is TenantScopedModelChoiceField)
        self.assertFalse(is_tenant_scoped_field(fs.form.fields["by_user"]))

    def test_non_model_filters_are_untouched(self):
        fs = self._filterset()(data={})
        self.assertFalse(is_tenant_scoped_field(fs.form.fields["name"]))

    def test_explicit_exclusion(self):
        fs = self._filterset(tenant_scoped_filter_exclusions=("locations",))(data={})
        self.assertTrue(is_tenant_scoped_field(fs.form.fields["location"]))
        self.assertFalse(is_tenant_scoped_field(fs.form.fields["locations"]))

    def test_unknown_exclusion_raises(self):
        with self.assertRaisesMessage(ValueError, "unknown filters"):
            self._filterset(tenant_scoped_filter_exclusions=("missing",))(data={})

    def test_already_built_field_is_converted(self):
        fs = self._filterset()(data={})
        self.assertTrue(is_tenant_scoped_field(fs.filters["location"].field))


class TenantScopedAdminFormMixinTests(_ScopeFixture):
    def test_generated_admin_form_scopes_model_choices(self):
        from django.contrib import admin

        class _LocationAdmin(TenantScopedAdminFormMixin, admin.ModelAdmin):
            fields = ("name", "slug", "site", "parent")
            tenant_scoped_choice_exclusions = ("site",)

        model_admin = _LocationAdmin(Location, AdminSite())
        request = RequestFactory().get("/")
        request.user = self.superuser
        form_class = model_admin.get_form(request)
        _current_user.set(self.member)
        set_current_tenant(self.a1)
        form = form_class()
        self.assertTrue(is_tenant_scoped_field(form.fields["parent"]))
        self.assertFalse(is_tenant_scoped_field(form.fields["site"]))
        self.assertEqual(self.pks(form, "parent"), {self.loc_a1.pk})


class CoreFormsUseExplicitScopingTests(TestCase):
    """WP3: core's own forms declare scoping instead of relying on the global patches."""

    def test_search_form_carries_the_mixin(self):
        from core.forms.mixins import SearchForm

        self.assertTrue(issubclass(SearchForm, TenantScopedFormMixin))

    def test_filter_and_bulk_edit_forms_tom_select_through_helper(self):
        from core.forms.mixins import BulkEditForm, FilterForm

        class _Edit(BulkEditForm):
            pick = forms.ChoiceField(choices=[("a", "A")], required=False)
            listbox = forms.ChoiceField(choices=[("a", "A")], widget=forms.Select(attrs={"size": "5"}))

        class _FS(django_filters.FilterSet):
            class Meta:
                model = Location
                fields = ["name"]

        class _Filter(FilterForm):
            filterset_class = _FS
            pick = forms.ChoiceField(choices=[("a", "A")], required=False)
            radio = forms.ChoiceField(choices=[("a", "A")], widget=forms.RadioSelect)

        edit = _Edit(model=Location) if "model" in _Edit.__init__.__code__.co_varnames else _Edit()
        self.assertIn("data-tom-select", edit.fields["pick"].widget.attrs)
        self.assertNotIn("data-tom-select", edit.fields["listbox"].widget.attrs)
        flt = _Filter()
        self.assertIn("data-tom-select", flt.fields["pick"].widget.attrs)
        self.assertNotIn("data-tom-select", flt.fields["radio"].widget.attrs)
