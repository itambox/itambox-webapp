"""Provider SCIM group-provisioning contracts (WP-20, issue #193).

These tests pin the *service* boundary — ``apply_provider_group_patch`` /
``sync_provider_group_members`` / ``create_provider_group`` — against a real
database. ``users/tests/test_provider_patch.py`` already covers the pure parser;
nothing here re-asserts parser return values. What is asserted here is what the
parser cannot see:

* the persisted ``GroupMembership`` set a parsed operation list converges to,
* that a rejected PATCH leaves the database byte-identical (atomicity),
* that reconciliation touches ``GroupMembership.SOURCE_SCIM`` rows and nothing
  else (provenance),
* that the escalation guards fire before any row is written, and that a refused
  request leaks nothing about resources it may not see.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from core.tests.mixins import grant
from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant
from users.api.scim.provider_patch import SCIMPatchError, parse_group_patch_operations
from users.api.scim.provider_services import (
    apply_provider_group_patch,
    create_provider_group,
    sync_provider_group_members,
)
from users.models import GroupMembership, Token, UserGroup

User = get_user_model()

# The exact permission strings this surface is contractually gated on. Spelled out
# once so a rename cannot silently pass by "some permission was required".
PERM_CHANGE_MEMBERSHIP = "organization.change_membership"
PERM_VIEW_GROUP = "users.view_usergroup"
PERM_ADD_GROUP = "users.add_usergroup"
PERM_CHANGE_GROUP = "users.change_usergroup"


def member_filter_path(member_id, quote='"'):
    """Build the SCIM member path filter, e.g. ``members[value eq "42"]``.

    The quoting style is a parameter because IdPs emit all three forms (double
    quoted, single quoted and bare) and every one of them is part of the contract.
    """
    return "members[value eq %s%s%s]" % (quote, member_id, quote)


class ProviderGroupContractMixin:
    """Provider fixture shared by the contract, provenance and escalation suites.

    Mirrors ``users/tests/test_provider_scim.py``: a provider is a plain
    ``Tenant(is_provider=True)``, authority comes from permission *content* on a
    ``Role`` reached through ``grant()``, and a token is provider-scoped purely by
    its ``tenant`` FK.
    """

    def setUp(self):
        self.plain_tenant = Tenant.objects.create(name="Acme Corp", slug="acme-contracts")
        self.provider = Tenant.objects.create(name="MSP Contracts", slug="msp-contracts", is_provider=True)
        self.other_provider = Tenant.objects.create(name="MSP Rival", slug="msp-rival", is_provider=True)

        # Deliberately unrelated role names: authorization must resolve from
        # permission content, never from a name match.
        self.role_full = Role.objects.create(
            tenant=self.provider,
            name="Tier 3 Provisioning",
            permissions=[
                PERM_CHANGE_MEMBERSHIP,
                PERM_VIEW_GROUP,
                PERM_ADD_GROUP,
                PERM_CHANGE_GROUP,
            ],
        )
        # Holds the SCIM identity permission but no group-write authority.
        self.role_identity_only = Role.objects.create(
            tenant=self.provider,
            name="Identity Only",
            permissions=[PERM_CHANGE_MEMBERSHIP, PERM_VIEW_GROUP],
        )
        # Holds group-write authority but not the identity permission the inner
        # membership reconciliation demands.
        self.role_group_only = Role.objects.create(
            tenant=self.provider,
            name="Group Only",
            permissions=[PERM_VIEW_GROUP, PERM_ADD_GROUP, PERM_CHANGE_GROUP],
        )
        self.role_none = Role.objects.create(tenant=self.provider, name="No Authority", permissions=[])

        self.actor = User.objects.create_user(username="contract-actor", email="actor@msp.test")
        grant(self.actor, self.provider, self.role_full)

        self.identity_only_user = User.objects.create_user(username="identity-only", email="identity@msp.test")
        grant(self.identity_only_user, self.provider, self.role_identity_only)

        self.group_only_user = User.objects.create_user(username="group-only", email="group@msp.test")
        grant(self.group_only_user, self.provider, self.role_group_only)

        self.readonly_user = User.objects.create_user(username="readonly", email="readonly@msp.test")
        grant(self.readonly_user, self.provider, self.role_none)

        # Provisionable provider staff.
        self.alice = self.make_staff("alice")
        self.bob = self.make_staff("bob")
        self.carol = self.make_staff("carol")

        # Ineligible principals.
        self.outsider = User.objects.create_user(username="outsider", email="outsider@example.test")
        self.deprovisioned = self.make_staff("deprovisioned", is_active=False)
        self.rival_staff = User.objects.create_user(username="rival-staff", email="rival@rival.test")
        Membership.objects.create(user=self.rival_staff, tenant=self.other_provider, is_active=True)
        self.plain_tenant_user = User.objects.create_user(username="plain-user", email="plain@acme.test")
        Membership.objects.create(user=self.plain_tenant_user, tenant=self.plain_tenant, is_active=True)

        self.token = Token.objects.create(
            user=self.actor,
            tenant=self.provider,
            expires=timezone.now() + timezone.timedelta(days=1),
        )
        self.auth_headers = {"HTTP_AUTHORIZATION": f"Bearer {self.token.key}"}

    # ---- fixture helpers ----------------------------------------------------------------

    def make_staff(self, username, *, is_active=True):
        user = User.objects.create_user(username=username, email=f"{username}@msp.test")
        Membership.objects.create(user=user, tenant=self.provider, is_active=is_active)
        return user

    def make_group(self, name, members=(), *, tenant=None):
        group = UserGroup.objects.create(tenant=tenant or self.provider, name=name)
        if members:
            sync_provider_group_members(
                self.provider,
                group,
                [member.pk for member in members],
                actor=self.actor,
            )
        return group

    def add_row(self, group, user, source, external_id="", added_by=None):
        """Create a non-SCIM ``GroupMembership`` the way another provisioning domain would."""
        membership = Membership.objects.get(user=user, tenant=group.tenant)
        return GroupMembership.objects.create(
            user_group=group,
            membership=membership,
            source=source,
            external_id=external_id,
            added_by=added_by,
        )

    # ---- assertion helpers --------------------------------------------------------------

    def apply_operations(self, group, operations, *, actor=None):
        patch = parse_group_patch_operations(operations)
        return apply_provider_group_patch(self.provider, group, patch, actor=actor or self.actor)

    def rows_by_user(self, group):
        return {row.membership.user_id: row for row in group.group_memberships.select_related("membership")}

    def scim_member_ids(self, group):
        return {
            row.membership.user_id
            for row in group.group_memberships.select_related("membership")
            if row.source == GroupMembership.SOURCE_SCIM
        }

    def group_snapshot(self, group):
        """Everything a SCIM mutation could possibly change about a group's members."""
        group.refresh_from_db()
        return (
            group.name,
            group.external_id,
            frozenset(
                (row.pk, row.membership.user_id, row.source, row.external_id, row.added_by_id)
                for row in group.group_memberships.select_related("membership")
            ),
        )


class ProviderGroupMemberPatchContractTests(ProviderGroupContractMixin, TestCase):
    """Table-driven ``members`` contracts, applied against the database.

    Every row is applied **twice**. The second application is the idempotent-replay
    contract: the same PATCH must converge to the same member set, must not create
    a duplicate ``GroupMembership``, must not churn row identity (primary keys are
    stable), and must never manufacture a ``RoleGrant``.
    """

    def member_contract_rows(self):
        alice, bob, carol = self.alice, self.bob, self.carol

        def add(value, path="members"):
            operation = {"op": "add", "value": value}
            if path is not None:
                operation["path"] = path
            return operation

        return (
            # --- add -------------------------------------------------------------------
            ("add-single-member", (), [add([{"value": str(alice.pk)}])], {alice}),
            (
                "add-nested-value-payload-attributes",
                (),
                [
                    add(
                        [
                            {
                                "value": str(alice.pk),
                                "display": "Alice Example",
                                "$ref": "https://idp.test/scim/v2/Users/alice",
                                "type": "User",
                            },
                            {"value": str(bob.scim_id)},
                        ]
                    )
                ],
                {alice, bob},
            ),
            ("add-is-additive-not-replacing", (alice,), [add([{"value": str(bob.pk)}])], {alice, bob}),
            (
                "add-duplicate-ids-in-mixed-forms-converge",
                (alice,),
                [
                    add(
                        [
                            {"value": str(alice.pk)},
                            {"value": str(alice.scim_id)},
                            {"value": str(bob.pk)},
                            {"value": str(bob.pk)},
                        ]
                    )
                ],
                {alice, bob},
            ),
            (
                "add-repeated-across-operations",
                (),
                [add([{"value": str(alice.pk)}]), add([{"value": str(alice.pk)}])],
                {alice},
            ),
            ("add-single-member-object-instead-of-array", (), [add({"value": str(alice.pk)})], {alice}),
            ("add-pathless-member-array", (alice,), [add([{"value": str(bob.pk)}], path=None)], {alice, bob}),
            (
                "add-pathless-nested-members-object",
                (),
                [add({"members": [{"value": str(bob.pk)}]}, path=None)],
                {bob},
            ),
            (
                "operation-name-and-path-are-case-insensitive",
                (),
                [{"op": "Add", "path": "Members", "value": [{"value": str(alice.pk)}]}],
                {alice},
            ),
            # --- remove ----------------------------------------------------------------
            (
                "remove-by-value-list",
                (alice, bob),
                [{"op": "remove", "path": "members", "value": [{"value": str(alice.pk)}]}],
                {bob},
            ),
            (
                "remove-by-path-filter-legacy-id",
                (alice, bob),
                [{"op": "remove", "path": member_filter_path(alice.pk)}],
                {bob},
            ),
            (
                "remove-by-path-filter-opaque-scim-id",
                (alice, bob),
                [{"op": "remove", "path": member_filter_path(alice.scim_id)}],
                {bob},
            ),
            (
                "remove-by-path-filter-single-quoted",
                (alice, bob),
                [{"op": "remove", "path": member_filter_path(alice.pk, quote="'")}],
                {bob},
            ),
            (
                "remove-by-path-filter-unquoted",
                (alice, bob),
                [{"op": "remove", "path": member_filter_path(alice.pk, quote="")}],
                {bob},
            ),
            ("empty-remove-clears-every-member", (alice, bob), [{"op": "remove", "path": "members"}], set()),
            (
                "remove-with-empty-value-list-is-a-noop",
                (alice,),
                [{"op": "remove", "path": "members", "value": []}],
                {alice},
            ),
            (
                "remove-of-an-unknown-principal-is-a-noop",
                (alice,),
                [{"op": "remove", "path": member_filter_path(self.outsider.pk)}],
                {alice},
            ),
            # --- replace ---------------------------------------------------------------
            (
                "replace-swaps-the-whole-member-set",
                (alice,),
                [{"op": "replace", "path": "members", "value": [{"value": str(bob.pk)}]}],
                {bob},
            ),
            (
                "replace-with-empty-list-clears",
                (alice, bob),
                [{"op": "replace", "path": "members", "value": []}],
                set(),
            ),
            (
                "replace-pathless-nested-members",
                (alice,),
                [{"op": "replace", "value": {"members": [{"value": str(carol.pk)}]}}],
                {carol},
            ),
            # --- mixed operation lists --------------------------------------------------
            (
                "mixed-add-then-filtered-remove-then-add",
                (alice,),
                [
                    add([{"value": str(bob.pk)}]),
                    {"op": "remove", "path": member_filter_path(alice.pk)},
                    add([{"value": str(carol.pk)}]),
                ],
                {bob, carol},
            ),
            (
                "mixed-replace-then-add-then-remove",
                (alice, bob, carol),
                [
                    {"op": "replace", "path": "members", "value": [{"value": str(alice.pk)}]},
                    add([{"value": str(bob.pk)}]),
                    {"op": "remove", "path": member_filter_path(alice.pk)},
                ],
                {bob},
            ),
            (
                "mixed-add-then-clear-then-add",
                (alice,),
                [
                    add([{"value": str(bob.pk)}]),
                    {"op": "remove", "path": "members"},
                    add([{"value": str(carol.pk)}]),
                ],
                {carol},
            ),
            # --- eligibility ------------------------------------------------------------
            (
                "add-skips-every-ineligible-principal",
                (),
                [
                    add(
                        [
                            {"value": str(self.outsider.pk)},
                            {"value": str(self.deprovisioned.pk)},
                            {"value": str(self.rival_staff.pk)},
                            {"value": str(self.plain_tenant_user.pk)},
                            {"value": str(alice.pk)},
                        ]
                    )
                ],
                {alice},
            ),
            (
                "replace-skips-ineligible-and-keeps-the-eligible-member",
                (bob,),
                [
                    {
                        "op": "replace",
                        "path": "members",
                        "value": [{"value": str(self.outsider.pk)}, {"value": str(alice.pk)}],
                    }
                ],
                {alice},
            ),
        )

    def test_group_member_operations_converge_and_replay_idempotently(self):
        for label, initial, operations, expected_users in self.member_contract_rows():
            with self.subTest(contract=label):
                group = self.make_group(f"Contract {label}", members=initial)
                expected_ids = {user.pk for user in expected_users}

                self.apply_operations(group, operations)
                self.assertEqual(self.scim_member_ids(group), expected_ids)

                rows = self.rows_by_user(group)
                # No duplicate rows, and every row this surface wrote carries SCIM
                # provenance plus the opaque identifier and the acting principal.
                self.assertEqual(set(rows), expected_ids)
                for user in expected_users:
                    row = rows[user.pk]
                    self.assertEqual(row.source, GroupMembership.SOURCE_SCIM)
                    self.assertEqual(row.external_id, str(user.scim_id))
                    self.assertEqual(row.added_by, self.actor)
                self.assertFalse(RoleGrant.objects.filter(user_group=group).exists())

                first_pass = {user_id: row.pk for user_id, row in rows.items()}

                # Idempotent replay: identical PATCH, identical outcome, same rows.
                self.apply_operations(group, operations)
                replayed = self.rows_by_user(group)
                self.assertEqual(self.scim_member_ids(group), expected_ids)
                self.assertEqual({user_id: row.pk for user_id, row in replayed.items()}, first_pass)
                self.assertEqual(
                    GroupMembership.objects.filter(user_group=group).count(),
                    len(expected_ids),
                )
                self.assertFalse(RoleGrant.objects.filter(user_group=group).exists())

    def test_group_member_operations_are_rejected_explicitly_without_mutation(self):
        """Unknown operations and malformed values raise ``SCIMPatchError`` — never a
        silent no-op — and never leave a partially applied group behind."""
        group = self.make_group("Rejection fixture", members=(self.alice, self.bob))
        member = [{"value": str(self.carol.pk)}]
        valid_add = {"op": "add", "path": "members", "value": member}

        cases = (
            # --- unknown operation names ------------------------------------------------
            ("unknown-op-merge", [{"op": "merge", "path": "members", "value": member}], "Unsupported SCIM PATCH op"),
            ("unknown-op-move", [{"op": "move", "path": "members", "value": member}], "Unsupported SCIM PATCH op"),
            ("unknown-op-delete", [{"op": "delete", "path": "members"}], "Unsupported SCIM PATCH op"),
            ("unknown-op-uppercase", [{"op": "MOVE", "path": "members"}], "Unsupported SCIM PATCH op"),
            ("unknown-op-empty-string", [{"op": "", "path": "members"}], "Unsupported SCIM PATCH op"),
            ("unknown-op-null", [{"op": None, "path": "members"}], "Unsupported SCIM PATCH op"),
            ("unknown-op-non-string", [{"op": 42, "path": "members"}], "Unsupported SCIM PATCH op"),
            ("missing-op", [{"path": "members", "value": member}], "Unsupported SCIM PATCH op"),
            (
                "valid-operation-followed-by-unknown-op",
                [valid_add, {"op": "merge", "path": "members", "value": member}],
                "Unsupported SCIM PATCH op",
            ),
            # --- malformed operation lists ----------------------------------------------
            ("operations-not-a-list", "add-everything", "Operations must be a list"),
            ("operations-empty", [], "Operations must not be empty"),
            ("operation-not-an-object", [["add", "members"]], "Operation 0 must be an object"),
            # --- malformed member values -------------------------------------------------
            (
                "member-value-not-an-identifier",
                [{"op": "add", "path": "members", "value": [{"value": "not-an-id"}]}],
                "Invalid member ID",
            ),
            (
                "member-value-boolean",
                [{"op": "add", "path": "members", "value": [{"value": True}]}],
                "Invalid member ID",
            ),
            (
                "member-value-fractional",
                [{"op": "add", "path": "members", "value": [{"value": 1.5}]}],
                "Invalid member ID",
            ),
            (
                "member-value-zero",
                [{"op": "add", "path": "members", "value": [{"value": "0"}]}],
                "Invalid member ID",
            ),
            (
                "member-value-negative",
                [{"op": "add", "path": "members", "value": [{"value": -3}]}],
                "Invalid member ID",
            ),
            ("member-value-null", [{"op": "add", "path": "members", "value": [{"value": None}]}], "Invalid member ID"),
            ("member-object-without-value", [{"op": "add", "path": "members", "value": [{}]}], "Invalid member ID"),
            (
                "member-array-is-a-bare-string",
                [{"op": "add", "path": "members", "value": "5"}],
                "Invalid member values",
            ),
            ("member-array-is-a-number", [{"op": "replace", "path": "members", "value": 5}], "Invalid member values"),
            (
                "member-object-with-unsupported-key",
                [{"op": "add", "path": "members", "value": [{"value": str(self.carol.pk), "role": "owner"}]}],
                "Unsupported SCIM PATCH member keys",
            ),
            (
                "add-with-null-value",
                [{"op": "add", "path": "members", "value": None}],
                "add operation requires a non-null value",
            ),
            (
                "replace-without-value",
                [{"op": "replace", "path": "members"}],
                "replace operation requires a non-null value",
            ),
            # --- malformed paths ----------------------------------------------------------
            ("remove-without-path", [{"op": "remove"}], "remove operation requires a path"),
            ("remove-with-unsupported-path", [{"op": "remove", "path": "description"}], "Unsupported remove path"),
            (
                "remove-with-unsupported-member-filter",
                [{"op": "remove", "path": 'members[id eq "1"]'}],
                "Invalid member path",
            ),
            (
                "remove-filter-with-malformed-id",
                [{"op": "remove", "path": 'members[value eq "not-an-id"]'}],
                "Invalid member ID",
            ),
            (
                "unsupported-sub-attribute-path",
                [{"op": "replace", "path": "members.value", "value": member}],
                "Unsupported SCIM PATCH path",
            ),
            (
                "blank-path",
                [{"op": "add", "path": "   ", "value": member}],
                "path must be a non-empty string",
            ),
        )

        for label, operations, expected_message in cases:
            with self.subTest(rejection=label):
                before = self.group_snapshot(group)
                with self.assertRaises(SCIMPatchError) as context:
                    self.apply_operations(group, operations)
                self.assertIn(expected_message, str(context.exception))
                self.assertEqual(context.exception.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(self.group_snapshot(group), before)

        # The whole batch left the original membership exactly as it was.
        self.assertEqual(self.scim_member_ids(group), {self.alice.pk, self.bob.pk})

    def test_remove_without_a_path_reports_the_no_target_scim_type(self):
        group = self.make_group("No target", members=(self.alice,))
        with self.assertRaises(SCIMPatchError) as context:
            self.apply_operations(group, [{"op": "remove"}])
        self.assertEqual(context.exception.scim_type, "noTarget")
        self.assertEqual(self.scim_member_ids(group), {self.alice.pk})

    def test_member_operations_roll_back_when_a_later_group_attribute_conflicts(self):
        """A mixed operation list is one transaction: a 409 on ``displayName`` must not
        leave the member delta applied."""
        UserGroup.objects.create(tenant=self.provider, name="Taken name")
        group = self.make_group("Atomic group", members=(self.alice,))

        with self.assertRaises(SCIMPatchError) as context:
            self.apply_operations(
                group,
                [
                    {"op": "add", "path": "members", "value": [{"value": str(self.bob.pk)}]},
                    {"op": "replace", "path": "displayName", "value": "Taken name"},
                ],
            )
        self.assertEqual(context.exception.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(context.exception.scim_type, "uniqueness")

        group.refresh_from_db()
        self.assertEqual(group.name, "Atomic group")
        self.assertEqual(self.scim_member_ids(group), {self.alice.pk})

    def test_unknown_operation_is_an_explicit_scim_error_over_http(self):
        """The HTTP surface must surface an unknown op as a 400 SCIM error document —
        never a 200 that silently ignored the operation."""
        group = self.make_group("HTTP rejection", members=(self.alice,))
        detail_url = reverse(
            "api:provider_scim:group-detail",
            kwargs={"provider_slug": self.provider.slug, "pk": group.pk},
        )
        for operation_name in ("merge", "move", "copy", "DELETE"):
            with self.subTest(op=operation_name):
                response = self.client.patch(
                    detail_url,
                    data={
                        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                        "Operations": [
                            {
                                "op": operation_name,
                                "path": "members",
                                "value": [{"value": str(self.bob.pk)}],
                            }
                        ],
                    },
                    content_type="application/json",
                    **self.auth_headers,
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                body = response.json()
                self.assertEqual(body["schemas"], ["urn:ietf:params:scim:api:messages:2.0:Error"])
                self.assertEqual(body["status"], "400")
                self.assertIn("Unsupported SCIM PATCH operation", body["detail"])
                self.assertEqual(self.scim_member_ids(group), {self.alice.pk})


class ProviderGroupMembershipProvenanceTests(ProviderGroupContractMixin, TestCase):
    """SCIM reconciles the rows SCIM owns, and only those.

    A ``GroupMembership`` written by another provisioning domain (manual, LDAP,
    OIDC, SAML) is not SCIM's to delete or rewrite, no matter which operation the
    IdP sends.
    """

    def setUp(self):
        super().setUp()
        self.manual_user = self.make_staff("provenance-manual")
        self.ldap_user = self.make_staff("provenance-ldap")
        self.oidc_user = self.make_staff("provenance-oidc")
        self.saml_user = self.make_staff("provenance-saml")
        self.scim_user = self.make_staff("provenance-scim")

    def build_mixed_provenance_group(self, name):
        group = UserGroup.objects.create(tenant=self.provider, name=name)
        foreign_rows = (
            self.add_row(
                group,
                self.manual_user,
                GroupMembership.SOURCE_MANUAL,
                external_id="manual-record",
                added_by=self.readonly_user,
            ),
            self.add_row(group, self.ldap_user, GroupMembership.SOURCE_LDAP, external_id="ldap-record"),
            self.add_row(group, self.oidc_user, GroupMembership.SOURCE_OIDC, external_id="oidc-record"),
            self.add_row(group, self.saml_user, GroupMembership.SOURCE_SAML, external_id="saml-record"),
        )
        scim_row = self.add_row(
            group,
            self.scim_user,
            GroupMembership.SOURCE_SCIM,
            external_id=str(self.scim_user.scim_id),
            added_by=self.actor,
        )
        return group, foreign_rows, scim_row

    def assert_rows_untouched(self, rows):
        for row in rows:
            with self.subTest(source=row.source):
                stored = GroupMembership.objects.get(pk=row.pk)
                self.assertEqual(stored.source, row.source)
                self.assertEqual(stored.external_id, row.external_id)
                self.assertEqual(stored.added_by_id, row.added_by_id)
                self.assertEqual(stored.membership_id, row.membership_id)
                self.assertEqual(stored.added_at, row.added_at)

    def test_scim_operations_reconcile_only_scim_rows(self):
        operations_by_label = {
            "replace": [
                {"op": "replace", "path": "members", "value": [{"value": str(self.alice.pk)}]},
            ],
            "empty-remove-clear": [{"op": "remove", "path": "members"}],
            "remove-by-value-list": [
                {"op": "remove", "path": "members", "value": [{"value": str(self.scim_user.pk)}]},
            ],
            "remove-by-path-filter": [
                {"op": "remove", "path": member_filter_path(self.scim_user.scim_id)},
            ],
        }
        expected_scim_members = {
            "replace": {self.alice.pk},
            "empty-remove-clear": set(),
            "remove-by-value-list": set(),
            "remove-by-path-filter": set(),
        }

        for label, operations in operations_by_label.items():
            with self.subTest(operation=label):
                group, foreign_rows, scim_row = self.build_mixed_provenance_group(f"Provenance {label}")

                self.apply_operations(group, operations)

                # The SCIM-owned row was reconciled away…
                self.assertFalse(GroupMembership.objects.filter(pk=scim_row.pk).exists())
                # …and every foreign-provenance row survived byte-identically.
                self.assert_rows_untouched(foreign_rows)
                self.assertEqual(self.scim_member_ids(group), expected_scim_members[label])
                self.assertEqual(
                    GroupMembership.objects.filter(user_group=group).count(),
                    len(foreign_rows) + len(expected_scim_members[label]),
                )

    def test_scim_cannot_delete_a_manually_managed_row_for_a_targeted_user(self):
        """A filtered remove naming a user whose row is manual removes nothing: SCIM's
        reach is the provenance it owns, not the principal it names."""
        group = UserGroup.objects.create(tenant=self.provider, name="Manual guard")
        manual_row = self.add_row(
            group,
            self.alice,
            GroupMembership.SOURCE_MANUAL,
            external_id="manual-alice",
            added_by=self.readonly_user,
        )
        self.apply_operations(group, [{"op": "add", "path": "members", "value": [{"value": str(self.bob.pk)}]}])

        self.apply_operations(group, [{"op": "remove", "path": member_filter_path(self.alice.pk)}])
        self.assert_rows_untouched([manual_row])
        self.assertEqual(self.scim_member_ids(group), {self.bob.pk})

        # SCIM may still remove the row SCIM created.
        self.apply_operations(group, [{"op": "remove", "path": member_filter_path(self.bob.pk)}])
        self.assert_rows_untouched([manual_row])
        self.assertEqual(self.scim_member_ids(group), set())
        self.assertEqual(GroupMembership.objects.filter(user_group=group).count(), 1)

    def test_scim_add_never_rewrites_an_existing_foreign_row(self):
        """Adding a principal who is already a member via another source must not
        re-source, re-stamp, or duplicate that row."""
        group = UserGroup.objects.create(tenant=self.provider, name="No re-sourcing")
        manual_row = self.add_row(
            group,
            self.alice,
            GroupMembership.SOURCE_MANUAL,
            external_id="manual-alice",
            added_by=self.readonly_user,
        )

        self.apply_operations(group, [{"op": "add", "path": "members", "value": [{"value": str(self.alice.pk)}]}])

        self.assert_rows_untouched([manual_row])
        self.assertEqual(GroupMembership.objects.filter(user_group=group).count(), 1)
        self.assertEqual(self.scim_member_ids(group), set())

    def test_replay_never_deletes_unrelated_provenance(self):
        group, foreign_rows, scim_row = self.build_mixed_provenance_group("Replay provenance")
        operations = [
            {
                "op": "replace",
                "path": "members",
                "value": [{"value": str(self.alice.pk)}, {"value": str(self.bob.pk)}],
            }
        ]

        self.apply_operations(group, operations)
        first_pass = self.rows_by_user(group)

        for replay in range(2):
            with self.subTest(replay=replay):
                self.apply_operations(group, operations)
                self.assert_rows_untouched(foreign_rows)
                self.assertEqual(self.scim_member_ids(group), {self.alice.pk, self.bob.pk})
                self.assertEqual(
                    {user_id: row.pk for user_id, row in self.rows_by_user(group).items()},
                    {user_id: row.pk for user_id, row in first_pass.items()},
                )
                self.assertFalse(GroupMembership.objects.filter(pk=scim_row.pk).exists())

    def test_display_name_only_patch_leaves_every_membership_untouched(self):
        group, foreign_rows, scim_row = self.build_mixed_provenance_group("Rename only")

        self.apply_operations(group, [{"op": "replace", "path": "displayName", "value": "Renamed only"}])

        group.refresh_from_db()
        self.assertEqual(group.name, "Renamed only")
        self.assert_rows_untouched([*foreign_rows, scim_row])
        self.assertEqual(GroupMembership.objects.filter(user_group=group).count(), 5)

    def test_scim_sync_does_not_reach_into_another_providers_group(self):
        """A user who is staff of two providers keeps the membership the other provider
        manages when this provider clears its own group."""
        shared_user = self.make_staff("shared-across-providers")
        Membership.objects.create(user=shared_user, tenant=self.other_provider, is_active=True)
        rival_group = UserGroup.objects.create(tenant=self.other_provider, name="Rival group")
        rival_row = self.add_row(
            rival_group,
            shared_user,
            GroupMembership.SOURCE_SCIM,
            external_id=str(shared_user.scim_id),
        )
        group = self.make_group("Local group", members=(shared_user,))

        self.apply_operations(group, [{"op": "remove", "path": "members"}])

        self.assertEqual(self.scim_member_ids(group), set())
        self.assert_rows_untouched([rival_row])


class ProviderGroupProvisioningEscalationTests(ProviderGroupContractMixin, TestCase):
    """Negative contracts: the SCIM group surface may never widen anyone's authority,
    reach into a tenant it does not own, or confirm that a foreign resource exists."""

    def build_privileged_group(self, name, permissions=("assets.delete_asset",)):
        role = Role.objects.create(
            tenant=self.provider,
            name=f"Inherited role for {name}",
            permissions=list(permissions),
        )
        group = UserGroup.objects.create(tenant=self.provider, name=name)
        role_grant = RoleGrant.objects.create(user_group=group, role=role)
        RoleGrantScope.objects.create(role_grant=role_grant, scope_type=RoleGrantScope.SCOPE_OWN)
        return group

    def test_each_service_entry_point_requires_its_exact_permission(self):
        group = self.make_group("Permission fixture", members=(self.alice,))
        plain_group = UserGroup.objects.create(tenant=self.plain_tenant, name="Plain tenant group")
        patch = parse_group_patch_operations([{"op": "add", "path": "members", "value": [{"value": str(self.bob.pk)}]}])

        cases = (
            (
                "sync-without-group-permission",
                lambda: sync_provider_group_members(self.provider, group, [self.bob.pk], actor=self.identity_only_user),
                status.HTTP_403_FORBIDDEN,
                PERM_CHANGE_GROUP,
            ),
            (
                "patch-without-group-permission",
                lambda: apply_provider_group_patch(self.provider, group, patch, actor=self.identity_only_user),
                status.HTTP_403_FORBIDDEN,
                PERM_CHANGE_GROUP,
            ),
            (
                "sync-without-membership-permission",
                lambda: sync_provider_group_members(self.provider, group, [self.bob.pk], actor=self.group_only_user),
                status.HTTP_403_FORBIDDEN,
                PERM_CHANGE_MEMBERSHIP,
            ),
            (
                "patch-without-membership-permission",
                lambda: apply_provider_group_patch(self.provider, group, patch, actor=self.group_only_user),
                status.HTTP_403_FORBIDDEN,
                PERM_CHANGE_MEMBERSHIP,
            ),
            (
                "create-without-add-permission",
                lambda: create_provider_group(self.provider, "Denied create", [self.bob.pk], actor=self.readonly_user),
                status.HTTP_403_FORBIDDEN,
                PERM_ADD_GROUP,
            ),
            (
                "create-without-membership-permission",
                lambda: create_provider_group(
                    self.provider, "Denied members", [self.bob.pk], actor=self.group_only_user
                ),
                status.HTTP_403_FORBIDDEN,
                PERM_CHANGE_MEMBERSHIP,
            ),
            (
                "sync-with-anonymous-actor",
                lambda: sync_provider_group_members(self.provider, group, [self.bob.pk], actor=AnonymousUser()),
                status.HTTP_401_UNAUTHORIZED,
                "authenticated actor",
            ),
            (
                "sync-without-actor",
                lambda: sync_provider_group_members(self.provider, group, [self.bob.pk], actor=None),
                status.HTTP_401_UNAUTHORIZED,
                "authenticated actor",
            ),
            (
                "sync-against-a-non-provider-tenant",
                lambda: sync_provider_group_members(self.plain_tenant, plain_group, [], actor=self.actor),
                status.HTTP_403_FORBIDDEN,
                "requires a provider tenant",
            ),
        )

        for label, call, expected_status, expected_fragment in cases:
            with self.subTest(entry_point=label):
                with self.assertRaises(SCIMPatchError) as context:
                    call()
                self.assertEqual(context.exception.status_code, expected_status)
                self.assertIn(expected_fragment, str(context.exception))
                # Nothing was written by a refused call.
                self.assertEqual(self.scim_member_ids(group), {self.alice.pk})
                self.assertFalse(GroupMembership.objects.filter(user_group=plain_group).exists())

        # The refused create rolled its group back too.
        self.assertFalse(UserGroup.objects.filter(name__startswith="Denied").exists())

    def test_role_grant_escalation_through_member_sync_is_blocked(self):
        """Joining a role-bearing group inherits that role. The canonical guard must
        refuse when the actor does not itself hold the inherited permissions."""
        group = self.build_privileged_group("Privileged target")

        with self.assertRaises(ValidationError) as context:
            sync_provider_group_members(self.provider, group, [self.alice.pk], actor=self.actor)
        self.assertIn("Privilege escalation detected", str(context.exception))
        self.assertEqual(GroupMembership.objects.filter(user_group=group).count(), 0)

    def test_escalation_rolls_back_group_attributes_applied_earlier_in_the_patch(self):
        group = self.build_privileged_group("Privileged rollback")

        with self.assertRaises(ValidationError):
            self.apply_operations(
                group,
                [
                    {"op": "replace", "path": "displayName", "value": "Must roll back"},
                    {"op": "add", "path": "members", "value": [{"value": str(self.alice.pk)}]},
                ],
            )

        group.refresh_from_db()
        self.assertEqual(group.name, "Privileged rollback")
        self.assertFalse(GroupMembership.objects.filter(user_group=group).exists())

    def test_removing_a_member_from_a_privileged_group_is_permitted(self):
        """The guard exists to stop *widening* authority. Narrowing it — removing a
        member from a role-bearing group — must stay available to the IdP."""
        group = self.build_privileged_group("Privileged narrowing")
        membership = Membership.objects.get(user=self.alice, tenant=self.provider)
        GroupMembership.objects.create(
            user_group=group,
            membership=membership,
            source=GroupMembership.SOURCE_SCIM,
            external_id=str(self.alice.scim_id),
            added_by=self.actor,
        )

        self.apply_operations(group, [{"op": "remove", "path": "members"}])

        self.assertEqual(self.scim_member_ids(group), set())

    def test_actor_holding_the_inherited_authority_may_add_members(self):
        """The guard checks authority, not the mere presence of a RoleGrant: an actor who
        already holds the inherited permission is allowed through."""
        peer_role = Role.objects.create(
            tenant=self.provider,
            name="Equal Authority",
            permissions=[
                PERM_CHANGE_MEMBERSHIP,
                PERM_VIEW_GROUP,
                PERM_ADD_GROUP,
                PERM_CHANGE_GROUP,
                "assets.delete_asset",
            ],
        )
        peer_actor = User.objects.create_user(username="equal-authority", email="equal@msp.test")
        grant(peer_actor, self.provider, peer_role)
        group = self.build_privileged_group("Privileged peer")

        sync_provider_group_members(self.provider, group, [self.alice.pk], actor=peer_actor)

        self.assertEqual(self.scim_member_ids(group), {self.alice.pk})
        self.assertEqual(
            GroupMembership.objects.get(user_group=group).added_by,
            peer_actor,
        )

    def test_superuser_bypasses_the_escalation_guard_but_not_tenant_ownership(self):
        root = User.objects.create_superuser(username="contract-root", email="root@msp.test", password="x")
        group = self.build_privileged_group("Privileged superuser")
        foreign_group = UserGroup.objects.create(tenant=self.other_provider, name="Rival owned")

        sync_provider_group_members(self.provider, group, [self.alice.pk], actor=root)
        self.assertEqual(self.scim_member_ids(group), {self.alice.pk})

        with self.assertRaises(SCIMPatchError) as context:
            sync_provider_group_members(self.provider, foreign_group, [self.alice.pk], actor=root)
        self.assertEqual(context.exception.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(GroupMembership.objects.filter(user_group=foreign_group).exists())

    def test_scim_provisioning_never_creates_a_role_grant(self):
        group = create_provider_group(
            self.provider,
            "Identity only provisioning",
            [self.alice.pk],
            actor=self.actor,
        )
        self.apply_operations(group, [{"op": "add", "path": "members", "value": [{"value": str(self.bob.pk)}]}])
        self.apply_operations(group, [{"op": "add", "path": "members", "value": [{"value": str(self.bob.pk)}]}])

        self.assertEqual(self.scim_member_ids(group), {self.alice.pk, self.bob.pk})
        self.assertFalse(RoleGrant.objects.filter(user_group=group).exists())
        self.assertFalse(RoleGrant.objects.filter(membership__user__in=[self.alice, self.bob]).exists())

    def test_a_group_owned_by_another_provider_is_refused_without_a_leak(self):
        foreign_group = UserGroup.objects.create(tenant=self.other_provider, name="Rival secret group")
        deleted_group = UserGroup.objects.create(tenant=self.provider, name="Deleted group")
        deleted_group.delete()
        patch = parse_group_patch_operations(
            [{"op": "add", "path": "members", "value": [{"value": str(self.alice.pk)}]}]
        )

        with self.assertRaises(SCIMPatchError) as sync_context:
            sync_provider_group_members(self.provider, foreign_group, [self.alice.pk], actor=self.actor)
        self.assertEqual(sync_context.exception.status_code, status.HTTP_403_FORBIDDEN)

        with self.assertRaises(SCIMPatchError) as foreign_context:
            apply_provider_group_patch(self.provider, foreign_group, patch, actor=self.actor)
        with self.assertRaises(SCIMPatchError) as deleted_context:
            apply_provider_group_patch(self.provider, deleted_group, patch, actor=self.actor)

        # A group owned by someone else is indistinguishable from one that never
        # existed here: same status, same wording, no name or slug echoed back.
        self.assertEqual(foreign_context.exception.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(str(foreign_context.exception), str(deleted_context.exception))
        for leaked in (foreign_group.name, str(foreign_group.pk), self.other_provider.slug):
            self.assertNotIn(leaked, str(foreign_context.exception))
        self.assertFalse(GroupMembership.objects.filter(user_group=foreign_group).exists())

    def test_http_patch_of_a_foreign_group_matches_an_unknown_group(self):
        foreign_group = UserGroup.objects.create(tenant=self.other_provider, name="Rival http group")
        payload = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "add", "path": "members", "value": [{"value": str(self.alice.pk)}]}],
        }
        foreign_url = reverse(
            "api:provider_scim:group-detail",
            kwargs={"provider_slug": self.provider.slug, "pk": foreign_group.scim_id},
        )
        unknown_url = reverse(
            "api:provider_scim:group-detail",
            kwargs={"provider_slug": self.provider.slug, "pk": "3f1a5f36-4b0f-4a1a-9a1f-6a2d5c8e7b01"},
        )

        foreign_response = self.client.patch(
            foreign_url, data=payload, content_type="application/json", **self.auth_headers
        )
        unknown_response = self.client.patch(
            unknown_url, data=payload, content_type="application/json", **self.auth_headers
        )

        self.assertEqual(foreign_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(unknown_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(foreign_response.json(), unknown_response.json())
        self.assertNotIn(foreign_group.name, str(foreign_response.json()))
        self.assertFalse(GroupMembership.objects.filter(user_group=foreign_group).exists())

    def test_identifiers_that_are_not_provider_users_are_never_resolved(self):
        """Group identifiers, unknown UUIDs and principals belonging to other tenants are
        skipped — a members array is not a lookup oracle."""
        foreign_group = UserGroup.objects.create(tenant=self.other_provider, name="Rival identifier source")
        local_group = UserGroup.objects.create(tenant=self.provider, name="Identifier probe target")
        group = self.make_group("Identifier probe")

        self.apply_operations(
            group,
            [
                {
                    "op": "add",
                    "path": "members",
                    "value": [
                        {"value": str(foreign_group.scim_id)},
                        {"value": str(local_group.scim_id)},
                        {"value": "8a0f0b62-6b6e-4f2f-9c66-2f6b1f5f0a55"},
                        {"value": str(self.rival_staff.pk)},
                        {"value": str(self.rival_staff.scim_id)},
                        {"value": str(self.plain_tenant_user.pk)},
                        {"value": str(self.outsider.pk)},
                        {"value": str(self.deprovisioned.pk)},
                        {"value": str(self.alice.pk)},
                    ],
                }
            ],
        )

        self.assertEqual(self.scim_member_ids(group), {self.alice.pk})
        self.assertEqual(GroupMembership.objects.filter(user_group=group).count(), 1)
        self.assertFalse(GroupMembership.objects.filter(user_group=foreign_group).exists())
        self.assertFalse(GroupMembership.objects.filter(user_group=local_group).exists())
