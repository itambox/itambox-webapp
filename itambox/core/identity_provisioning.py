"""SDK-free identity provisioning port and lifecycle boundary."""

import typing as _typing
from collections.abc import Iterator as _Iterator
from contextlib import contextmanager as _contextmanager
from dataclasses import dataclass as _dataclass
from typing import Protocol as _Protocol

from core.provider_slot import SingleProviderSlot as _SingleProviderSlot

IdentitySource = _typing.Literal["LDAP", "OIDC", "SAML"]
CustomerRoleName = _typing.Literal["Admin", "Manager", "Member"]
ProvisioningMode = _typing.Literal[
    "customer",
    "provider_staff",
    "provider_mapping_rejected",
]


class UserRef(_Protocol):
    pk: int
    username: str
    email: str


class TenantRef(_Protocol):
    pk: int
    slug: str
    is_provider: bool
    managed_by_id: int | None


@_dataclass(frozen=True)
class ExternalIdentityProfile:
    source: IdentitySource
    email: str | None
    upn: str | None
    first_name: str
    last_name: str


@_dataclass(frozen=True)
class ProviderStaffIntent:
    provider_tenant: TenantRef
    role_name: str


@_dataclass(frozen=True)
class ExternalIdentityProvisioningCommand:
    user: UserRef
    customer_tenant: TenantRef
    profile: ExternalIdentityProfile
    customer_role_name: CustomerRoleName
    provider_staff: ProviderStaffIntent | None = None


@_dataclass(frozen=True)
class ExternalIdentityProvisioningResult:
    mode: ProvisioningMode
    holder_id: int | None = None
    membership_id: int | None = None
    role_id: int | None = None


class IdentityProvisioner(_Protocol):
    def provision(
        self,
        command: ExternalIdentityProvisioningCommand,
    ) -> ExternalIdentityProvisioningResult:
        """Provision one normalized external identity command."""
        ...


_identity_provisioner = _SingleProviderSlot[IdentityProvisioner]("identity provisioner")


def configure_identity_provisioner(provider: IdentityProvisioner) -> None:
    _identity_provisioner.register(provider)


def provision_external_identity(
    command: ExternalIdentityProvisioningCommand,
) -> ExternalIdentityProvisioningResult:
    return _identity_provisioner.get().provision(command)


@_contextmanager
def override_identity_provisioner(
    provider: IdentityProvisioner,
) -> _Iterator[None]:
    with _identity_provisioner.override(provider):
        yield


__all__ = [
    "IdentitySource",
    "CustomerRoleName",
    "ProvisioningMode",
    "UserRef",
    "TenantRef",
    "ExternalIdentityProfile",
    "ProviderStaffIntent",
    "ExternalIdentityProvisioningCommand",
    "ExternalIdentityProvisioningResult",
    "IdentityProvisioner",
    "configure_identity_provisioner",
    "provision_external_identity",
    "override_identity_provisioner",
]
