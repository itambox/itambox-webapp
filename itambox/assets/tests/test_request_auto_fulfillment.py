"""Issue #492 — one physical assignment fulfils exactly one requested unit."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from assets.choices import RequestStatusChoices
from assets.models import Asset, AssetRequest, AssetRole, AssetType, Manufacturer, StatusLabel
from assets.services import checkout_asset
from core.context import _current_user
from core.managers import (
    get_current_all_accessible,
    get_current_tenant,
    get_current_tenant_group,
    set_current_all_accessible,
    set_current_membership,
    set_current_tenant,
    set_current_tenant_group,
)
from organization.models import AssetHolder, Location, Membership, Site, Tenant, TenantGroup

User = get_user_model()


@contextmanager
def _scope(setter, getter, value):
    old = getter()
    setter(value)
    try:
        yield
    finally:
        setter(old)


@contextmanager
def _all_accessible_scope(user):
    old_flag = get_current_all_accessible()
    old_tenant = get_current_tenant()
    old_group = get_current_tenant_group()
    old_user = _current_user.get()
    set_current_tenant(None)
    set_current_tenant_group(None)
    set_current_membership(None)
    set_current_all_accessible(True)
    _current_user.set(user)
    try:
        yield
    finally:
        set_current_all_accessible(old_flag)
        set_current_tenant(old_tenant)
        set_current_tenant_group(old_group)
        set_current_membership(None)
        _current_user.set(old_user)


@pytest.fixture
def world():
    tenant = Tenant.objects.create(name="Acme", slug="acme492")
    admin = User.objects.create_user(username="op492", is_staff=True, is_superuser=True)
    requester = User.objects.create_user(username="req492")
    delegatee = User.objects.create_user(username="del492")
    other = User.objects.create_user(username="oth492")

    manufacturer = Manufacturer.objects.create(name="Lenovo492", slug="lenovo492")
    role = AssetRole.objects.create(name="Laptop492", slug="laptop492")
    deployable = StatusLabel.objects.create(
        name="Deployable492", slug="deployable492", type=StatusLabel.TYPE_DEPLOYABLE
    )
    deployed = StatusLabel.objects.create(name="Deployed492", slug="deployed492", type=StatusLabel.TYPE_DEPLOYED)
    asset_type = AssetType.objects.create(
        manufacturer=manufacturer, model="T14-492", slug="t14-492", requestable=True, asset_role=role
    )
    other_type = AssetType.objects.create(
        manufacturer=manufacturer, model="T16-492", slug="t16-492", requestable=True, asset_role=role
    )

    def make_asset(name, tag, atype=None, for_tenant=None):
        return Asset.objects.create(
            name=name,
            asset_tag=tag,
            asset_type=atype or asset_type,
            asset_role=role,
            status=deployable,
            requestable=True,
            tenant=for_tenant or tenant,
        )

    def make_holder(user, for_tenant=None):
        return AssetHolder.objects.create(
            user=user,
            first_name="Hold",
            last_name=user.username,
            upn=f"{user.username}@example.com",
            tenant=for_tenant or tenant,
        )

    def make_membership(user, for_tenant=None):
        Membership.objects.get_or_create(user=user, tenant=for_tenant or tenant)

    holder = make_holder(requester)
    delegatee_holder = make_holder(delegatee)
    other_holder = make_holder(other)

    def make_request(user, atype=None, asset=None, status=RequestStatusChoices.APPROVED, assigned_user=None):
        request_row = AssetRequest(
            requester=user,
            asset_type=atype,
            asset=asset,
            status=status,
            assigned_user=assigned_user,
            tenant=tenant,
        )
        request_row._skip_duplicate_check = True
        request_row.save()
        return request_row

    def make_group(user, atype, qty, assigned_user=None, child_status=RequestStatusChoices.APPROVED):
        parent = AssetRequest(
            requester=user,
            asset_type=atype,
            qty=qty,
            is_group=True,
            status=RequestStatusChoices.APPROVED,
            assigned_user=assigned_user,
            tenant=tenant,
        )
        parent._skip_duplicate_check = True
        parent.save()
        children = []
        for _ in range(qty):
            child = AssetRequest(
                requester=user,
                asset_type=atype,
                qty=1,
                parent=parent,
                status=child_status,
                assigned_user=assigned_user,
                tenant=tenant,
            )
            child._skip_duplicate_check = True
            child.save()
            children.append(child)
        return parent, children

    return SimpleNamespace(
        tenant=tenant,
        admin=admin,
        requester=requester,
        delegatee=delegatee,
        other=other,
        holder=holder,
        delegatee_holder=delegatee_holder,
        other_holder=other_holder,
        asset_type=asset_type,
        other_type=other_type,
        deployable=deployable,
        deployed=deployed,
        make_asset=make_asset,
        make_holder=make_holder,
        make_membership=make_membership,
        make_request=make_request,
        make_group=make_group,
    )


@pytest.mark.django_db
def test_group_request_partially_fulfilled_one_unit_per_checkout(world):
    parent, children = world.make_group(world.requester, world.asset_type, qty=3)
    assets = [
        world.make_asset("T14-A", "TAG-A"),
        world.make_asset("T14-B", "TAG-B"),
        world.make_asset("T14-C", "TAG-C"),
    ]

    checkout_asset(asset=assets[0], holder=world.holder, user=world.admin)

    states = [AssetRequest.objects.get(pk=c.pk).status for c in children]
    assert states.count(RequestStatusChoices.FULFILLED) == 1
    parent.refresh_from_db()
    assert parent.status == RequestStatusChoices.APPROVED

    checkout_asset(asset=assets[1], holder=world.holder, user=world.admin)
    states = [AssetRequest.objects.get(pk=c.pk).status for c in children]
    assert states.count(RequestStatusChoices.FULFILLED) == 2
    parent.refresh_from_db()
    assert parent.status == RequestStatusChoices.APPROVED

    checkout_asset(asset=assets[2], holder=world.holder, user=world.admin)
    states = [AssetRequest.objects.get(pk=c.pk).status for c in children]
    assert states == [RequestStatusChoices.FULFILLED] * 3
    parent.refresh_from_db()
    assert parent.status == RequestStatusChoices.FULFILLED


@pytest.mark.django_db
@pytest.mark.parametrize(
    "terminal_status, child_status",
    [
        (RequestStatusChoices.DENIED, RequestStatusChoices.PENDING),
        (RequestStatusChoices.CANCELLED, RequestStatusChoices.APPROVED),
    ],
)
def test_group_parent_fulfilled_when_remaining_children_are_terminal(world, terminal_status, child_status):
    parent, children = world.make_group(world.requester, world.asset_type, qty=3, child_status=child_status)
    children[0].status = terminal_status
    children[0].save(update_fields=["status"])
    for child in children[1:]:
        if child.status == RequestStatusChoices.PENDING:
            child.status = RequestStatusChoices.APPROVED
            child.save(update_fields=["status"])
    suffix = terminal_status.replace("-", "_")
    assets = [
        world.make_asset(f"T14-T-{suffix}-1", f"TAG-T-{suffix}-1"),
        world.make_asset(f"T14-T-{suffix}-2", f"TAG-T-{suffix}-2"),
    ]

    checkout_asset(asset=assets[0], holder=world.holder, user=world.admin)
    checkout_asset(asset=assets[1], holder=world.holder, user=world.admin)

    parent.refresh_from_db()
    assert parent.status == RequestStatusChoices.FULFILLED


@pytest.mark.django_db
def test_oldest_matching_request_unit_wins(world):
    first = world.make_request(world.requester, atype=world.asset_type)
    second = world.make_request(world.requester, atype=world.asset_type)
    asset = world.make_asset("T14-OLD", "TAG-OLD")

    checkout_asset(asset=asset, holder=world.holder, user=world.admin)

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == RequestStatusChoices.FULFILLED
    assert second.status == RequestStatusChoices.APPROVED


@pytest.mark.django_db
def test_asset_specific_and_type_requests_are_not_both_fulfilled(world):
    asset = world.make_asset("T14-SPEC", "TAG-SPEC")
    specific = world.make_request(world.requester, asset=asset)
    generic = world.make_request(world.requester, atype=world.asset_type)
    other_asset = world.make_asset("T14-GEN", "TAG-GEN")

    checkout_asset(asset=asset, holder=world.holder, user=world.admin)

    specific.refresh_from_db()
    generic.refresh_from_db()
    assert specific.status == RequestStatusChoices.FULFILLED
    assert generic.status == RequestStatusChoices.APPROVED

    checkout_asset(asset=other_asset, holder=world.holder, user=world.admin)
    generic.refresh_from_db()
    assert generic.status == RequestStatusChoices.FULFILLED


@pytest.mark.django_db
def test_delegated_request_fulfilled_for_assigned_user(world):
    request_row = world.make_request(world.requester, atype=world.asset_type, assigned_user=world.delegatee_holder)
    asset = world.make_asset("T14-DEL", "TAG-DEL")

    checkout_asset(asset=asset, holder=world.delegatee_holder, user=world.admin)

    request_row.refresh_from_db()
    assert request_row.status == RequestStatusChoices.FULFILLED
    assert request_row.responded_by == world.admin
    assert request_row.response_date is not None
    assert "Automatically fulfilled" in request_row.response_notes


@pytest.mark.django_db
def test_checkout_to_other_holder_does_not_fulfill_delegated_or_own_requests(world):
    delegated = world.make_request(world.requester, atype=world.asset_type, assigned_user=world.delegatee_holder)
    own = world.make_request(world.requester, atype=world.asset_type)
    asset = world.make_asset("T14-WRONG", "TAG-WRONG")

    checkout_asset(asset=asset, holder=world.other_holder, user=world.admin)

    delegated.refresh_from_db()
    own.refresh_from_db()
    assert delegated.status == RequestStatusChoices.APPROVED
    assert own.status == RequestStatusChoices.APPROVED


@pytest.mark.django_db
def test_procurement_request_is_not_fulfilled_by_plain_assignment(world):
    request_row = world.make_request(world.requester, atype=world.asset_type, status=RequestStatusChoices.PROCUREMENT)
    asset = world.make_asset("T14-PROC", "TAG-PROC")

    checkout_asset(asset=asset, holder=world.holder, user=world.admin)

    request_row.refresh_from_db()
    assert request_row.status == RequestStatusChoices.PROCUREMENT


@pytest.mark.django_db
def test_location_targeted_request_is_not_fulfilled_by_holder_checkout(world):
    site = Site.objects.create(name="HQ492", slug="hq492", tenant=world.tenant)
    location = Location.objects.create(name="Staging492", slug="staging492", site=site, tenant=world.tenant)
    request_row = AssetRequest(
        requester=world.requester,
        asset_type=world.asset_type,
        status=RequestStatusChoices.APPROVED,
        assigned_location=location,
        tenant=world.tenant,
    )
    request_row._skip_duplicate_check = True
    request_row.save()
    asset = world.make_asset("T14-LOC", "TAG-LOC")

    checkout_asset(asset=asset, holder=world.holder, user=world.admin)

    request_row.refresh_from_db()
    assert request_row.status == RequestStatusChoices.APPROVED


@pytest.mark.django_db
def test_audit_fields_recorded_on_auto_fulfillment(world):
    request_row = world.make_request(world.requester, atype=world.asset_type)
    asset = world.make_asset("T14-AUDIT", "TAG-AUDIT")

    checkout_asset(asset=asset, holder=world.holder, user=world.admin)

    request_row.refresh_from_db()
    assert request_row.status == RequestStatusChoices.FULFILLED
    assert request_row.responded_by == world.admin
    assert request_row.response_date <= timezone.now()
    assert "assignment checkout transaction ID:" in request_row.response_notes


@pytest.mark.django_db
def test_single_tenant_scope_only_fulfils_requests_of_the_active_tenant(world):
    other_tenant = Tenant.objects.create(name="OtherCorp", slug="other492")
    other_holder = world.make_holder(world.requester, for_tenant=other_tenant)
    other_asset = world.make_asset("T14-T2", "TAG-T2", for_tenant=other_tenant)
    other_request = AssetRequest(
        requester=world.requester,
        asset_type=world.asset_type,
        status=RequestStatusChoices.APPROVED,
        tenant=other_tenant,
    )
    other_request._skip_duplicate_check = True
    other_request.save()
    own_request = world.make_request(world.requester, atype=world.asset_type)
    own_asset = world.make_asset("T14-T1", "TAG-T1")

    with _scope(set_current_tenant, get_current_tenant, other_tenant):
        checkout_asset(asset=other_asset, holder=other_holder, user=world.admin)

    other_request.refresh_from_db()
    assert other_request.status == RequestStatusChoices.FULFILLED
    own_request.refresh_from_db()
    assert own_request.status == RequestStatusChoices.APPROVED

    with _scope(set_current_tenant, get_current_tenant, world.tenant):
        checkout_asset(asset=own_asset, holder=world.holder, user=world.admin)

    own_request.refresh_from_db()
    assert own_request.status == RequestStatusChoices.FULFILLED


@pytest.mark.django_db
def test_group_scope_does_not_fulfil_requests_outside_group(world):
    group = TenantGroup.objects.create(name="Group492", slug="group492")
    inside = Tenant.objects.create(name="Inside", slug="inside492", group=group)
    outside = Tenant.objects.create(name="Outside", slug="outside492")
    inside_holder = world.make_holder(world.requester, for_tenant=inside)
    outside_request = AssetRequest(
        requester=world.requester,
        asset_type=world.asset_type,
        status=RequestStatusChoices.APPROVED,
        tenant=outside,
    )
    outside_request._skip_duplicate_check = True
    outside_request.save()
    inside_asset = world.make_asset("T14-IN", "TAG-IN", for_tenant=inside)

    with _scope(set_current_tenant_group, get_current_tenant_group, group):
        checkout_asset(asset=inside_asset, holder=inside_holder, user=world.admin)

    outside_request.refresh_from_db()
    assert outside_request.status == RequestStatusChoices.APPROVED


@pytest.mark.django_db
def test_all_accessible_scope_fulfils_exactly_one_unit(world):
    second_tenant = Tenant.objects.create(name="SecondCorp", slug="second492")
    second_holder = world.make_holder(world.requester, for_tenant=second_tenant)
    world.make_membership(world.requester, for_tenant=world.tenant)
    world.make_membership(world.requester, for_tenant=second_tenant)
    first_request = world.make_request(world.requester, atype=world.asset_type)
    second_request = AssetRequest(
        requester=world.requester,
        asset_type=world.asset_type,
        status=RequestStatusChoices.APPROVED,
        tenant=second_tenant,
    )
    second_request._skip_duplicate_check = True
    second_request.save()
    asset = world.make_asset("T14-AA", "TAG-AA", for_tenant=second_tenant)

    with _all_accessible_scope(world.requester):
        checkout_asset(asset=asset, holder=second_holder, user=world.admin)

    first_request.refresh_from_db()
    second_request.refresh_from_db()
    fulfilled = [r.status for r in (first_request, second_request)].count(RequestStatusChoices.FULFILLED)
    assert fulfilled == 1
