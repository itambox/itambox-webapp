"""Internal durable staging authority for Asset Type model images.

This module owns ingestion, read-only validation, transactional consume, and
explicit discard/expiry cleanup of ``AssetTypeImageStage`` rows.  It is the
bounded Adapter-facing seam the accepted contract requires: an adapter may
ingest validated bytes here *before command entry* and receives a bounded
server-created ``StagedImageId``; the four public T01 commands continue to
accept only immutable DTOs and that opaque ID.  Adapters later call this
authority; they do not implement its rules.

Storage effects (filesystem writes) are not rolled back by
``transaction.atomic``; the module therefore compensates bounded ingestion
failures with a storage delete and keeps cleanup state-driven and idempotent.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import DEFAULT_DB_ALIAS, transaction
from django.utils import timezone

from assets.models.catalog import AssetTypeImageStage
from core.validators import validate_image_attachment
from organization.services.access_scope import ActorContextDTO

STAGE_ID_CHARACTERS = 32
STAGE_LIFETIME_SECONDS = 60 * 60
CREATE_COMMAND_KIND = "create_asset_type"

_DEFAULT_DB = DEFAULT_DB_ALIAS


class ImageStageError(ValueError):
    """Stable, nondisclosing failure for any unusable staged image reference."""

    code = "REFERENCE_CONFLICT"

    def __init__(self, reason: str = "invalid staged image reference") -> None:
        self.reason = reason
        super().__init__(self.code)


def _stage_storage_key(stage_id: str, extension: str) -> str:
    """Server-owned immutable object key under the default media storage.

    The key lives under the same ``asset_types/`` prefix as the final
    ``AssetType.image`` values so a consumed stage name can become the owner's
    image value without moving or copying bytes during create.
    """
    return f"asset_types/{stage_id}{extension}"


def ingest_staged_image(
    *,
    actor: ActorContextDTO,
    command_kind: str,
    content: bytes,
    original_name: str,
    now=None,
) -> str:
    """Validate the bytes, write an immutable object, and register a pending stage.

    ``now`` overrides the clock for deterministic expiry tests.  Ingestion
    effects occur before command entry and are therefore not part of the
    command's zero-write rejection rule; DB/storage failures compensate the
    other side before re-raising.
    """
    if not isinstance(actor, ActorContextDTO):
        raise TypeError("actor must be an ActorContextDTO")
    if not isinstance(content, bytes) or not content:
        raise ValueError("content must be a non-empty byte string")
    if not isinstance(original_name, str) or not original_name:
        raise ValueError("original_name must be a non-empty string")

    uploaded = ContentFile(content, name=original_name)
    try:
        validate_image_attachment(uploaded)
    except ValidationError as exc:
        raise ImageStageError("image validation failed") from exc

    content_digest = hashlib.sha256(content).hexdigest()
    extension = os.path.splitext(original_name)[1].lower()
    stage_id = secrets.token_hex(STAGE_ID_CHARACTERS // 2)
    storage_key = _stage_storage_key(stage_id, extension)
    stored_name = default_storage.save(storage_key, ContentFile(content))
    try:
        with transaction.atomic(using=_DEFAULT_DB):
            AssetTypeImageStage.objects.using(_DEFAULT_DB).create(
                stage_id=stage_id,
                actor_id=actor.actor_id,
                authentication_revision=actor.authentication_revision,
                command_kind=command_kind,
                storage_key=stored_name,
                byte_size=len(content),
                content_digest=content_digest,
                state=AssetTypeImageStage.State.PENDING,
                expires_at=((now or timezone.now()) + timedelta(seconds=STAGE_LIFETIME_SECONDS)),
            )
    except Exception:
        default_storage.delete(stored_name)
        raise
    return stage_id


def _stage_matches_actor(row: AssetTypeImageStage, actor: ActorContextDTO, command_kind: str, *, now) -> bool:
    if row.state != AssetTypeImageStage.State.PENDING:
        return False
    if row.actor_id != actor.actor_id:
        return False
    if row.authentication_revision != actor.authentication_revision:
        return False
    if row.command_kind != command_kind:
        return False
    if row.expires_at <= (now or timezone.now()):
        return False
    return True


def preview_stage_or_none(stage_id: str, actor: ActorContextDTO, command_kind: str, *, using: str = _DEFAULT_DB, now=None):
    """Read-only validation for previews: never consumes, deletes, or refreshes.

    Returns the pending immutable stage row when ownership, authentication
    binding, command kind, and expiry all match; otherwise ``None``.
    """
    row = AssetTypeImageStage.objects.using(using).filter(stage_id=stage_id).first()
    if row is None:
        return None
    if not _stage_matches_actor(row, actor, command_kind, now=now):
        return None
    return row


def lock_stage(stage_id: str, *, using: str = _DEFAULT_DB):
    """Acquire the stage row lock in the deterministic reference-row order."""
    return (
        AssetTypeImageStage.objects.using(using)
        .select_for_update()
        .filter(stage_id=stage_id)
        .order_by("pk")
        .first()
    )


def lock_stage_for_consume(stage_id: str, actor: ActorContextDTO, command_kind: str, *, using: str = _DEFAULT_DB, now=None):
    """Lock and revalidate a pending stage for the write path.

    Returns the locked row when still pending/matching, ``None`` otherwise;
    the caller maps both to a nondisclosing reference conflict so a consumed
    or expired stage never reveals its state.
    """
    row = lock_stage(stage_id, using=using)
    if row is None:
        return None
    if not _stage_matches_actor(row, actor, command_kind, now=now):
        return None
    return row


def consume_stage(row: AssetTypeImageStage, asset_type_id: int, *, using: str = _DEFAULT_DB) -> None:
    """Mark the stage consumed and associate its owner inside the caller's savepoint."""
    row.state = AssetTypeImageStage.State.CONSUMED
    row.consumed_asset_type_id = asset_type_id
    row.save(using=using, update_fields=["state", "consumed_asset_type_id", "updated_at"])


def discard_stage(stage_id: str, actor: ActorContextDTO, command_kind: str, *, using: str = _DEFAULT_DB) -> bool:
    """Explicitly mark one owned pending stage discarded and remove its blob.

    State is marked transactionally; the storage delete runs afterwards and is
    idempotent.  A consumed stage, another actor's stage, or an unknown ID is
    left untouched and reports ``False``.
    """
    row = lock_stage(stage_id, using=using)
    if row is None:
        return False
    if not _stage_matches_actor(row, actor, command_kind, now=timezone.now()):
        return False
    with transaction.atomic(using=using):
        locked = (
            AssetTypeImageStage.objects.using(using)
            .select_for_update()
            .filter(pk=row.pk)
            .first()
        )
        if locked is None or not _stage_matches_actor(locked, actor, command_kind, now=timezone.now()):
            return False
        locked.state = AssetTypeImageStage.State.DISCARDED
        locked.consumed_asset_type_id = None
        locked.save(using=using, update_fields=["state", "consumed_asset_type_id", "updated_at"])
    default_storage.delete(locked.storage_key)
    return True


def cleanup_expired_stages(*, using: str = _DEFAULT_DB, now=None) -> int:
    """Discard expired pending stages durably and remove their blobs.

    Only pending rows at or past expiry are touched; consumed blobs are never
    removed.  Each row is re-locked and re-checked so a concurrent consume
    wins the race and this pass leaves it consumed.  Idempotent and retry-safe.
    """
    cutoff = now or timezone.now()
    candidates = list(
        AssetTypeImageStage.objects.using(using)
        .filter(state=AssetTypeImageStage.State.PENDING, expires_at__lte=cutoff)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    discarded = 0
    for pk in candidates:
        with transaction.atomic(using=using):
            row = AssetTypeImageStage.objects.using(using).select_for_update().filter(pk=pk).first()
            if row is None or row.state != AssetTypeImageStage.State.PENDING or row.expires_at > cutoff:
                continue
            row.state = AssetTypeImageStage.State.DISCARDED
            row.save(using=using, update_fields=["state", "updated_at"])
        default_storage.delete(row.storage_key)
        discarded += 1
    return discarded


__all__ = [
    "CREATE_COMMAND_KIND",
    "ImageStageError",
    "STAGE_ID_CHARACTERS",
    "STAGE_LIFETIME_SECONDS",
    "cleanup_expired_stages",
    "consume_stage",
    "discard_stage",
    "ingest_staged_image",
    "lock_stage",
    "lock_stage_for_consume",
    "preview_stage_or_none",
]