"""Server-side coverage for the role-form preset picker (Fix #9, §6).

The preset picker is a client-side convenience that pre-checks the permission
matrix. The TypeScript cannot be unit-tested here, so these tests assert the
*data* the server hands the client (``RoleForm.preset_field_map`` /
``preset_definitions``) is correct and scoped to the form's own matrix models,
and that feeding a preset's fields through the form produces the expected
permission grant. They also confirm the escalation guard still runs for a
non-superuser regardless of preset.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model

from core.tests.mixins import TenantTestMixin
from organization.models import Role
from organization.forms import RoleForm, MATRIX_MODELS

User = get_user_model()


class RolePresetFieldMapTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.clear_tenant_context()
        self.setup_tenant_context()
        self.superuser = self.tenant_admin  # created superuser by the mixin

    def tearDown(self):
        self.clear_tenant_context()

    def _form(self):
        return RoleForm(tenant=self.tenant, user=self.superuser)

    # ----------------------------------------------------------------- structure
    def test_preset_definitions_start_with_blank(self):
        form = self._form()
        values = [value for value, _label in form.preset_definitions]
        self.assertEqual(values[0], 'blank')
        self.assertEqual(
            set(values),
            {'blank', 'administrator', 'technician', 'readonly'},
        )

    def test_field_map_keys_match_definitions(self):
        form = self._form()
        self.assertEqual(
            set(form.preset_field_map.keys()),
            {value for value, _label in form.preset_definitions},
        )

    def test_blank_preset_checks_nothing(self):
        self.assertEqual(self._form().preset_field_map['blank'], [])

    # --------------------------------------------------------------- correctness
    def test_administrator_is_full_matrix(self):
        fmap = self._form().preset_field_map
        expected = {
            f'perm_{key}_{action}'
            for key in MATRIX_MODELS
            for action in ('read', 'create', 'edit', 'delete')
        }
        self.assertEqual(set(fmap['administrator']), expected)

    def test_administrator_is_superset_of_all_presets(self):
        fmap = self._form().preset_field_map
        admin = set(fmap['administrator'])
        self.assertTrue(set(fmap['technician']).issubset(admin))
        self.assertTrue(set(fmap['readonly']).issubset(admin))

    def test_readonly_is_view_only(self):
        # Read-Only must pre-check only the "read" (view_*) column.
        fmap = self._form().preset_field_map
        self.assertTrue(all(f.endswith('_read') for f in fmap['readonly']))
        expected = {f'perm_{key}_read' for key in MATRIX_MODELS}
        self.assertEqual(set(fmap['readonly']), expected)

    def test_technician_excludes_delete(self):
        # Technician gets read/create/edit but never delete_*.
        fmap = self._form().preset_field_map
        self.assertFalse(any(f.endswith('_delete') for f in fmap['technician']))
        expected = {
            f'perm_{key}_{action}'
            for key in MATRIX_MODELS
            for action in ('read', 'create', 'edit')
        }
        self.assertEqual(set(fmap['technician']), expected)

    # ------------------------------------------------------------------- scoping
    def test_field_map_scoped_to_forms_matrix_models(self):
        # Every field referenced by every preset must be a real matrix field on
        # this form (perm_<key>_<action> for a key the form actually renders).
        form = self._form()
        fmap = form.preset_field_map
        valid_fields = {
            f'perm_{key}_{action}'
            for key in MATRIX_MODELS
            for action in ('read', 'create', 'edit', 'delete')
        }
        for preset, fields in fmap.items():
            for fname in fields:
                self.assertIn(fname, valid_fields, f'{preset} references unknown {fname}')
                self.assertIn(fname, form.fields, f'{fname} is not a form field')

    # ----------------------------------------------- presets flow through clean()
    def test_readonly_preset_grants_only_view_perms(self):
        # Submitting the Read-Only preset's fields yields only view_* grants
        # (dashboard perms are auto-added; every other grant must be a view_).
        fmap = self._form().preset_field_map
        data = {'name': 'RO Role', 'tenant': self.tenant.pk}
        for fname in fmap['readonly']:
            data[fname] = True
        form = RoleForm(data=data, tenant=self.tenant, user=self.superuser)
        self.assertTrue(form.is_valid(), form.errors)
        granted = set(form.instance.permissions)
        dashboard = {
            'extras.view_dashboard', 'extras.change_dashboard',
            'extras.add_dashboard', 'extras.delete_dashboard',
        }
        non_dashboard = granted - dashboard
        self.assertTrue(non_dashboard, 'expected at least one matrix grant')
        self.assertTrue(all('.view_' in p for p in non_dashboard), non_dashboard)

    def test_technician_preset_grants_no_delete_perms(self):
        fmap = self._form().preset_field_map
        data = {'name': 'Tech Role', 'tenant': self.tenant.pk}
        for fname in fmap['technician']:
            data[fname] = True
        form = RoleForm(data=data, tenant=self.tenant, user=self.superuser)
        self.assertTrue(form.is_valid(), form.errors)
        granted = set(form.instance.permissions)
        self.assertFalse(any('.delete_' in p for p in granted), granted)

    def test_preset_does_not_bypass_escalation_guard(self):
        # A non-superuser who holds no permissions cannot save the Administrator
        # preset — the escalation guard in clean() rejects it. Proves presets are
        # convenience only and never widen what the actor may grant.
        fmap = self._form().preset_field_map
        data = {'name': 'Escalated', 'tenant': self.tenant.pk}
        for fname in fmap['administrator']:
            data[fname] = True
        form = RoleForm(data=data, tenant=self.tenant, user=self.tenant_user)
        self.set_active_tenant(self.tenant, self.tenant_membership)
        try:
            self.assertFalse(form.is_valid())
        finally:
            self.clear_tenant_context()
