from typing import Any, get_args, get_type_hints

from django.test import SimpleTestCase

from users.api.scim.provider_patch import (
    UNSET,
    GroupPatch,
    SCIMPatchError,
    UserPatch,
    _Unset,
    get_patch_operations,
    parse_group_patch_operations,
    parse_member_ids,
    parse_user_patch_operations,
    parse_user_resource,
)


class ProviderPatchParserTests(SimpleTestCase):
    def test_user_patch_parser_handles_mixed_nested_operations(self):
        patch = parse_user_patch_operations(
            [
                {
                    "op": "replace",
                    "path": "userName",
                    "value": "renamed@example.com",
                },
                {
                    "op": "add",
                    "value": {
                        "name": {"givenName": "Ada", "familyName": "Lovelace"},
                        "emails": [{"value": "ada@example.com", "primary": True}],
                    },
                },
                {"op": "replace", "path": "active", "value": False},
                {"op": "remove", "path": "emails"},
            ]
        )

        self.assertEqual(patch.username, "renamed@example.com")
        self.assertEqual(patch.email, "")
        self.assertEqual(patch.first_name, "Ada")
        self.assertEqual(patch.last_name, "Lovelace")
        self.assertFalse(patch.active)

    def test_group_patch_parser_preserves_mixed_member_operations_and_filters(self):
        patch = parse_group_patch_operations(
            [
                {"op": "replace", "path": "displayName", "value": "Operators"},
                {
                    "op": "add",
                    "value": {"members": [{"value": "5"}, {"value": 6}]},
                },
                {
                    "op": "remove",
                    "path": 'members[value eq "5"]',
                },
                {
                    "op": "replace",
                    "value": {
                        "displayName": "On-call",
                        "members": [{"value": "7"}],
                    },
                },
            ]
        )

        self.assertEqual(patch.display_name, "On-call")
        self.assertEqual(
            [
                (operation.op, operation.member_ids, operation.filter_member_id, operation.clear_members)
                for operation in patch.member_operations
            ],
            [
                ("add", (5, 6), None, False),
                ("remove", (), 5, False),
                ("replace", (7,), None, False),
            ],
        )

    def test_group_patch_parser_distinguishes_clear_from_empty_remove(self):
        clear_patch = parse_group_patch_operations([{"op": "remove", "path": "members"}])
        self.assertTrue(clear_patch.member_operations[0].clear_members)

        noop_patch = parse_group_patch_operations([{"op": "remove", "path": "members", "value": []}])
        self.assertFalse(noop_patch.member_operations[0].clear_members)
        self.assertEqual(noop_patch.member_operations[0].member_ids, ())

    def test_group_patch_parser_rejects_remove_without_path(self):
        with self.assertRaisesRegex(SCIMPatchError, "remove operation requires a path") as context:
            parse_group_patch_operations([{"op": "remove"}])
        self.assertEqual(context.exception.scim_type, "noTarget")

    def test_group_patch_parser_rejects_fractional_ids_blank_names_and_unknown_paths(self):
        invalid_operations = (
            {"op": "add", "path": "members", "value": [{"value": 1.5}]},
            {"op": "add", "path": "members", "value": [{"value": True}]},
            {"op": "replace", "path": "members", "value": "1"},
            {"op": "replace", "path": "displayName", "value": "   "},
            {"op": "replace", "path": "displayName", "value": "bad\x00name"},
            {"op": "replace", "path": "unsupported", "value": "x"},
        )
        for operation in invalid_operations:
            with self.subTest(operation=operation), self.assertRaises(SCIMPatchError):
                parse_group_patch_operations([operation])

    def test_user_patch_parser_rejects_missing_values_and_unknown_paths(self):
        invalid_operations = (
            {"op": "replace", "path": "active"},
            {"op": "replace", "path": "active", "value": "maybe"},
            {"op": "replace", "path": "userName", "value": None},
            {"op": "replace", "path": "userName", "value": 123},
            {"op": "replace", "path": "userName", "value": "   "},
            {"op": "replace", "path": "userName", "value": "bad\x00name"},
            {"op": "replace", "path": "unsupported", "value": "x"},
        )
        for operation in invalid_operations:
            with self.subTest(operation=operation), self.assertRaises(SCIMPatchError):
                parse_user_patch_operations([operation])

    def test_group_patch_parser_rejects_invalid_member_ids(self):
        invalid_operations = (
            {"op": "add", "path": "members", "value": [{"value": "not-an-id"}]},
            {"op": "add", "path": "members", "value": [{}]},
            {"op": "remove", "path": 'members[value eq "not-an-id"]'},
        )
        for operation in invalid_operations:
            with self.subTest(operation=operation), self.assertRaisesRegex(SCIMPatchError, "Invalid member ID"):
                parse_group_patch_operations([operation])

    def test_group_patch_parser_rejects_overlong_display_names(self):
        with self.assertRaisesRegex(SCIMPatchError, "displayName exceeds maximum length of 100 characters"):
            parse_group_patch_operations([{"op": "replace", "path": "displayName", "value": "x" * 101}])

    def test_group_patch_parser_rejects_non_string_display_names(self):
        with self.assertRaisesRegex(SCIMPatchError, "displayName must be a string"):
            parse_group_patch_operations([{"op": "replace", "path": "displayName", "value": 123}])

    def test_patch_parser_rejects_missing_or_unknown_operations(self):
        for operation in ({}, {"op": "merge"}):
            with (
                self.subTest(operation=operation),
                self.assertRaisesRegex(SCIMPatchError, "Unsupported SCIM PATCH operation"),
            ):
                parse_group_patch_operations([operation])

        with self.assertRaisesRegex(SCIMPatchError, "remove operation requires a path"):
            parse_user_patch_operations([{"op": "remove"}])

        with self.assertRaisesRegex(SCIMPatchError, "Operations must be a list"):
            parse_group_patch_operations(None)

    def test_parser_rejects_client_controlled_overflows_and_malformed_nested_values(self):
        invalid_operations = (
            {"op": "replace", "path": "userName", "value": "x" * 151},
            {"op": "replace", "path": "emails", "value": [{"value": "x", "type": 1}]},
            {"op": "replace", "path": "emails", "value": [{"value": "x" * 255}]},
            {"op": "add", "path": "emails", "value": []},
            {"op": "add", "value": {"members": [{"value": "1", "unexpected": True}]}},
            {"op": "remove", "path": "members", "value": None},
        )
        for operation in invalid_operations:
            with self.subTest(operation=operation), self.assertRaises(SCIMPatchError):
                parse_user_patch_operations([operation]) if operation.get("path", "").lower() in {
                    "active",
                    "username",
                    "emails",
                } else parse_group_patch_operations([operation])

        huge_id = "9" * 5000
        for operation in (
            {"op": "add", "path": "members", "value": [{"value": huge_id}]},
            {"op": "remove", "path": f"members[value eq {huge_id!r}]"},
        ):
            with self.subTest(operation="huge-id"), self.assertRaises(SCIMPatchError):
                parse_group_patch_operations([operation])

    def test_parser_rejects_explicit_null_or_empty_paths(self):
        for path in (None, ""):
            with self.subTest(path=path), self.assertRaises(SCIMPatchError):
                parse_user_patch_operations([{"op": "replace", "path": path, "value": {"userName": "alice"}}])
        with self.assertRaisesRegex(SCIMPatchError, "path exceeds maximum length"):
            parse_group_patch_operations([{"op": "remove", "path": "members[value eq " + (" " * 300) + "1]"}])

    def test_parser_accepts_pathless_member_objects_and_rejects_empty_operations(self):
        patch = parse_group_patch_operations([{"op": "replace", "value": {"members": [{"value": "5"}]}}])
        self.assertEqual(patch.member_operations[0].member_ids, (5,))
        with self.assertRaisesRegex(SCIMPatchError, "Operations must not be empty"):
            parse_group_patch_operations([])

    def test_parser_preserves_provider_compatibility_for_active_and_filtered_work_email(self):
        patch = parse_user_patch_operations(
            [
                {"op": "replace", "path": "active", "value": "false"},
                {"op": "replace", "path": 'emails[type eq "work"].value', "value": "work@example.com"},
            ]
        )
        self.assertFalse(patch.active)
        self.assertEqual(patch.email, "work@example.com")

        resource = parse_user_resource(
            {
                "userName": "alice",
                "emails": [],
                "name": {"givenName": "Ada", "familyName": "Lovelace"},
                "active": "true",
            }
        )
        self.assertEqual(resource.username, "alice")
        self.assertEqual(resource.email, "")
        self.assertTrue(resource.active)

    def test_group_parser_limits_unique_member_ids_across_operations(self):
        first_ids = [{"value": str(index)} for index in range(1, 10001)]
        with self.assertRaisesRegex(SCIMPatchError, "Member entries"):
            parse_group_patch_operations(
                [
                    {"op": "add", "path": "members", "value": first_ids},
                    {"op": "add", "path": "members", "value": [{"value": "10001"}]},
                ]
            )

    def test_parser_accepts_standard_unmanaged_attributes_and_external_id(self):
        patch = parse_user_patch_operations(
            [
                {"op": "replace", "path": "displayName", "value": "Ada Lovelace"},
                {"op": "replace", "path": "externalId", "value": "external-1"},
                {"op": "replace", "path": "employeeNumber", "value": "E-123"},
                {"op": "replace", "path": "phoneNumbers", "value": [{"value": "+1"}]},
                {"op": "replace", "path": "ims", "value": [{"value": "sip:ada"}]},
                {"op": "replace", "path": "employeeNumber", "value": "E-123"},
                {"op": "replace", "path": "nickname", "value": "ada"},
                {
                    "op": "replace",
                    "path": "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:employeeNumber",
                    "value": "E-123",
                },
                {"op": "replace", "path": 'emails[ type eq "home" ].value', "value": 123},
                {
                    "op": "replace",
                    "value": {
                        "name": {
                            "formatted": "Ada Lovelace",
                            "middleName": "Byron",
                            "honorificPrefix": "Dr.",
                        },
                        "phoneNumbers": [{"value": "+1"}],
                        "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User": {"employeeNumber": "E-123"},
                    },
                },
            ]
        )
        self.assertIs(patch.username, UNSET)
        self.assertIs(patch.email, UNSET)
        self.assertIs(patch.first_name, UNSET)
        self.assertIs(patch.last_name, UNSET)
        self.assertIs(patch.active, UNSET)
        self.assertEqual(patch.external_id, "external-1")


class ProviderPatchTypingTests(SimpleTestCase):
    def test_patch_fields_use_a_typed_absent_sentinel_instead_of_any(self):
        user_hints = get_type_hints(UserPatch)
        group_hints = get_type_hints(GroupPatch)

        self.assertIsInstance(UNSET, _Unset)
        self.assertIs(UserPatch().username, UNSET)
        self.assertIs(GroupPatch().display_name, UNSET)
        self.assertNotIn(Any, user_hints.values())
        self.assertNotIn(Any, group_hints.values())
        self.assertIn(_Unset, get_args(user_hints["username"]))
        self.assertIn(str, get_args(user_hints["username"]))
        self.assertIn(_Unset, get_args(user_hints["active"]))
        self.assertIn(bool, get_args(user_hints["active"]))
        self.assertIn(_Unset, get_args(group_hints["display_name"]))
        self.assertIn(str, get_args(group_hints["display_name"]))

    def test_parser_bounds_documents_operations_and_member_arrays(self):
        with self.assertRaisesRegex(SCIMPatchError, "SCIM document must be an object"):
            get_patch_operations([])
        with self.assertRaisesRegex(SCIMPatchError, "SCIM document must be an object"):
            parse_user_resource([])
        with self.assertRaisesRegex(SCIMPatchError, "Operations must not exceed"):
            parse_group_patch_operations([{"op": "replace", "path": "displayName", "value": "x"}] * 1001)
        with self.assertRaisesRegex(SCIMPatchError, "Members must not exceed"):
            parse_member_ids([{"value": "1"}] * 10001)
