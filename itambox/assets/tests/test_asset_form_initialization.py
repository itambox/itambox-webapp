"""Characterization tests for ``AssetForm`` initialization (issue #97).

``AssetForm.__init__`` mixes request/precedence resolution, tenant scoping,
field configuration, dynamic custom fields and layout construction. These tests
lock the *observable* contract of that initialization — quick-add labels, HTMX
wiring, tenant resolution, FK scoping, role defaulting, tag preview, custom
fields and fieldset order — so decomposing the initializer can be shown to be
behaviour-neutral.

``asset_type`` precedence (issue #81) is characterized separately in
``test_asset_form_initial.py``; FK tenant scoping regressions (B2) in
``test_asset_form_fk_scoping.py``.
"""

from unittest.mock import patch

from crispy_forms.layout import Fieldset
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse
from model_bakery import baker

from assets.forms.asset_form import AssetForm
from assets.models import Asset, AssetRole, AssetTagSequence, AssetType, AssetTypeFieldset, StatusLabel
from core.tests.mixins import TenantTestMixin
from extras.models import CustomField, CustomFieldChoice, CustomFieldChoiceSet, CustomFieldset, CustomFieldsetField
from organization.models import CostCenter, Location, Site, Tenant
from procurement.models import PurchaseOrderLine

HTMX_RELOAD_ATTRS = {
    "hx-post": "",
    "hx-trigger": "change",
    "hx-target": "closest form",
    "hx-swap": "outerHTML",
    "hx-vals": '{"_reload": "1"}',
    "hx-include": "closest form",
}


def build_form_without_sequence(**kwargs):
    """Build an AssetForm with the tag-sequence lookup stubbed out.

    Returns the form plus the throwaway ``Asset`` the form handed to
    ``resolve_sequence_for_asset``, which exposes the tenant/asset-type the
    initializer resolved.
    """
    with patch(
        "assets.models.AssetTagSequence.resolve_sequence_for_asset",
        return_value=None,
    ) as resolve_sequence:
        form = AssetForm(**kwargs)
    return form, resolve_sequence.call_args.args[0]


def fieldset_legends(form):
    return [str(element.legend) for element in form.helper.layout if isinstance(element, Fieldset)]


class AssetFormFieldConfigurationTests(TestCase):
    """Static field configuration applied on every construction."""

    def test_asset_tag_is_required_even_though_the_model_allows_blank(self):
        form, _ = build_form_without_sequence()

        self.assertTrue(form.fields["asset_tag"].required)

    def test_quick_add_labels_target_the_modal_placeholder(self):
        form, _ = build_form_without_sequence()

        expected = {
            "asset_type": (reverse("assets:assettype_create") + "?_quickadd=1", "Asset Type"),
            "asset_role": (reverse("assets:assetrole_create") + "?_quickadd=1", "Asset Role"),
            "location": (reverse("organization:location_create") + "?_quickadd=1", "Location"),
        }
        for field_name, (quick_add_url, visible_text) in expected.items():
            with self.subTest(field=field_name):
                label = str(form.fields[field_name].label)
                self.assertIn(visible_text, label)
                self.assertIn('hx-get="' + quick_add_url + '"', label)
                self.assertIn('hx-target="#modal-placeholder"', label)
                self.assertIn("mdi-plus-circle-outline", label)

    def test_asset_type_and_tenant_share_the_same_htmx_reload_attributes(self):
        form, _ = build_form_without_sequence()

        for field_name in ("asset_type", "tenant"):
            with self.subTest(field=field_name):
                attrs = form.fields[field_name].widget.attrs
                for attr, value in HTMX_RELOAD_ATTRS.items():
                    self.assertEqual(attrs.get(attr), value)


class AssetFormRequestableInitialTests(TestCase):
    """The tri-state ``requestable`` model value maps onto the select choices."""

    def test_unsaved_asset_gets_no_requestable_initial(self):
        form, _ = build_form_without_sequence()

        self.assertIsNone(form.initial.get("requestable"))

    def test_inherited_requestable_maps_to_the_empty_choice(self):
        asset = baker.make(Asset, requestable=None)

        form, _ = build_form_without_sequence(instance=asset)

        self.assertEqual(form.initial["requestable"], "")

    def test_forced_requestable_maps_to_true(self):
        asset = baker.make(Asset, requestable=True)

        form, _ = build_form_without_sequence(instance=asset)

        self.assertEqual(form.initial["requestable"], "true")

    def test_forced_unrequestable_maps_to_false(self):
        asset = baker.make(Asset, requestable=False)

        form, _ = build_form_without_sequence(instance=asset)

        self.assertEqual(form.initial["requestable"], "false")


class AssetFormTenantResolutionTests(TestCase):
    """Bound data, then initial, then the instance decide the selected tenant."""

    @classmethod
    def setUpTestData(cls):
        cls.bound_tenant = Tenant.objects.create(name="Bound", slug="bound")
        cls.initial_tenant = Tenant.objects.create(name="Initial", slug="initial")
        cls.instance_tenant = Tenant.objects.create(name="Instance", slug="instance")
        cls.asset = baker.make(Asset, tenant=cls.instance_tenant)

    def test_truthy_bound_tenant_wins(self):
        _, preview_asset = build_form_without_sequence(
            data={"tenant": str(self.bound_tenant.pk)},
            instance=self.asset,
            initial={"tenant": self.initial_tenant.pk},
        )

        self.assertEqual(preview_asset.tenant, self.bound_tenant)

    def test_empty_bound_tenant_falls_through_to_initial(self):
        _, preview_asset = build_form_without_sequence(
            data={"tenant": ""},
            initial={"tenant": self.initial_tenant.pk},
        )

        self.assertEqual(preview_asset.tenant, self.initial_tenant)

    def test_initial_tenant_wins_over_the_instance(self):
        _, preview_asset = build_form_without_sequence(
            instance=self.asset,
            initial={"tenant": self.initial_tenant.pk},
        )

        self.assertEqual(preview_asset.tenant, self.initial_tenant)

    def test_initial_tenant_accepts_a_tenant_instance(self):
        _, preview_asset = build_form_without_sequence(
            initial={"tenant": self.initial_tenant},
        )

        self.assertEqual(preview_asset.tenant, self.initial_tenant)

    def test_instance_tenant_is_the_final_fallback(self):
        _, preview_asset = build_form_without_sequence(instance=self.asset)

        self.assertEqual(preview_asset.tenant, self.instance_tenant)

    def test_no_tenant_anywhere_resolves_to_none(self):
        _, preview_asset = build_form_without_sequence()

        self.assertIsNone(preview_asset.tenant)

    def test_malformed_or_unknown_tenant_does_not_break_initialization(self):
        unknown_pk = str(
            max(self.bound_tenant.pk, self.initial_tenant.pk, self.instance_tenant.pk) + 1000,
        )
        for raw_tenant in ("not-a-pk", unknown_pk):
            with self.subTest(tenant=raw_tenant):
                form, preview_asset = build_form_without_sequence(data={"tenant": raw_tenant})

                self.assertIsNone(preview_asset.tenant)
                self.assertIn("location", form.fields)


class AssetFormTenantScopedQuerysetTests(TenantTestMixin, TestCase):
    """``location`` / ``cost_center`` / ``purchase_order_line`` are rescoped per build."""

    def setUp(self):
        self.setup_tenant_context(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        self.site = Site.objects.create(name="HQ", slug="hq")
        self.loc_a = Location.objects.create(name="Loc A", slug="loc-a", site=self.site, tenant=self.tenant)
        self.loc_b = Location.objects.create(name="Loc B", slug="loc-b", site=self.site, tenant=self.tenant_b)
        self.cc_a = baker.make(CostCenter, tenant=self.tenant)
        self.cc_b = baker.make(CostCenter, tenant=self.tenant_b)
        line_type = baker.make(AssetType)
        self.line_a = baker.make(PurchaseOrderLine, tenant=self.tenant, asset_type=line_type)
        self.line_b = baker.make(PurchaseOrderLine, tenant=self.tenant_b, asset_type=line_type)

    def _field_pks(self, form, field_name):
        return set(form.fields[field_name].queryset.values_list("pk", flat=True))

    def test_selected_tenant_narrows_every_tenant_owned_fk(self):
        form, preview_asset = build_form_without_sequence(data={"tenant": str(self.tenant_b.pk)})

        self.assertEqual(preview_asset.tenant, self.tenant_b)
        self.assertEqual(self._field_pks(form, "location"), {self.loc_b.pk})
        self.assertEqual(self._field_pks(form, "cost_center"), {self.cc_b.pk})
        self.assertEqual(self._field_pks(form, "purchase_order_line"), {self.line_b.pk})

    def test_without_a_selected_tenant_the_manager_queryset_is_kept(self):
        form, preview_asset = build_form_without_sequence()

        self.assertIsNone(preview_asset.tenant)
        for field_name, model in (
            ("location", Location),
            ("cost_center", CostCenter),
            ("purchase_order_line", PurchaseOrderLine),
        ):
            with self.subTest(field=field_name):
                # Unnarrowed: exactly what the model's current default manager returns.
                self.assertEqual(
                    self._field_pks(form, field_name),
                    set(model.objects.values_list("pk", flat=True)),
                )
        self.assertIn(self.loc_a.pk, self._field_pks(form, "location"))
        self.assertIn(self.loc_b.pk, self._field_pks(form, "location"))

    def test_querysets_are_re_evaluated_through_the_current_manager(self):
        self.set_active_tenant(self.tenant)

        form, _ = build_form_without_sequence()

        self.assertEqual(self._field_pks(form, "location"), {self.loc_a.pk})
        self.assertEqual(self._field_pks(form, "cost_center"), {self.cc_a.pk})
        self.assertEqual(self._field_pks(form, "purchase_order_line"), {self.line_a.pk})


class AssetFormAssetRoleDefaultTests(TestCase):
    """The asset type's default role only ever seeds a brand-new asset."""

    @classmethod
    def setUpTestData(cls):
        cls.default_role = AssetRole.objects.create(name="Laptop", slug="laptop")
        cls.other_role = AssetRole.objects.create(name="Server", slug="server")
        cls.typed = baker.make(AssetType, asset_role=cls.default_role)
        cls.untyped = baker.make(AssetType, asset_role=None)

    def test_new_asset_takes_the_asset_types_default_role(self):
        form, _ = build_form_without_sequence(initial={"asset_type": self.typed.pk})

        self.assertEqual(form.fields["asset_role"].initial, self.default_role)

    def test_default_role_is_not_normalized_into_form_initial(self):
        form, _ = build_form_without_sequence(initial={"asset_type": self.typed.pk})

        self.assertNotIn("asset_role", form.initial)

    def test_asset_type_without_a_default_role_leaves_the_field_alone(self):
        form, _ = build_form_without_sequence(initial={"asset_type": self.untyped.pk})

        self.assertIsNone(form.fields["asset_role"].initial)

    def test_bound_asset_role_is_not_overridden(self):
        form, _ = build_form_without_sequence(
            data={"asset_type": str(self.typed.pk), "asset_role": str(self.other_role.pk)},
        )

        self.assertIsNone(form.fields["asset_role"].initial)

    def test_initial_asset_role_is_not_overridden(self):
        form, _ = build_form_without_sequence(
            initial={"asset_type": self.typed.pk, "asset_role": self.other_role.pk},
        )

        self.assertIsNone(form.fields["asset_role"].initial)

    def test_existing_asset_role_is_never_overwritten(self):
        asset = baker.make(Asset, asset_type=self.typed, asset_role=self.other_role)

        form, _ = build_form_without_sequence(instance=asset)

        self.assertIsNone(form.fields["asset_role"].initial)
        self.assertEqual(form.initial["asset_role"], self.other_role.pk)

    def test_asset_role_query_parameter_is_not_supported(self):
        form, _ = build_form_without_sequence(
            initial={"asset_type": self.typed.pk},
            data=None,
        )

        # Only the asset type seeds the role; there is no asset_role quick-add path.
        self.assertEqual(form.fields["asset_role"].initial, self.default_role)


class AssetFormAssetTagPreviewTests(TestCase):
    """The asset-tag help text previews the resolved sequence."""

    def test_help_text_shows_the_next_tag_and_a_fill_target(self):
        tenant = Tenant.objects.create(name="Preview", slug="preview")
        AssetTagSequence.objects.create(tenant=tenant, prefix="PRV-", next_value=7, zero_padding=4)

        form = AssetForm(initial={"tenant": tenant.pk})

        help_text = str(form.fields["asset_tag"].help_text)
        self.assertIn("PRV-0007", help_text)
        self.assertIn('data-fill-target="id_asset_tag"', help_text)
        self.assertIn('data-fill-value="PRV-0007"', help_text)

    def test_missing_sequence_falls_back_to_an_explanatory_message(self):
        form, _ = build_form_without_sequence()

        self.assertIn("No active tag sequence found for this scope.", str(form.fields["asset_tag"].help_text))

    def test_rendering_the_form_creates_the_global_default_sequence(self):
        # Documented (issue #97 non-goal) side effect of previewing at render time.
        self.assertFalse(AssetTagSequence.all_objects.filter(prefix="ASSET-").exists())

        AssetForm()

        self.assertTrue(
            AssetTagSequence.all_objects.filter(tenant__isnull=True, category__isnull=True, prefix="ASSET-").exists()
        )

    def test_preview_does_not_consume_a_tag(self):
        tenant = Tenant.objects.create(name="Stable", slug="stable")
        sequence = AssetTagSequence.objects.create(tenant=tenant, prefix="STB-", next_value=5)

        AssetForm(initial={"tenant": tenant.pk})

        sequence.refresh_from_db()
        self.assertEqual(sequence.next_value, 5)


class AssetFormLayoutTests(TestCase):
    """Fieldset order is part of the form's contract."""

    BASE_LEGENDS = [
        "Identity",
        "Classification",
        "Assignment",
        "Procurement & Financial",
        "Lifecycle",
        "Optional: Warranty",
        "Notes & Tags",
    ]

    def test_custom_specifications_section_is_omitted_without_custom_fields(self):
        form, _ = build_form_without_sequence()

        self.assertEqual(fieldset_legends(form), self.BASE_LEGENDS)

    def test_custom_specifications_section_is_inserted_before_the_warranty_section(self):
        CustomField.objects.create(
            name="hostname",
            label="Hostname",
            field_type=CustomField.FIELD_TYPE_TEXT,
            scope=CustomField.SCOPE_ASSET,
        )

        form, _ = build_form_without_sequence()

        expected = list(self.BASE_LEGENDS)
        expected.insert(expected.index("Optional: Warranty"), "Custom Specifications")
        self.assertEqual(fieldset_legends(form), expected)

    def test_layout_ends_with_the_shared_action_buttons(self):
        form, _ = build_form_without_sequence()

        rendered_tail = "".join(str(getattr(element, "html", "")) for element in form.helper.layout[-3:])
        self.assertIn(reverse("assets:asset_list"), rendered_tail)


class AssetFormCustomFieldTests(TestCase):
    """Dynamic per-device custom fields: selection, construction and initials."""

    @classmethod
    def setUpTestData(cls):
        cls.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)

    def _asset_custom_field(self, **kwargs):
        kwargs.setdefault("scope", CustomField.SCOPE_ASSET)
        return CustomField.objects.create(**kwargs)

    def test_global_custom_field_keys_follow_stable_key_ordering(self):
        self._asset_custom_field(name="zeta", label="Zeta", field_type="text")
        self._asset_custom_field(name="alpha", label="Alpha", field_type="text")
        self._asset_custom_field(name="mid", label="Mid", field_type="text")

        form, _ = build_form_without_sequence()

        self.assertEqual(form.custom_field_keys, ["cf_alpha", "cf_mid", "cf_zeta"])

    def test_each_supported_field_type_builds_its_form_field(self):
        self._asset_custom_field(name="txt", label="A Text", field_type="text")
        self._asset_custom_field(name="num", label="B Number", field_type="integer")
        self._asset_custom_field(name="dec", label="C Decimal", field_type="decimal", decimal_scale=2)
        self._asset_custom_field(name="dat", label="C Date", field_type="date")
        self._asset_custom_field(name="boo", label="D Boolean", field_type="boolean")
        choice_set = CustomFieldChoiceSet.objects.create(namespace="local", slug="colours", label="Colours")
        CustomFieldChoice.objects.create(choice_set=choice_set, key="red", label="Red", position=1)
        CustomFieldChoice.objects.create(choice_set=choice_set, key="green", label="Green", position=2)
        CustomFieldChoice.objects.create(choice_set=choice_set, key="blue", label="Blue", position=3)
        self._asset_custom_field(
            name="sel",
            label="E Select",
            field_type=CustomField.FIELD_TYPE_SINGLE_SELECT,
            choice_set=choice_set,
        )

        form, _ = build_form_without_sequence()

        self.assertEqual(form.fields["cf_txt"].__class__.__name__, "CharField")
        self.assertEqual(form.fields["cf_num"].__class__.__name__, "IntegerField")
        self.assertEqual(form.fields["cf_dec"].__class__.__name__, "DecimalField")
        self.assertEqual(form.fields["cf_dat"].__class__.__name__, "DateField")
        self.assertEqual(form.fields["cf_boo"].__class__.__name__, "BooleanField")
        self.assertEqual(form.fields["cf_sel"].__class__.__name__, "ChoiceField")
        self.assertEqual(
            form.fields["cf_sel"].choices,
            [("", "---------"), ("red", "Red"), ("green", "Green"), ("blue", "Blue")],
        )
        self.assertFalse(form.fields["cf_boo"].initial)
        self.assertEqual(form.fields["cf_txt"].label, "A Text")

    def test_required_flag_is_carried_over(self):
        self._asset_custom_field(name="needed", label="Needed", field_type="text", required=True)
        self._asset_custom_field(name="optional", label="Optional", field_type="text", required=False)

        form, _ = build_form_without_sequence()

        self.assertTrue(form.fields["cf_needed"].required)
        self.assertFalse(form.fields["cf_optional"].required)

    def test_stored_values_seed_the_field_initials(self):
        self._asset_custom_field(name="hostname", label="Hostname", field_type="text")
        asset = Asset.objects.create(
            name="Stored",
            asset_tag="STORED-1",
            status=self.status,
            custom_field_data={"hostname": "srv-01"},
        )

        form, _ = build_form_without_sequence(instance=asset)

        self.assertEqual(form.fields["cf_hostname"].initial, "srv-01")

    def test_fieldset_fields_of_the_selected_asset_type_are_added_once(self):
        self._asset_custom_field(name="hostname", label="Hostname", field_type="text")
        scoped_field = self._asset_custom_field(name="rack", label="Rack", field_type="text")
        spec_field = CustomField.objects.create(
            name="cpu",
            label="CPU",
            field_type="text",
            scope=CustomField.SCOPE_ASSET_TYPE,
        )
        fieldset = CustomFieldset.objects.create(
            name="Server Specs",
            namespace="local",
            slug="server-specs",
            label="Server Specs",
        )
        CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=scoped_field, position=10)
        CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=spec_field, position=20)
        asset_type = baker.make(AssetType)
        AssetTypeFieldset.objects.create(asset_type=asset_type, fieldset=fieldset, position=10)

        form, _ = build_form_without_sequence(initial={"asset_type": asset_type.pk})

        self.assertEqual(form.custom_field_keys.count("cf_hostname"), 1)
        self.assertIn("cf_rack", form.custom_field_keys)
        self.assertNotIn("cf_cpu", form.custom_field_keys)

    def test_custom_fields_are_paired_two_per_layout_row(self):
        self._asset_custom_field(name="one", label="A One", field_type="text")
        self._asset_custom_field(name="two", label="B Two", field_type="text")
        self._asset_custom_field(name="three", label="C Three", field_type="text")

        form, _ = build_form_without_sequence()

        custom_fieldset = next(
            element
            for element in form.helper.layout
            if isinstance(element, Fieldset) and str(element.legend) == "Custom Specifications"
        )
        rows = [[column.fields[0] for column in row.fields] for row in custom_fieldset.fields]
        self.assertEqual(rows, [["cf_one", "cf_three"], ["cf_two"]])


def marked_translation(message):
    """Stand-in for gettext that makes each translated unit visible in output."""
    return f"«{message}»"


class AssetFormRenderedEscapingTests(TestCase):
    """Dynamic values reach the browser escaped, and visible text is translated.

    Rendering goes through the same template the HTMX reload path uses, so a
    downstream unsafe-template regression is caught too — inspecting the helper
    value alone would not.
    """

    # 20 characters at most (AssetTagSequence.prefix max_length), attribute-breaking.
    MALICIOUS_PREFIX = '"><svg onload=y>'

    def _render(self, form):
        return render_to_string("htmx/crispy_form.html", {"form": form})

    def test_malicious_tag_prefix_is_escaped_in_text_and_in_data_fill_value(self):
        tenant = Tenant.objects.create(name="Evil", slug="evil")
        AssetTagSequence.objects.create(tenant=tenant, prefix=self.MALICIOUS_PREFIX, next_value=1, zero_padding=6)

        html = self._render(AssetForm(initial={"tenant": tenant.pk}))

        self.assertNotIn(self.MALICIOUS_PREFIX, html)
        self.assertNotIn("<svg", html)
        self.assertIn("&quot;&gt;&lt;svg onload=y&gt;000001", html)
        # The developer-controlled markup around it survives.
        self.assertIn('data-fill-target="id_asset_tag"', html)
        self.assertIn("data-fill-value=", html)

    def test_quick_add_labels_render_markup_but_translate_only_text(self):
        with patch("assets.forms.asset_form._", marked_translation):
            form, _preview = build_form_without_sequence()

        for field_name, label_text, title_text in (
            ("asset_type", "Asset Type", "Add new Asset Type"),
            ("asset_role", "Asset Role", "Add new Asset Role"),
            ("location", "Location", "Add new Location"),
        ):
            with self.subTest(field=field_name):
                label = str(form.fields[field_name].label)
                self.assertIn(marked_translation(label_text), label)
                self.assertIn('title="' + marked_translation(title_text) + '"', label)
                # Markup is never part of a translatable message.
                self.assertNotIn(f"{marked_translation(label_text)[:-1]} <button", label)
                self.assertIn("<button", label)

    def test_tag_preview_help_text_translates_its_visible_text(self):
        tenant = Tenant.objects.create(name="Translated", slug="translated")
        AssetTagSequence.objects.create(tenant=tenant, prefix="TRN-", next_value=1, zero_padding=6)

        with patch("assets.forms.asset_form._", marked_translation):
            form = AssetForm(initial={"tenant": tenant.pk})

        help_text = str(form.fields["asset_tag"].help_text)
        self.assertIn(marked_translation("Suggested:"), help_text)
        self.assertIn("TRN-000001", help_text)
        self.assertIn('<a href="#"', help_text)

    def test_missing_sequence_message_is_translated(self):
        with patch("assets.forms.asset_form._", marked_translation):
            form, _preview = build_form_without_sequence()

        self.assertIn(
            marked_translation("No active tag sequence found for this scope."),
            str(form.fields["asset_tag"].help_text),
        )

    def test_warranty_hint_is_rendered_as_translated_text(self):
        with patch("assets.forms.asset_form._", marked_translation):
            form, _preview = build_form_without_sequence()

        rendered_hints = "".join(
            str(getattr(element, "html", ""))
            for fieldset in form.helper.layout
            if isinstance(fieldset, Fieldset)
            for element in fieldset.fields
        )
        self.assertIn(
            marked_translation("Fill in to create a warranty for this asset; leave blank to skip."),
            rendered_hints,
        )
