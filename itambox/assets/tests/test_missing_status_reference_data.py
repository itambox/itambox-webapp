from django.apps import apps
from django.db import connection
from django.test import TestCase

from assets.models import StatusLabel
from assets.signals import ensure_canonical_missing_status


class CanonicalMissingStatusTests(TestCase):
    """The canonical ``missing`` reference row survives deletion via the
    post-migrate restore seam without any historical migration rehearsal."""

    def test_restore_seam_recreates_the_canonical_row_after_deletion(self):
        StatusLabel._base_manager.filter(slug="missing").delete()
        self.assertFalse(StatusLabel._base_manager.filter(slug="missing").exists())

        ensure_canonical_missing_status(sender=apps.get_app_config("assets"), using=connection.alias)

        missing = StatusLabel._base_manager.get(slug="missing")
        self.assertEqual((missing.name, missing.type, missing.color), ("Missing", "undeployable", "dc3545"))

    def test_restore_seam_is_idempotent_and_leaves_normal_rows_untouched(self):
        StatusLabel._base_manager.filter(slug="missing").delete()
        ensure_canonical_missing_status(sender=apps.get_app_config("assets"), using=connection.alias)
        ensure_canonical_missing_status(sender=apps.get_app_config("assets"), using=connection.alias)

        self.assertEqual(StatusLabel._base_manager.filter(slug="missing").count(), 1)

        normal = StatusLabel._base_manager.create(
            name="Post-restore available",
            slug="post-restore-available",
            type=StatusLabel.TYPE_DEPLOYABLE,
            color="28a745",
        )
        missing = StatusLabel._base_manager.get(slug="missing")
        self.assertNotEqual(normal.pk, missing.pk)
        self.assertTrue(StatusLabel._base_manager.filter(pk=normal.pk).exists())
