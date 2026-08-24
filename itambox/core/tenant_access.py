"""Typed, domain-blind access boundary for tenant-aware framework code."""

from __future__ import annotations

from collections.abc import Collection, Iterator
from contextlib import contextmanager
from typing import Protocol

from core.provider_slot import SingleProviderSlot


class PrincipalRef(Protocol):
    pk: int
    is_authenticated: bool
    is_superuser: bool


class TenantRef(Protocol):
    pk: int


class MembershipRef(Protocol):
    user_id: int
    tenant_id: int
    is_active: bool
    tenant: TenantRef


class TenantAccessPolicy(Protocol):
    def accessible_tenant_ids(self, user: PrincipalRef | None) -> set[int]:
        pass

    def active_membership(
        self,
        user: PrincipalRef,
        tenant_id: int,
    ) -> MembershipRef | None:
        pass

    def first_active_membership_in(
        self,
        user: PrincipalRef,
        authorized_tenant_ids: Collection[int],
    ) -> MembershipRef | None:
        pass

    def shared_stock_read_allowed(
        self,
        obj: object,
        active_tenant: TenantRef,
        user: PrincipalRef,
        perm: str | None = None,
    ) -> bool:
        pass


_tenant_access_policy = SingleProviderSlot[TenantAccessPolicy]("tenant access policy")


def configure_tenant_access_policy(provider: TenantAccessPolicy) -> None:
    _tenant_access_policy.register(provider)


def get_tenant_access_policy() -> TenantAccessPolicy:
    return _tenant_access_policy.get()


@contextmanager
def override_tenant_access_policy(provider: TenantAccessPolicy) -> Iterator[None]:
    with _tenant_access_policy.override(provider):
        yield


def accessible_tenant_ids(user: PrincipalRef | None) -> set[int]:
    return get_tenant_access_policy().accessible_tenant_ids(user)


def active_membership(user: PrincipalRef, tenant_id: int) -> MembershipRef | None:
    return get_tenant_access_policy().active_membership(user, tenant_id)


def first_active_membership_in(
    user: PrincipalRef,
    authorized_tenant_ids: Collection[int],
) -> MembershipRef | None:
    return get_tenant_access_policy().first_active_membership_in(
        user,
        authorized_tenant_ids,
    )


def shared_stock_read_allowed(
    obj: object,
    active_tenant: TenantRef,
    user: PrincipalRef,
    perm: str | None = None,
) -> bool:
    return get_tenant_access_policy().shared_stock_read_allowed(
        obj,
        active_tenant,
        user,
        perm,
    )
