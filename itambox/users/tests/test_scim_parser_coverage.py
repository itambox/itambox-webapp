from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID

from django.db import IntegrityError
from django.test import SimpleTestCase, TestCase

from users.api.scim.filters import (
    SCIMFilterError,
    _build_filter_query,
    parse_scim_filter,
    parse_scim_membership_filter,
)
from users.api.scim.identifiers import identifier_lookup_or_none
from users.api.scim.provider_patch import (
    GroupMemberOperation,
    SCIMPatchError,
    parse_group_patch_operations,
    parse_member_ids,
    parse_user_patch_operations,
    parse_user_resource,
)
from users.api.scim.provider_services import (
    _apply_group_member_operation,
    _resolve_provider_member_ids,
    _save_provider_user_external_id,
    ensure_provider_group_external_id_available,
)


class SCIMParserCoverageTests(SimpleTestCase):
    def test_filters_cover_boolean_id_and_scoped_variants(self):
        self.assertEqual(parse_scim_filter("active eq false", "user"), parse_scim_filter("active eq false", "user"))
        self.assertEqual(parse_scim_filter("active eq null", "user"), parse_scim_filter("active eq null", "user"))
        self.assertIsNone(parse_scim_membership_filter("active eq"))
        self.assertIsNone(parse_scim_membership_filter("unsupported eq value"))

        legacy = parse_scim_filter("id eq 42", "user")
        legacy_ne = parse_scim_filter("id ne 42", "user")
        self.assertIn("pk", str(legacy))
        self.assertIn("NOT", str(legacy_ne))
        opaque = "12345678-1234-5678-1234-567812345678"
        self.assertIn("scim_id", str(parse_scim_filter(f"id eq {opaque}", "user")))
        self.assertIn("NOT", str(parse_scim_filter(f"id ne {opaque}", "user")))
        self.assertIn("scim_id", str(parse_scim_filter("id pr", "user")))

        with self.assertRaises(SCIMFilterError):
            parse_scim_filter("id co 42", "user")
        with self.assertRaises(SCIMFilterError):
            parse_scim_filter("id eq 9223372036854775808", "user")
        with self.assertRaises(SCIMFilterError):
            parse_scim_filter('id eq "not-a-uuid"', "user")

        self.assertIn("external_id__exact", str(_build_filter_query("external_id", "eq", "x")))
        self.assertIn("field", str(_build_filter_query("field", "eq", 1)))
        self.assertIn("NOT", str(_build_filter_query("field", "ne", 1)))
        self.assertTrue(_build_filter_query("external_id", "pr", None).children)

        self.assertIn("external_id", str(parse_scim_filter('externalId eq "x"', "group")))
        self.assertIn("scim_id", str(parse_scim_filter("id pr", "group")))
        with self.assertRaises(SCIMFilterError):
            parse_scim_filter("members__user eq 1", "user")

    def test_identifier_lookup_or_none_fails_closed(self):
        self.assertIsNone(identifier_lookup_or_none("not-a-scim-id"))

    def test_patch_parser_covers_external_ids_and_opaque_member_ids(self):
        opaque = str(UUID("12345678-1234-5678-1234-567812345678"))
        self.assertEqual(parse_member_ids([{"value": opaque}]), (opaque,))
        user_patch = parse_user_patch_operations(
            [
                {"op": "add", "value": {"externalId": "nested-user-id"}},
                {"op": "replace", "path": "externalId", "value": "path-user-id"},
                {"op": "remove", "path": "externalId"},
            ]
        )
        self.assertEqual(user_patch.external_id, "")
        self.assertEqual(
            parse_user_resource({"userName": "alice", "externalId": "resource-id"}).external_id, "resource-id"
        )

        pathless_members = parse_group_patch_operations([{"op": "add", "value": [{"value": opaque}]}])
        self.assertEqual(pathless_members.member_operations[0].member_ids, (opaque,))
        nested_group = parse_group_patch_operations([{"op": "replace", "value": {"externalId": "group-id"}}])
        self.assertEqual(nested_group.external_id, "group-id")
        removed_group = parse_group_patch_operations([{"op": "remove", "path": "externalId"}])
        self.assertEqual(removed_group.external_id, "")
        with self.assertRaises(SCIMPatchError):
            parse_group_patch_operations([{"op": "replace", "value": {"externalId": "x", "unknown": True}}])


class ProviderServiceCoverageTests(TestCase):
    def test_provider_member_resolution_counts_invalid_and_resolved_ids(self):
        tenant = SimpleNamespace(slug="provider")
        self.assertEqual(_resolve_provider_member_ids(tenant, ["not-an-id"]), (set(), 1))

        opaque = UUID("12345678-1234-5678-1234-567812345678")
        query = Mock()
        query.filter.return_value.values_list.return_value.distinct.return_value = [(7, opaque)]
        with patch("users.api.scim.provider_services.User.objects.filter", return_value=query):
            resolved, skipped = _resolve_provider_member_ids(tenant, ["7", str(opaque), "missing"])
        self.assertEqual(resolved, {7})
        self.assertEqual(skipped, 1)

    def test_provider_external_id_conflict_and_member_remove_paths(self):
        user = SimpleNamespace()
        tenant = SimpleNamespace()
        membership = Mock(external_id="old")
        membership.save.side_effect = IntegrityError
        with patch("users.api.scim.provider_services.Membership.objects.filter") as membership_filter:
            membership_filter.return_value.first.return_value = membership
            with self.assertRaisesRegex(SCIMPatchError, "externalId is already used"):
                _save_provider_user_external_id(user, tenant, "new")

        queryset = Mock()
        queryset.exists.return_value = True
        with patch("users.api.scim.provider_services.UserGroup.objects.filter", return_value=queryset):
            with self.assertRaisesRegex(SCIMPatchError, "Group externalId already exists"):
                ensure_provider_group_external_id_available(tenant, "duplicate")

        operation = GroupMemberOperation(op="remove", member_ids=(7,))
        with patch("users.api.scim.provider_services._resolved_operation_member_ids", return_value={7}):
            current = _apply_group_member_operation(tenant, {7, 8}, operation)
        self.assertEqual(current, {8})
