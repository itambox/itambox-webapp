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

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.tests.mixins import grant
from organization.models import Membership, Role, Tenant
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
