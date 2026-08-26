"""SDK-free restore authority port and lifecycle boundary."""

from collections.abc import Iterator as _Iterator
from contextlib import contextmanager as _contextmanager
from typing import Protocol as _Protocol

from core.provider_slot import SingleProviderSlot as _SingleProviderSlot


class PrincipalRef(_Protocol):
    pk: int


class RestoreAuthorityValidator(_Protocol):
    def validate(self, user: PrincipalRef, obj: object) -> None:
        """Validate restore authority for one principal and object."""


_restore_authority_validator = _SingleProviderSlot[RestoreAuthorityValidator]("restore-authority validator")


def configure_restore_authority_validator(
    provider: RestoreAuthorityValidator,
) -> None:
    _restore_authority_validator.register(provider)


def validate_restore_grant_authority(
    user: PrincipalRef,
    obj: object,
) -> None:
    _restore_authority_validator.get().validate(user, obj)


@_contextmanager
def override_restore_authority_validator(
    provider: RestoreAuthorityValidator,
) -> _Iterator[None]:
    with _restore_authority_validator.override(provider):
        yield


__all__ = [
    "PrincipalRef",
    "RestoreAuthorityValidator",
    "configure_restore_authority_validator",
    "validate_restore_grant_authority",
    "override_restore_authority_validator",
]
