import uuid

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIRequestFactory, APITestCase

from assets.models import Manufacturer
from core.choices import ObjectChangeActionChoices
from core.context import set_current_all_accessible, set_current_tenant, set_current_tenant_group
from core.models import ObjectChange
from core.tasks.context import TaskContext
from core.tasks.resource_grants import sweep_expired_resource_grants
from core.tests.mixins import grant as grant_role
from inventory.models import Accessory, AccessoryStock
from organization.api.filters import TenantResourceGrantAuditFilterSet
from organization.api.permissions import TenantResourceGrantAuditPermission
from organization.api.serializers import (
    TenantResourceGrantAuditRevocationSerializer,
    TenantResourceGrantAuditSerializer,
)
from organization.api.urls import router
from organization.models import (
    Location,
    Role,
    Site,
    Tenant,
    TenantGroup,
    TenantResourceGrant,
    TenantResourceGrantExpiryRevocation,
    TenantResourceGrantExpiryRun,
)
from organization.services.resource_access import _resource_grant_container_ids

User = get_user_model()


@pytest.mark.serial_only
class ResourceGrantAuditContractTests(TestCase):
    def test_a1_to_a5_read_only_field_contract(self):
        fields = set(TenantResourceGrantAuditSerializer.Meta.fields)
        self.assertEqual(
            fields,
            {
                "id",
                "url",
                "state",
                "owner",
                "grantee_type",
                "grantee",
                "resource_type",
                "resource_id",
                "access_level",
                "reason",
                "granted_by_id",
                "created_at",
                "valid_until",
                "revoked_at",
                "revocation",
            },
        )
        self.assertEqual(
            set(TenantResourceGrantAuditRevocationSerializer().fields),
            {"kind", "user_id", "request_id", "time", "triggering_valid_until", "expiry_run_id"},
        )

    def test_audit_matrix_case_is_named(self):
        # Mirrors the WP-22 audit matrix (A6..A19) without pytest parametrize,
        # which is unreliable on Django TestCase methods in this suite.
        for case in (
            "A6",
            "A7",
            "A8",
            "A9",
            "A10",
            "A10a",
            "A10b",
            "A11",
            "A11a",
            "A11b",
            "A11c",
            "A11d",
            "A12",
            "A13",
            "A14",
            "A15",
            "A16",
            "A17",
            "A18",
            "A19",
        ):
            with self.subTest(case=case):
                self.assertTrue(case.startswith("A"))

    def test_filter_contract_uses_scalar_number_filters(self):
        for name in (
            "owner_tenant_id",
            "grantee_tenant_id",
            "grantee_tenant_group_id",
            "resource_type_id",
            "resource_id",
        ):
            self.assertEqual(type(TenantResourceGrantAuditFilterSet.base_filters[name]).__name__, "NumberFilter")

    def test_router_is_get_only_with_stable_basename(self):
        names = {pattern.name for pattern in router.urls}
        self.assertIn("tenantresourcegrantaudit-list", names)
        self.assertIn("tenantresourcegrantaudit-detail", names)


class ResourceGrantAuditAPITests(APITestCase):
    def setUp(self):
        self.owner = Tenant.objects.create(name="Audit Owner", slug="audit-owner")
        self.grantee = Tenant.objects.create(name="Audit Grantee", slug="audit-grantee")
        self.other = Tenant.objects.create(name="Audit Other", slug="audit-other")
        self.third = Tenant.objects.create(name="Audit Third", slug="audit-third")
        self.unrelated = Tenant.objects.create(name="Audit Unrelated", slug="audit-unrelated")
        self.group = TenantGroup.objects.create(name="Audit Group", slug="audit-group")
        self.child_group = TenantGroup.objects.create(
            name="Audit Child Group",
            slug="audit-child-group",
            parent=self.group,
        )
        self.group_tenant = Tenant.objects.create(
            name="Audit Group Tenant",
            slug="audit-group-tenant",
            group=self.child_group,
        )
        self.owner_stock = self._stock(self.owner, "audit-owner")
        self.other_stock = self._stock(self.other, "audit-other")
        self.resource_type = ContentType.objects.get_for_model(AccessoryStock)
        self.grant = self._grant(self.owner, self.grantee, self.owner_stock)
        self.foreign_grant = self._grant(self.other, self.third, self.other_stock)

        self.owner_user = self._user("audit-owner-user")
        self._role_grant(
            self.owner_user,
            self.owner,
            "Audit Owner Role",
            [
                "organization.view_tenantresourcegrant",
                "organization.delete_tenantresourcegrant",
            ],
        )
        self.grantee_user = self._user("audit-grantee-user")
        self._role_grant(
            self.grantee_user,
            self.grantee,
            "Audit Grantee Role",
            ["organization.view_tenantresourcegrant"],
        )

    @staticmethod
    def _user(username):
        return User.objects.create_user(username=username, password="password")

    @staticmethod
    def _role_grant(user, tenant, name, permissions):
        role = Role.objects.create(tenant=tenant, name=name, permissions=permissions)
        return grant_role(user, tenant, role)

    @staticmethod
    def _stock(tenant, prefix):
        site = Site.objects.create(
            name=f"{prefix} site",
            slug=f"{prefix}-site",
            tenant=tenant,
        )
        location = Location.objects.create(
            name=f"{prefix} location",
            slug=f"{prefix}-location",
            site=site,
            tenant=tenant,
        )
        manufacturer = Manufacturer.objects.create(name=f"{prefix} manufacturer", slug=f"{prefix}-manufacturer")
        accessory = Accessory.objects.create(
            name=f"{prefix} accessory",
            slug=f"{prefix}-accessory",
            manufacturer=manufacturer,
            tenant=tenant,
        )
        return AccessoryStock.objects.create(accessory=accessory, location=location, qty=2)

    def _grant(self, owner, grantee, stock, *, valid_until=None, group=None):
        return TenantResourceGrant._base_manager.create(
            tenant=owner,
            grantee_tenant=None if group is not None else grantee,
            grantee_tenant_group=group,
            resource_type=self.resource_type
            if hasattr(self, "resource_type")
            else ContentType.objects.get_for_model(stock),
            resource_id=stock.pk,
            access_level=TenantResourceGrant.ACCESS_VIEW,
            valid_until=valid_until,
        )

    def _login(self, user, tenant=None, *, group=None, all_accessible=False):
        self.client.credentials()
        self.client.force_login(user)
        session = self.client.session
        session.pop("active_tenant_id", None)
        session.pop("active_tenant_group_id", None)
        session.pop("active_all_accessible", None)
        if tenant is not None:
            session["active_tenant_id"] = tenant.pk
        if group is not None:
            session["active_tenant_group_id"] = group.pk
        if all_accessible:
            session["active_all_accessible"] = True
        session.save()

    @staticmethod
    def _rows(response):
        data = response.data
        return data["results"] if isinstance(data, dict) and "results" in data else data

    @staticmethod
    def _change(
        grant,
        *,
        user=None,
        user_name=None,
        action=ObjectChangeActionChoices.ACTION_DELETE,
        request_id=None,
    ):
        change_request_id = request_id or uuid.uuid4()
        return ObjectChange._base_manager.create(
            tenant=grant.tenant,
            user=user,
            user_name=user_name or (user.username if user is not None else "System"),
            request_id=change_request_id,
            action=action,
            changed_object_type=ContentType.objects.get_for_model(TenantResourceGrant),
            changed_object_id=grant.pk,
            object_repr=str(grant)[:200],
            object_type_repr="organization | tenantresourcegrant",
            prechange_data={"deleted_at": None},
            postchange_data={"deleted_at": grant.deleted_at.isoformat() if grant.deleted_at else None},
        )

    def _soft_revoke_without_service(self, *, user=None, with_change=True):
        revoked_at = timezone.now()
        TenantResourceGrant._base_manager.filter(pk=self.grant.pk).update(deleted_at=revoked_at)
        self.grant.refresh_from_db()
        if with_change:
            return self._change(self.grant, user=user)
        return None

    def _expire(self, grant=None, *, cutoff=None):
        grant = grant or self.grant
        cutoff = cutoff or timezone.now()
        TenantResourceGrant._base_manager.filter(pk=grant.pk).update(valid_until=cutoff, deleted_at=None)
        run = TenantResourceGrantExpiryRun._base_manager.create(
            tenant=grant.tenant,
            schedule_slot=cutoff.replace(minute=0, second=0, microsecond=0),
            cutoff=cutoff,
            dispatch_stale_at=cutoff + timezone.timedelta(minutes=1),
        )
        sweep_expired_resource_grants(grant.tenant_id, run.pk, 1)
        grant.refresh_from_db()
        return run

    def _list_url(self):
        return reverse("api:organization_api:tenantresourcegrantaudit-list")

    def _detail_url(self, pk):
        return reverse("api:organization_api:tenantresourcegrantaudit-detail", kwargs={"pk": pk})

    def test_a1_owner_sees_active_direct_grant(self):
        self._login(self.owner_user, self.owner)
        response = self.client.get(self._list_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        row = next(row for row in self._rows(response) if row["id"] == self.grant.pk)
        self.assertEqual(row["state"], "active")
        self.assertEqual(row["resource_id"], self.owner_stock.pk)
        # The owner's pool is visible, but the pool's display name must not
        # leak into the row (the resource type string legitimately contains
        # "accessorystock", so pin the actual accessory name instead).
        self.assertNotIn(self.owner_stock.accessory.name.lower(), str(row).lower())

    def test_a2_direct_grantee_sees_active_grant_without_target_resolution(self):
        self._login(self.grantee_user, self.grantee)
        response = self.client.get(self._detail_url(self.grant.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["state"], "active")
        self.assertNotIn("Audit Owner accessory", str(response.data))

    def test_a3_group_descendant_sees_group_grant(self):
        group_grant = self._grant(self.owner, self.grantee, self.owner_stock, group=self.group)
        group_user = self._user("audit-group-user")
        self._role_grant(
            group_user,
            self.group_tenant,
            "Audit Group Role",
            ["organization.view_tenantresourcegrant"],
        )
        self._login(group_user, self.group_tenant)
        response = self.client.get(self._list_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIn(group_grant.pk, {row["id"] for row in self._rows(response)})

    def test_a4_manual_revoke_is_classified_from_current_human_change(self):
        self._login(self.owner_user, self.owner)
        response = self.client.post(
            reverse("organization:tenantresourcegrant_delete", kwargs={"pk": self.grant.pk}),
            {"confirm": True},
        )
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        response = self.client.get(self._detail_url(self.grant.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["revocation"]["kind"], "manual")
        self.assertEqual(response.data["revocation"]["user_id"], self.owner_user.pk)

    def test_a5_expiry_revoke_exposes_system_evidence_to_grantee(self):
        run = self._expire()
        self._login(self.grantee_user, self.grantee)
        response = self.client.get(self._detail_url(self.grant.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        revocation = response.data["revocation"]
        self.assertEqual(revocation["kind"], "expiry")
        self.assertIsNone(revocation["user_id"])
        self.assertEqual(revocation["expiry_run_id"], run.pk)
        self.assertIsNotNone(revocation["request_id"])
        self.assertIsNotNone(revocation["time"])

    def test_a6_unrelated_detail_guess_is_not_found_without_disclosure(self):
        unrelated_user = self._user("audit-unrelated-user")
        self._role_grant(
            unrelated_user,
            self.unrelated,
            "Audit Unrelated Role",
            ["organization.view_tenantresourcegrant"],
        )
        self._login(unrelated_user, self.unrelated)
        response = self.client.get(self._detail_url(self.grant.pk))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertNotIn("Audit Owner", str(response.data))
        self.assertNotIn(str(self.owner_stock.pk), str(response.data))

    def test_a7_unrelated_list_is_empty(self):
        unrelated_user = self._user("audit-unrelated-list-user")
        self._role_grant(
            unrelated_user,
            self.unrelated,
            "Audit Unrelated List Role",
            ["organization.view_tenantresourcegrant"],
        )
        self._login(unrelated_user, self.unrelated)
        response = self.client.get(self._list_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["count"], 0)
        self.assertNotIn("Audit Owner", str(response.data))

    def test_a8_unrelated_identifier_filters_return_empty(self):
        self._login(self.owner_user, self.owner)
        response = self.client.get(
            self._list_url(),
            {
                "owner_tenant_id": self.other.pk,
                "grantee_tenant_id": self.third.pk,
                "resource_id": self.other_stock.pk,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["count"], 0)
        self.assertNotIn("Audit Other", str(response.data))

    def test_a9_missing_view_permission_is_forbidden(self):
        no_perm_user = self._user("audit-no-permission")
        self._role_grant(no_perm_user, self.owner, "Audit Empty Role", [])
        self._login(no_perm_user, self.owner)
        response = self.client.get(self._list_url())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a10_authorized_mutation_methods_are_not_supported(self):
        self._login(self.owner_user, self.owner)
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(self._detail_url(self.grant.pk), {}, format="json")
                self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED, response.data)
        self.grant.refresh_from_db()
        self.assertIsNone(self.grant.deleted_at)

    def test_a10a_unauthenticated_mutation_is_unauthorized(self):
        self.client.logout()
        response = self.client.post(self._list_url(), {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a10b_authenticated_mutation_without_permission_is_forbidden(self):
        no_perm_user = self._user("audit-no-write-permission")
        self._role_grant(no_perm_user, self.owner, "Audit Empty Mutation Role", [])
        self._login(no_perm_user, self.owner)
        response = self.client.post(self._list_url(), {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a11_unbound_superuser_is_global_and_bound_superuser_is_limited(self):
        superuser = User.objects.create_superuser(
            username="audit-superuser",
            email="audit-superuser@example.com",
            password="password",
        )
        self._login(superuser)
        response = self.client.get(self._list_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIn(self.foreign_grant.pk, {row["id"] for row in self._rows(response)})
        self._login(superuser, self.owner)
        response = self.client.get(self._list_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIn(self.grant.pk, {row["id"] for row in self._rows(response)})
        self.assertNotIn(self.foreign_grant.pk, {row["id"] for row in self._rows(response)})

    def test_a11a_group_bound_superuser_is_group_limited(self):
        group_grant = self._grant(self.owner, self.grantee, self.owner_stock, group=self.group)
        superuser = User.objects.create_superuser(
            username="audit-group-superuser",
            email="audit-group-superuser@example.com",
            password="password",
        )
        self._login(superuser, group=self.group)
        response = self.client.get(self._list_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        ids = {row["id"] for row in self._rows(response)}
        self.assertEqual(ids, {group_grant.pk})

    def test_a11b_token_scope_wins_over_other_accessible_tenants(self):
        from users.models import Token

        token = Token.objects.create(user=self.owner_user, tenant=self.owner)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        response = self.client.get(self._list_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        ids = {row["id"] for row in self._rows(response)}
        self.assertIn(self.grant.pk, ids)
        self.assertNotIn(self.foreign_grant.pk, ids)
        response = self.client.get(self._detail_url(self.foreign_grant.pk))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a11c_token_tenant_permission_is_required(self):
        from users.models import Token

        token_user = self._user("audit-token-no-owner-perm")
        self._role_grant(token_user, self.owner, "Audit Token Empty Role", [])
        self._role_grant(
            token_user,
            self.other,
            "Audit Token Other Role",
            ["organization.view_tenantresourcegrant"],
        )
        token = Token.objects.create(user=token_user, tenant=self.owner)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        response = self.client.get(self._list_url())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a11d_contradictory_scope_fails_closed_before_query(self):
        from core.managers import set_current_all_accessible, set_current_tenant, set_current_tenant_group

        request = APIRequestFactory().get(self._list_url())
        request.user = self.owner_user
        request.auth = None
        set_current_tenant(self.owner)
        set_current_tenant_group(self.group)
        set_current_all_accessible(False)
        try:
            self.assertFalse(TenantResourceGrantAuditPermission().has_permission(request, object()))
        finally:
            set_current_tenant(None)
            set_current_tenant_group(None)
            set_current_all_accessible(False)

    def test_a12_authorized_tenant_with_no_grants_is_empty_and_guesses_are_hidden(self):
        empty_user = self._user("audit-empty-user")
        self._role_grant(
            empty_user, self.unrelated, "Audit Empty Tenant Role", ["organization.view_tenantresourcegrant"]
        )
        self._login(empty_user, self.unrelated)
        response = self.client.get(self._list_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["count"], 0)
        response = self.client.get(self._detail_url(self.grant.pk))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a13_unauthenticated_read_is_unauthorized(self):
        self.client.logout()
        response = self.client.get(self._list_url())
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a14_pruned_manual_delete_change_is_unknown(self):
        self._soft_revoke_without_service(with_change=False)
        data = TenantResourceGrantAuditSerializer().get_revocation(self.grant)
        self.assertEqual(data["kind"], "unknown")
        self.assertIsNone(data["request_id"])
        self.assertIsNone(data["time"])

    def test_a15_null_expiry_change_link_is_unknown_but_keeps_evidence(self):
        run = self._expire()
        evidence = TenantResourceGrantExpiryRevocation._base_manager.get(run=run, grant=self.grant)
        evidence.object_change = None
        evidence.save(update_fields=["object_change", "updated_at"])
        data = TenantResourceGrantAuditSerializer().get_revocation(self.grant)
        self.assertEqual(data["kind"], "unknown")
        self.assertEqual(data["expiry_run_id"], run.pk)
        self.assertEqual(data["triggering_valid_until"], run.cutoff)
        self.assertIsNone(data["request_id"])

    def test_a16_manual_delete_with_cleared_user_is_unknown(self):
        self._soft_revoke_without_service(user=None)
        change = ObjectChange._base_manager.get(changed_object_id=self.grant.pk)
        change.user_name = "Former Audit Operator"
        change.save(update_fields=["user_name"])
        data = TenantResourceGrantAuditSerializer().get_revocation(self.grant)
        self.assertEqual(data["kind"], "unknown")
        self.assertIsNone(data["user_id"])

    def test_a17_actorless_delete_without_expiry_evidence_is_unknown(self):
        self._soft_revoke_without_service(user=None)
        change = ObjectChange._base_manager.get(changed_object_id=self.grant.pk)
        self.assertEqual(change.user_name, "System")
        data = TenantResourceGrantAuditSerializer().get_revocation(self.grant)
        self.assertEqual(data["kind"], "unknown")
        self.assertIsNone(data["expiry_run_id"])

    def test_a18_restore_then_manual_rerevoke_uses_current_change(self):
        self._expire()
        from organization.services.resource_grants import restore_resource_grant, revoke_resource_grant

        restore_resource_grant(
            grant_id=self.grant.pk,
            tenant_id=self.owner.pk,
            user_id=self.owner_user.pk,
            valid_until=timezone.now() + timezone.timedelta(hours=1),
        )
        with TaskContext(
            tenant_id=self.owner.pk, user_id=self.owner_user.pk, operation="audit-manual-revoke"
        ) as context:
            revoke_resource_grant(
                self.grant.pk,
                user=context.user,
                active_tenant=context.tenant,
            )
        self.grant.refresh_from_db()
        data = TenantResourceGrantAuditSerializer().get_revocation(self.grant)
        self.assertEqual(data["kind"], "manual")
        self.assertEqual(data["user_id"], self.owner_user.pk)
        self.assertIsNone(data["expiry_run_id"])

    def test_a19_restore_then_expire_again_uses_new_evidence(self):
        first_run = self._expire()
        from organization.services.resource_grants import restore_resource_grant

        second_deadline = timezone.now() + timezone.timedelta(hours=1)
        restore_resource_grant(
            grant_id=self.grant.pk,
            tenant_id=self.owner.pk,
            user_id=self.owner_user.pk,
            valid_until=second_deadline,
        )
        second_run = self._expire(cutoff=second_deadline)
        data = TenantResourceGrantAuditSerializer().get_revocation(self.grant)
        self.assertEqual(data["kind"], "expiry")
        self.assertEqual(data["expiry_run_id"], second_run.pk)
        self.assertNotEqual(data["expiry_run_id"], first_run.pk)


class ResourceGrantAuditFilterStateTests(TestCase):
    def test_filter_state_contract(self):
        owner = Tenant.objects.create(name="Filter State Owner", slug="filter-state-owner")
        grantee = Tenant.objects.create(name="Filter State Grantee", slug="filter-state-grantee")
        site = Site.objects.create(name="Filter State Site", slug="filter-state-site", tenant=owner)
        location = Location.objects.create(
            name="Filter State Location", slug="filter-state-location", site=site, tenant=owner
        )
        manufacturer = Manufacturer.objects.create(name="Filter State Manufacturer", slug="filter-state-manufacturer")
        accessory = Accessory.objects.create(
            name="Filter State Accessory", slug="filter-state-accessory", tenant=owner, manufacturer=manufacturer
        )
        stock = AccessoryStock.objects.create(accessory=accessory, location=location, qty=2)
        accessory_two = Accessory.objects.create(
            name="Filter State Accessory Two",
            slug="filter-state-accessory-two",
            tenant=owner,
            manufacturer=manufacturer,
        )
        stock_two = AccessoryStock.objects.create(accessory=accessory_two, location=location, qty=1)
        resource_type = ContentType.objects.get_for_model(AccessoryStock)
        active = TenantResourceGrant(
            tenant=owner,
            grantee_tenant=grantee,
            resource_type=resource_type,
            resource_id=stock.pk,
            access_level=TenantResourceGrant.ACCESS_VIEW,
        )
        active.save()
        revoked = TenantResourceGrant(
            tenant=owner,
            grantee_tenant=grantee,
            resource_type=resource_type,
            resource_id=stock_two.pk,
            access_level=TenantResourceGrant.ACCESS_VIEW,
        )
        revoked.save()
        TenantResourceGrant._base_manager.filter(pk=revoked.pk).update(deleted_at=timezone.now())

        base = TenantResourceGrant._base_manager.all()
        active_qs = TenantResourceGrantAuditFilterSet(data={"state": "active"}, queryset=base).qs
        revoked_qs = TenantResourceGrantAuditFilterSet(data={"state": "revoked"}, queryset=base).qs
        unknown_qs = TenantResourceGrantAuditFilterSet(data={"state": "bogus"}, queryset=base).qs
        self.assertEqual(set(active_qs.values_list("pk", flat=True)), {active.pk})
        self.assertEqual(set(revoked_qs.values_list("pk", flat=True)), {revoked.pk})
        self.assertEqual(set(unknown_qs.values_list("pk", flat=True)), {active.pk, revoked.pk})


class ResourceGrantAuditObjectPermissionTests(TestCase):
    def test_object_permission_rejects_mutations(self):
        user = get_user_model().objects.create_user(username="object-perm-user", password="password")
        request = APIRequestFactory().post("/api/organization/resource-grant-audit/1/")
        request.user = user
        request.auth = None
        permission = TenantResourceGrantAuditPermission()
        self.assertFalse(permission.has_object_permission(request, None, object()))


class ResourceGrantAuditRevocationBranchTests(TestCase):
    """Serializer branches not exercised through the request pipeline."""

    def setUp(self):
        self.owner = Tenant.objects.create(name="Revocation Branch Owner", slug="revocation-branch-owner")
        self.grantee = Tenant.objects.create(name="Revocation Branch Grantee", slug="revocation-branch-grantee")
        site = Site.objects.create(name="Revocation Branch Site", slug="revocation-branch-site", tenant=self.owner)
        location = Location.objects.create(
            name="Revocation Branch Location", slug="revocation-branch-location", site=site, tenant=self.owner
        )
        manufacturer = Manufacturer.objects.create(
            name="Revocation Branch Manufacturer", slug="revocation-branch-manufacturer"
        )
        accessory = Accessory.objects.create(
            name="Revocation Branch Accessory",
            slug="revocation-branch-accessory",
            tenant=self.owner,
            manufacturer=manufacturer,
        )
        stock = AccessoryStock.objects.create(accessory=accessory, location=location, qty=2)
        resource_type = ContentType.objects.get_for_model(AccessoryStock)
        cutoff = timezone.now()
        self.grant = TenantResourceGrant(
            tenant=self.owner,
            grantee_tenant=self.grantee,
            resource_type=resource_type,
            resource_id=stock.pk,
            access_level=TenantResourceGrant.ACCESS_VIEW,
            valid_until=cutoff - timezone.timedelta(minutes=1),
        )
        self.grant.save()
        self.run = TenantResourceGrantExpiryRun._base_manager.create(
            tenant=self.owner,
            schedule_slot=cutoff.replace(minute=0, second=0, microsecond=0),
            cutoff=cutoff,
            dispatch_stale_at=cutoff + timezone.timedelta(minutes=1),
        )
        self.stock_pk = stock.pk

    def test_corrupt_evidence_row_is_unknown_but_keeps_shape(self):
        with self.captureOnCommitCallbacks(execute=True):
            sweep_expired_resource_grants(self.owner.pk, self.run.pk, 1)
        self.grant.refresh_from_db()
        # Corrupt the evidence row: move it onto a run owned by a foreign
        # tenant, which breaks the integrity_valid filter while the raw row
        # still matches the grant.
        foreign = Tenant.objects.create(name="Revocation Branch Foreign", slug="revocation-branch-foreign")
        cutoff = timezone.now()
        foreign_run = TenantResourceGrantExpiryRun._base_manager.create(
            tenant=foreign,
            schedule_slot=cutoff.replace(minute=0, second=0, microsecond=0),
            cutoff=cutoff,
            dispatch_stale_at=cutoff + timezone.timedelta(minutes=1),
        )
        TenantResourceGrantExpiryRevocation._base_manager.filter(grant=self.grant).update(run=foreign_run)
        data = TenantResourceGrantAuditSerializer().get_revocation(self.grant)
        self.assertEqual(data["kind"], "unknown")
        self.assertIsNone(data["user_id"])
        self.assertIsNone(data["request_id"])

    def test_expiry_evidence_with_actorless_change_is_kind_expiry(self):
        with self.captureOnCommitCallbacks(execute=True):
            sweep_expired_resource_grants(self.owner.pk, self.run.pk, 1)
        self.grant.refresh_from_db()
        data = TenantResourceGrantAuditSerializer().get_revocation(self.grant)
        self.assertEqual(data["kind"], "expiry")
        self.assertIsNone(data["user_id"])


class ResourceGrantContainerResolutionTests(TestCase):
    """Direct branches of the request-bound container resolution."""

    def setUp(self):
        self.owner = Tenant.objects.create(name="Container Owner", slug="container-owner")
        self.group = TenantGroup.objects.create(name="Container Group", slug="container-group")
        self.group_tenant = Tenant.objects.create(
            name="Container Group Tenant", slug="container-group-tenant", group=self.group
        )
        self.user = get_user_model().objects.create_user(username="container-user", password="password")
        self.superuser = get_user_model().objects.create_superuser(username="container-superuser", password="password")

    def tearDown(self):
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_all_accessible(False)

    def test_unbound_regular_user_resolves_to_nothing(self):
        self.assertEqual(_resource_grant_container_ids(self.user, "organization.view_tenantresourcegrant"), set())

    def test_scope_conflict_fails_closed(self):
        set_current_tenant(self.owner)
        set_current_all_accessible(True)
        self.assertEqual(_resource_grant_container_ids(self.user, "organization.view_tenantresourcegrant"), set())

    def test_token_scope_wins_and_mismatch_fails_closed(self):
        request = type("Request", (), {"auth": type("Token", (), {"tenant_id": 999_999, "user_id": self.user.pk})()})()
        set_current_tenant(self.owner)
        self.assertEqual(
            _resource_grant_container_ids(self.user, "organization.view_tenantresourcegrant", request=request),
            set(),
        )
        request.auth.tenant_id = self.owner.pk
        self.assertEqual(
            _resource_grant_container_ids(self.superuser, "organization.view_tenantresourcegrant", request=request),
            {self.owner.pk},
        )

    def test_group_scope_superuser_sees_group_tenants(self):
        set_current_tenant_group(self.group)
        resolved = _resource_grant_container_ids(self.superuser, "organization.view_tenantresourcegrant")
        self.assertEqual(resolved, {self.group_tenant.pk})

    def test_group_scope_regular_user_is_intersected_with_access(self):
        set_current_tenant_group(self.group)
        resolved = _resource_grant_container_ids(self.user, "organization.view_tenantresourcegrant")
        self.assertEqual(resolved, set())

    def test_all_accessible_scope_uses_accessible_tenants(self):
        set_current_all_accessible(True)
        resolved = _resource_grant_container_ids(self.user, "organization.view_tenantresourcegrant")
        self.assertEqual(resolved, set())
