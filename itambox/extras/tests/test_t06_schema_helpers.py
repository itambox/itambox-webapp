from datetime import datetime, timezone
import unittest

from extras.t06_schema import (
    T06SchemaConflict,
    classify_activation,
    dense_ordinals,
    normalize_lifecycle,
    validate_activation,
    validate_object_types,
)


class T06SchemaHelperTests(unittest.TestCase):
    def test_object_types_are_the_only_applicability_authority(self):
        self.assertEqual(
            validate_object_types(("assets.assettype", "organization.assetholder", "assets.assettype")),
            ("assets.assettype", "organization.assetholder"),
        )

    def test_empty_object_types_are_blocking(self):
        with self.assertRaisesRegex(T06SchemaConflict, "empty_object_types"):
            validate_object_types(())

    def test_activation_is_a_closed_vocabulary(self):
        self.assertEqual(validate_activation("global"), "global")
        with self.assertRaisesRegex(T06SchemaConflict, "invalid_activation"):
            validate_activation("membership-derived")

    def test_activation_classification_is_not_recomputed_at_runtime(self):
        self.assertEqual(classify_activation(has_memberships=True), "composed")
        self.assertEqual(classify_activation(has_memberships=False), "global")

    def test_lifecycle_preserves_deprecated_history(self):
        timestamp = datetime(2026, 9, 6, tzinfo=timezone.utc)
        lifecycle, deprecated_at = normalize_lifecycle(
            lifecycle="deleted",
            deleted_at=None,
            deprecated_at=None,
            migration_timestamp=timestamp,
        )
        self.assertEqual((lifecycle, deprecated_at), ("deprecated", timestamp))

    def test_ordinals_are_dense_and_stable(self):
        self.assertEqual(
            dense_ordinals(((30, "z"), (10, "a"), (20, "m"))),
            {"a": 1, "m": 2, "z": 3},
        )
        with self.assertRaisesRegex(T06SchemaConflict, "duplicate_member"):
            dense_ordinals(((1, "same"), (2, "same")))


if __name__ == "__main__":
    unittest.main()
