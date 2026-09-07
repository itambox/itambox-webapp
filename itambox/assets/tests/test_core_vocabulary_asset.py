import json
import unittest
from pathlib import Path

from assets.services.specifications.core_vocabulary import get_core_vocabulary


class CoreVocabularyAssetTests(unittest.TestCase):
    @staticmethod
    def _oracle():
        repository_root = Path(__file__).resolve().parents[3]
        oracle_path = (
            repository_root
            / "scripts"
            / "tests"
            / "fixtures"
            / "specification_vocabulary"
            / "canonical-target.json"
        )
        return json.loads(oracle_path.read_text(encoding="utf-8"))

    def test_asset_is_exact_normalized_oracle(self):
        self.assertEqual(get_core_vocabulary(), self._oracle())

    def test_asset_returns_an_independent_deep_copy(self):
        first = get_core_vocabulary()
        second = get_core_vocabulary()

        first["active_fields"][0]["label"] = "mutated"
        first["sections"][0]["memberships"].append({"field": "mutated"})

        self.assertNotEqual(first, second)
        self.assertEqual(second, self._oracle())


if __name__ == "__main__":
    unittest.main()
