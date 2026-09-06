"""PostgreSQL-backed regressions for the internal Asset Type image staging authority."""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.storage import FileSystemStorage
from django.test import TestCase
from django.utils import timezone

from assets.models.catalog import AssetType, AssetTypeImageStage, Manufacturer
from assets.services.specifications._image_staging import (
    CREATE_COMMAND_KIND,
    STAGE_LIFETIME_SECONDS,
    ImageStageError,
    cleanup_expired_stages,
    consume_stage,
    discard_stage,
    ingest_staged_image,
    lock_stage_for_consume,
    preview_stage_or_none,
)
from core.tests.mixins import TenantTestMixin
from organization.services.access_scope import ActorContextDTO, authentication_revision_for_actor

User = get_user_model()

_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c63f8cfc0f01f00050001ff89993d1d0000000049454e44ae426082"
)
_STAGE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class SpecificationImageStageTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.storage = FileSystemStorage(location=tempfile.mkdtemp(prefix="itambox-stage-svc-"))
        self.patch_default_storage = patch("assets.services.specifications._image_staging.default_storage", self.storage)
        self.patch_default_storage.start()
        self.addCleanup(self.patch_default_storage.stop)
        self.addCleanup(lambda: shutil.rmtree(self.storage.location, ignore_errors=True))

        self.user = User.objects.create_user(username="stage-owner")
        self.actor = ActorContextDTO(
            actor_id=self.user.pk,
            authentication_revision=authentication_revision_for_actor(self.user),
        )
        self.manufacturer = Manufacturer.objects.create(name="Stage maker", slug="stage-maker")

    def _stage(self, *, actor=None, content=None, name="type-image.png", now=None):
        return ingest_staged_image(
            actor=actor or self.actor,
            command_kind=CREATE_COMMAND_KIND,
            content=content or _TINY_PNG,
            original_name=name,
            now=now,
        )

    def test_ingestion_registers_a_pending_immutable_stage(self):
        now = timezone.now()
        stage_id = self._stage(now=now)
        self.assertIsNotNone(_STAGE_ID_RE.fullmatch(stage_id))
        row = AssetTypeImageStage.objects.get(stage_id=stage_id)
        self.assertEqual(row.state, "pending")
        self.assertEqual(row.actor_id, self.user.pk)
        self.assertEqual(row.authentication_revision, self.actor.authentication_revision)
        self.assertEqual(row.command_kind, CREATE_COMMAND_KIND)
        self.assertEqual(row.byte_size, len(_TINY_PNG))
        self.assertEqual(row.content_digest, hashlib.sha256(_TINY_PNG).hexdigest())
        self.assertTrue(row.storage_key.startswith("asset_types/"))
        self.assertEqual(row.expires_at, now + timedelta(seconds=STAGE_LIFETIME_SECONDS))
        self.assertTrue(self.storage.exists(row.storage_key))
        self.assertEqual(self.storage.open(row.storage_key).read(), _TINY_PNG)

    def test_ingestion_rejects_invalid_images_without_stage_or_blob(self):
        invalid_cases = (
            ("too-large.png", b"x" * (5 * 1024 * 1024 + 1)),
            ("malware.exe", b"MZ" + b"\x00" * 128),
            ("notes.txt", b"this is not an image"),
            ("fake.png", b"not really a png"),
        )
        for name, content in invalid_cases:
            with self.subTest(name=name):
                with self.assertRaises(ImageStageError):
                    self._stage(content=content, name=name)
                self.assertEqual(AssetTypeImageStage.objects.count(), 0)
                self.assertFalse(self.storage.exists("asset_types"))

    def test_discard_marks_discarded_and_removes_the_blob(self):
        stage_id = self._stage()
        self.assertTrue(discard_stage(stage_id, self.actor, CREATE_COMMAND_KIND))
        row = AssetTypeImageStage.objects.get(stage_id=stage_id)
        self.assertEqual(row.state, "discarded")
        self.assertFalse(self.storage.exists(row.storage_key))

    def test_discard_is_idempotent_and_never_touches_foreign_or_consumed_stages(self):
        self.assertFalse(discard_stage("a" * 32, self.actor, CREATE_COMMAND_KIND))

        foreign_id = self._stage()
        other_user = User.objects.create_user(username="stage-other")
        other_actor = ActorContextDTO(
            actor_id=other_user.pk,
            authentication_revision=authentication_revision_for_actor(other_user),
        )
        self.assertFalse(discard_stage(foreign_id, other_actor, CREATE_COMMAND_KIND))
        foreign = AssetTypeImageStage.objects.get(stage_id=foreign_id)
        self.assertEqual(foreign.state, "pending")
        self.assertTrue(self.storage.exists(foreign.storage_key))

        owner = AssetType.objects.create(
            manufacturer=self.manufacturer, model="Consumed type", slug="consumed-type"
        )
        consumed_id = self._stage()
        locked = lock_stage_for_consume(consumed_id, self.actor, CREATE_COMMAND_KIND)
        self.assertIsNotNone(locked)
        consume_stage(locked, owner.pk)
        self.assertFalse(discard_stage(consumed_id, self.actor, CREATE_COMMAND_KIND))
        consumed = AssetTypeImageStage.objects.get(stage_id=consumed_id)
        self.assertEqual(consumed.state, "consumed")
        self.assertTrue(self.storage.exists(consumed.storage_key))

        self.assertTrue(discard_stage(foreign_id, self.actor, CREATE_COMMAND_KIND))
        self.assertFalse(discard_stage(foreign_id, self.actor, CREATE_COMMAND_KIND))

    def test_cleanup_discards_only_expired_pending_stages_and_keeps_consumed_blobs(self):
        now = timezone.now()
        expired = self._stage(now=now - timedelta(hours=2))
        expired_row = AssetTypeImageStage.objects.get(stage_id=expired)
        AssetTypeImageStage.objects.filter(pk=expired_row.pk).update(expires_at=now - timedelta(minutes=1))

        unexpired = self._stage(now=now)
        owner = AssetType.objects.create(
            manufacturer=self.manufacturer, model="Cleanup type", slug="cleanup-type"
        )
        consumed = self._stage(now=now - timedelta(hours=2))
        consumed_row = AssetTypeImageStage.objects.get(stage_id=consumed)
        AssetTypeImageStage.objects.filter(pk=consumed_row.pk).update(expires_at=now - timedelta(minutes=1))
        locked_consumed = lock_stage_for_consume(
            consumed, self.actor, CREATE_COMMAND_KIND, now=now - timedelta(minutes=2)
        )
        self.assertIsNotNone(locked_consumed)
        consume_stage(locked_consumed, owner.pk)

        self.assertEqual(cleanup_expired_stages(now=now), 1)
        expired_after = AssetTypeImageStage.objects.get(stage_id=expired)
        self.assertEqual(expired_after.state, "discarded")
        self.assertFalse(self.storage.exists(expired_after.storage_key))

        unexpired_after = AssetTypeImageStage.objects.get(stage_id=unexpired)
        self.assertEqual(unexpired_after.state, "pending")
        self.assertTrue(self.storage.exists(unexpired_after.storage_key))

        consumed_after = AssetTypeImageStage.objects.get(stage_id=consumed)
        self.assertEqual(consumed_after.state, "consumed")
        self.assertTrue(self.storage.exists(consumed_after.storage_key))

        self.assertEqual(cleanup_expired_stages(now=now), 0)

    def test_stage_lookup_rejects_wrong_principal_command_or_state(self):
        stage_id = self._stage()
        self.assertIsNotNone(preview_stage_or_none(stage_id, self.actor, CREATE_COMMAND_KIND))

        other_user = User.objects.create_user(username="stage-lookup")
        other_actor = ActorContextDTO(
            actor_id=other_user.pk,
            authentication_revision=authentication_revision_for_actor(other_user),
        )
        self.assertIsNone(preview_stage_or_none(stage_id, other_actor, CREATE_COMMAND_KIND))
        self.assertIsNone(preview_stage_or_none(stage_id, self.actor, "apply_category_defaults"))

        stale_revision = self.actor.authentication_revision
        self.user.set_password("rotated-password")
        self.user.save(update_fields=["password"])
        stale_actor = ActorContextDTO(actor_id=self.user.pk, authentication_revision=stale_revision)
        self.assertIsNone(preview_stage_or_none(stage_id, stale_actor, CREATE_COMMAND_KIND))
        self.assertIsNone(lock_stage_for_consume(stage_id, stale_actor, CREATE_COMMAND_KIND))

        AssetTypeImageStage.objects.filter(stage_id=stage_id).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        self.assertIsNone(preview_stage_or_none(stage_id, self.actor, CREATE_COMMAND_KIND))