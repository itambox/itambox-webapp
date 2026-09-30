"""PostgreSQL races around SCIM provisioning (epic #571, work packages 2 and 4).

Concurrent IdP calls must not duplicate identities or memberships, a racing
externalId collision must fail closed without hijacking another identity, and
concurrent provider group member operations must converge to a single row, and a racing
deactivate/reactivate pair must leave the global account flag mirroring the surviving
membership state.
These tests run real threads against PostgreSQL (no mocks): the strict shared
parser, the IntegrityError retry paths and the group row lock are exercised
end to end.
"""

from __future__ import annotations

import threading
import time
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import close_old_connections, connection, connections, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.tests.mixins import grant
from organization.models import Membership, Role, Tenant
from users.api.scim.provider_patch import SCIMPatchError
from users.api.scim.provider_views import _lock_provider_scim_user
from users.api.scim.views import _lock_tenant_scim_user
from users.models import GroupMembership, Token, UserGroup

User = get_user_model()

pytestmark = [pytest.mark.serial_only, pytest.mark.django_db(transaction=True)]

JSON = "application/json"


def run_race(worker_count, target):
    """Run ``target(index)`` in ``worker_count`` threads aligned on one barrier."""
    barrier = threading.Barrier(worker_count)
    results, errors = [], []

    def worker(index):
        close_old_connections()
        try:
            barrier.wait(timeout=15)
            results.append((index, target(index)))
        except Exception as error:  # surfaced through the ``errors`` assertion below
            errors.append(error)
        finally:
            connections["default"].close()

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(worker_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert all(not thread.is_alive() for thread in threads), "a race worker never finished"
    assert len(results) == worker_count, f"expected {worker_count} race results, got {len(results)} (errors: {errors})"
    return results, errors


def provision_headers(token):
    return {"HTTP_AUTHORIZATION": f"Bearer {token.key}"}


def tenant_fixture(slug):
    tenant = Tenant.objects.create(name=f"Race {slug}", slug=slug)
    role = Role.objects.create(
        tenant=tenant,
        name="Race provisioner",
        permissions=["organization.change_membership", "users.view_usergroup"],
    )
    actor = User.objects.create_user(username=f"race-actor-{slug}")
    grant(actor, tenant, role)
    token = Token.objects.create(user=actor, tenant=tenant, expires=timezone.now() + timezone.timedelta(days=1))
    return tenant, token


def provider_fixture(slug):
    provider = Tenant.objects.create(name=f"Race {slug}", slug=slug, is_provider=True)
    role = Role.objects.create(
        tenant=provider,
        name="Race provisioner",
        permissions=[
            "organization.change_membership",
            "users.view_usergroup",
            "users.add_usergroup",
            "users.change_usergroup",
            "users.delete_usergroup",
        ],
    )
    actor = User.objects.create_user(username=f"race-actor-{slug}")
    grant(actor, provider, role)
    token = Token.objects.create(user=actor, tenant=provider, expires=timezone.now() + timezone.timedelta(days=1))
    return provider, token


def entra_payload(username, external_id):
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "externalId": external_id,
        "userName": username,
        "name": {"givenName": "Race", "familyName": "User"},
        "emails": [{"value": username, "type": "work", "primary": True}],
        "active": True,
    }


def test_concurrent_tenant_posts_do_not_duplicate_identity_or_membership():
    assert connection.vendor == "postgresql"
    tenant, token = tenant_fixture("race-tenant")
    url = reverse("api:scim:user-list", kwargs={"tenant_slug": tenant.slug})
    username = "race.user@contoso.onmicrosoft.com"
    external_id = "aa1f0000-race-4000-8000-000000000001"
    payload = entra_payload(username, external_id)

    def attempt(_index):
        response = Client().post(url, payload, content_type=JSON, **provision_headers(token))
        return response

    results, errors = run_race(3, attempt)
    assert not errors, errors
    statuses = sorted(response.status_code for _, response in results)
    assert set(statuses) <= {200, 201}, statuses
    # Exactly one request created the identity; its racers converged on it.
    assert statuses.count(201) == 1, statuses

    assert User.objects.filter(username=username).count() == 1
    memberships = Membership.objects.filter(tenant=tenant, external_id=external_id)
    assert memberships.count() == 1
    assert memberships.get().user.username == username


def test_concurrent_provider_posts_do_not_duplicate_identity_or_membership():
    assert connection.vendor == "postgresql"
    provider, token = provider_fixture("race-provider")
    url = reverse("api:provider_scim:user-list", kwargs={"provider_slug": provider.slug})
    username = "race.staff@msp.example"
    external_id = "aa1f0000-race-4000-8000-000000000002"
    payload = entra_payload(username, external_id)

    def attempt(_index):
        response = Client().post(url, payload, content_type=JSON, **provision_headers(token))
        return response

    results, errors = run_race(3, attempt)
    assert not errors, errors
    statuses = sorted(response.status_code for _, response in results)
    assert set(statuses) <= {200, 201}, statuses
    assert statuses.count(201) == 1, statuses

    assert User.objects.filter(username=username).count() == 1
    assert Membership.objects.filter(tenant=provider, external_id=external_id).count() == 1


def test_concurrent_conflicting_external_id_fails_closed_without_hijack():
    """Two concurrent creates reusing one externalId: one wins, the other answers a
    uniqueness conflict, and the losing username never gains a membership."""
    assert connection.vendor == "postgresql"
    tenant, token = tenant_fixture("race-conflict")
    url = reverse("api:scim:user-list", kwargs={"tenant_slug": tenant.slug})
    external_id = "aa1f0000-race-4000-8000-000000000003"

    def attempt(index):
        payload = entra_payload(f"race.conflict.{index}@contoso.onmicrosoft.com", external_id)
        return Client().post(url, payload, content_type=JSON, **provision_headers(token))

    results, errors = run_race(2, attempt)
    assert not errors, errors
    statuses = sorted(response.status_code for _, response in results)
    assert statuses == [201, 409], statuses

    membership = Membership.objects.get(tenant=tenant, external_id=external_id)
    assert membership.user.username.startswith("race.conflict.")
    assert User.objects.filter(username__startswith="race.conflict.").count() == 1
    conflict = next(response for _, response in results if response.status_code == 409)
    assert conflict.json()["scimType"] == "uniqueness"


def test_concurrent_provider_group_member_adds_converge_to_one_row():
    assert connection.vendor == "postgresql"
    provider, token = provider_fixture("race-group")
    staff = User.objects.create_user(username="race-staff", email="race-staff@msp.example")
    Membership.objects.create(user=staff, tenant=provider, is_active=True)
    group = UserGroup.objects.create(tenant=provider, name="Race group")
    url = reverse("api:provider_scim:group-detail", kwargs={"provider_slug": provider.slug, "pk": str(group.scim_id)})
    patch_body = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "add", "path": "members", "value": [{"value": str(staff.scim_id)}]}],
    }

    def attempt(_index):
        response = Client().patch(url, patch_body, content_type=JSON, **provision_headers(token))
        return response

    results, errors = run_race(2, attempt)
    assert not errors, errors
    assert [response.status_code for _, response in results] == [200, 200]

    scim_rows = GroupMembership.objects.filter(user_group=group, source=GroupMembership.SOURCE_SCIM)
    assert scim_rows.count() == 1
    assert scim_rows.get().membership.user_id == staff.pk


def test_concurrent_tenant_deactivate_reactivate_keep_global_flag_consistent():
    """PATCH active=false racing PATCH active=true must always leave ``User.is_active``
    mirroring "has any active membership": each lifecycle mutation reloads the user
    under the same row lock, so a racer can never recompute the mirror from a stale
    in-memory flag. Several rounds raise the probability of hitting the interleaving
    that the pre-fix code lost the race on; with the fix every interleaving passes."""
    assert connection.vendor == "postgresql"
    tenant, token = tenant_fixture("race-lifecycle")
    username = "race.lifecycle@contoso.onmicrosoft.com"
    external_id = "aa1f0000-race-4000-8000-000000000004"
    list_url = reverse("api:scim:user-list", kwargs={"tenant_slug": tenant.slug})
    created = Client().post(
        list_url, entra_payload(username, external_id), content_type=JSON, **provision_headers(token)
    )
    assert created.status_code == 201, created.content
    user = User.objects.get(username=username)
    detail_url = reverse("api:scim:user-detail", kwargs={"tenant_slug": tenant.slug, "pk": str(user.scim_id)})

    def patch_active(active):
        body = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": active}],
        }
        return Client().patch(detail_url, body, content_type=JSON, **provision_headers(token))

    for _round in range(10):
        # Settle on an active state; a racing deactivate then has something to race.
        reset = patch_active(True)
        assert reset.status_code == 200, reset.content

        results, errors = run_race(2, lambda index: patch_active(index == 0))
        assert not errors, errors
        assert sorted(response.status_code for _, response in results) == [200, 200]

        user.refresh_from_db()
        membership = Membership.objects.get(user=user, tenant=tenant)
        any_active = Membership.objects.filter(user=user, is_active=True).exists()
        assert user.is_active == any_active, (membership.is_active, user.is_active)


def test_lock_helpers_fail_closed_when_identity_vanished():
    """A lifecycle request admitted for an identity that disappears before the locked
    reload must fail closed with a SCIM 404; the tenant helper additionally rechecks
    the membership after waiting on the lock."""
    assert connection.vendor == "postgresql"
    tenant, _token = tenant_fixture("race-gone")
    other_tenant = Tenant.objects.create(name="Race gone other", slug="race-gone-other")
    user = User.objects.create_user(username="race.gone@contoso.onmicrosoft.com")
    Membership.objects.create(user=user, tenant=tenant, is_active=True)
    ghost_pk = user.pk
    user.delete()

    with pytest.raises(SCIMPatchError) as excinfo:
        with transaction.atomic():
            _lock_tenant_scim_user(User(pk=ghost_pk), tenant)
    assert excinfo.value.status_code == 404

    survivor = User.objects.create_user(username="race.survivor@contoso.onmicrosoft.com")
    Membership.objects.create(user=survivor, tenant=other_tenant, is_active=True)
    with pytest.raises(SCIMPatchError) as excinfo:
        with transaction.atomic():
            _lock_tenant_scim_user(survivor, tenant)
    assert excinfo.value.status_code == 404

    provider_ghost_pk = User.objects.create_user(username="race.provider-gone@contoso.onmicrosoft.com").pk
    User.objects.filter(pk=provider_ghost_pk).delete()
    with pytest.raises(SCIMPatchError) as excinfo:
        with transaction.atomic():
            _lock_provider_scim_user(User(pk=provider_ghost_pk))
    assert excinfo.value.status_code == 404


def test_tenant_post_retry_reconciles_when_racer_commits_membership_first():
    """Deterministic replay of the POST lost-update race: the competing membership
    lands between the request's lookups and the create, so the create raises
    IntegrityError and the retry path must reconcile the identity under the
    user-row lock (200 replay). Only the two prologue lookups are served pre-race
    snapshots; the retry lookup and the create hit the real database."""
    assert connection.vendor == "postgresql"
    tenant, token = tenant_fixture("race-retry")
    username = "race.retry@contoso.onmicrosoft.com"
    external_id = "aa1f0000-race-4000-8000-000000000005"
    user = User.objects.create_user(username=username)
    Membership.objects.create(user=user, tenant=tenant, is_active=True, external_id=external_id)
    url = reverse("api:scim:user-list", kwargs={"tenant_slug": tenant.slug})

    real_filter = Membership.objects.filter
    real_select_related = Membership.objects.select_related
    retry_lookups = {"count": 0}

    def pre_race_filter(*args, **kwargs):
        queryset = real_filter(*args, **kwargs)
        if "user" in kwargs and "tenant" in kwargs:
            return queryset.none()
        return queryset

    def pre_race_select_related(*args, **kwargs):
        retry_lookups["count"] += 1
        queryset = real_select_related(*args, **kwargs)
        if retry_lookups["count"] == 1:
            return queryset.none()
        return queryset

    with (
        mock.patch.object(Membership.objects, "select_related", new=pre_race_select_related),
        mock.patch.object(Membership.objects, "filter", new=pre_race_filter),
    ):
        response = Client().post(
            url, entra_payload(username, external_id), content_type=JSON, **provision_headers(token)
        )

    assert response.status_code == 200, response.content
    assert retry_lookups["count"] >= 2, "the retry path never ran its correlated lookup"
    assert Membership.objects.filter(user=user, tenant=tenant).count() == 1
    user.refresh_from_db()
    any_active = Membership.objects.filter(user=user, is_active=True).exists()
    assert user.is_active == any_active


def test_provider_post_retry_reconciles_when_racer_commits_membership_first():
    """Provider-mount mirror of the tenant POST retry race: after the IntegrityError,
    the retry sync must run under the same user-row lock (200 replay)."""
    assert connection.vendor == "postgresql"
    provider, token = provider_fixture("race-prov-retry")
    username = "race.retry.provider@contoso.onmicrosoft.com"
    external_id = "aa1f0000-race-4000-8000-000000000006"
    user = User.objects.create_user(username=username)
    Membership.objects.create(user=user, tenant=provider, is_active=True, external_id=external_id)
    url = reverse("api:provider_scim:user-list", kwargs={"provider_slug": provider.slug})

    real_filter = Membership.objects.filter
    real_select_related = Membership.objects.select_related
    retry_lookups = {"count": 0}

    def pre_race_filter(*args, **kwargs):
        queryset = real_filter(*args, **kwargs)
        if "user" in kwargs and "tenant" in kwargs:
            return queryset.none()
        return queryset

    def pre_race_select_related(*args, **kwargs):
        retry_lookups["count"] += 1
        queryset = real_select_related(*args, **kwargs)
        if retry_lookups["count"] == 1:
            return queryset.none()
        return queryset

    with (
        mock.patch.object(Membership.objects, "select_related", new=pre_race_select_related),
        mock.patch.object(Membership.objects, "filter", new=pre_race_filter),
    ):
        response = Client().post(
            url, entra_payload(username, external_id), content_type=JSON, **provision_headers(token)
        )

    assert response.status_code == 200, response.content
    assert retry_lookups["count"] >= 2, "the retry path never ran its correlated lookup"
    assert Membership.objects.filter(user=user, tenant=provider).count() == 1
    user.refresh_from_db()
    any_active = Membership.objects.filter(user=user, is_active=True).exists()
    assert user.is_active == any_active


def test_scim_error_envelope_maps_django_validation_errors():
    """Django-level validation failures bubbling out of a SCIM request must render as
    the SCIM error envelope with 400 (defensive branch shared by both mounts)."""
    assert connection.vendor == "postgresql"
    tenant, token = tenant_fixture("race-validation")
    url = reverse("api:scim:user-list", kwargs={"tenant_slug": tenant.slug})
    payload = entra_payload("race.validation@contoso.onmicrosoft.com", "aa1f0000-race-4000-8000-000000000007")
    with mock.patch(
        "users.api.scim.views.parse_user_resource", side_effect=DjangoValidationError("invalid user document")
    ):
        response = Client().post(url, payload, content_type=JSON, **provision_headers(token))

    assert response.status_code == 400, response.content
    body = response.json()
    assert body["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
    assert body["status"] == "400"


def test_tenant_patch_waits_for_the_user_row_lock_before_mutating():
    """Deterministic serialization proof: while another transaction holds the user's
    row lock, a tenant PATCH must not be able to run to completion (nor mutate the
    membership). The pre-fix code only touched the user row conditionally at the end
    of the sync and completed regardless; the fixed code waits on
    ``select_for_update()`` first, exactly like the provider mount."""
    assert connection.vendor == "postgresql"
    tenant, token = tenant_fixture("race-lock")
    other_tenant = Tenant.objects.create(name="Race lock other", slug="race-lock-other")
    user = User.objects.create_user(username="race.lock@contoso.onmicrosoft.com")
    Membership.objects.create(user=user, tenant=tenant, is_active=True)
    Membership.objects.create(user=user, tenant=other_tenant, is_active=True)
    detail_url = reverse("api:scim:user-detail", kwargs={"tenant_slug": tenant.slug, "pk": str(user.scim_id)})
    patch_body = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "active", "value": False}],
    }

    locked, release = threading.Event(), threading.Event()
    outcome: dict = {}

    def hold_user_row_lock():
        close_old_connections()
        try:
            with transaction.atomic():
                list(User.objects.select_for_update().filter(pk=user.pk))
                locked.set()
                release.wait(timeout=30)
        finally:
            connections["default"].close()

    def run_patch():
        close_old_connections()
        try:
            outcome["response"] = Client().patch(detail_url, patch_body, content_type=JSON, **provision_headers(token))
        finally:
            connections["default"].close()

    holder = threading.Thread(target=hold_user_row_lock, daemon=True)
    holder.start()
    assert locked.wait(timeout=15), "the helper transaction never took the user row lock"

    patcher = threading.Thread(target=run_patch, daemon=True)
    patcher.start()
    time.sleep(2.0)
    assert patcher.is_alive(), "tenant PATCH completed while the user row lock was held"
    assert "response" not in outcome

    release.set()
    patcher.join(timeout=30)
    holder.join(timeout=30)
    assert not patcher.is_alive() and not holder.is_alive()
    response = outcome["response"]
    assert response.status_code == 200, response.content

    membership = Membership.objects.get(user=user, tenant=tenant)
    assert membership.is_active is False
    user.refresh_from_db()
    any_active = Membership.objects.filter(user=user, is_active=True).exists()
    assert user.is_active == any_active
