"""Guard the evaluation against leakage, collisions, and inflated metrics."""

import sys
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from cache_stage1_embeddings import CONFIG, parse_vectors
from run_stage1_baselines import (aggregate_alias_scores, exact_predictions,
                                 group_statistics, normalize, rank_scores)
from stage1_data import load_benchmark


class BaselineTests(unittest.TestCase):
    def test_normalization_preserves_technical_punctuation(self):
        normalized = {normalize(tag, "safe_separators") for tag in ("C", "C++", "C#", ".NET", "NET")}
        self.assertEqual(normalized, {"c", "c++", "c#", ".net", "net"})
        self.assertEqual(normalize("  Foo___Bar — baz  ", "safe_separators"), "foo-bar-baz")
        self.assertEqual(normalize("__init__", "safe_separators"), "__init__")

    def test_normalization_collision_abstains(self):
        predictions, statuses = exact_predictions(["foo_bar"], ["foo-bar", "foo_bar"], [], "safe_separators", False)
        self.assertEqual(predictions, [None])
        self.assertEqual(statuses, ["ambiguous"])

    def test_exact_lookup_hides_evaluated_alias(self):
        predictions, statuses = exact_predictions(["alias", "alpha"], ["alpha", "beta"],
                                                  [("alias", "alpha")], "raw", True)
        self.assertEqual(predictions, [None, "alpha"])
        self.assertEqual(statuses, ["no_match", "unique"])

    def test_other_alias_remains_permitted_after_self_mask(self):
        predictions, _ = exact_predictions(["foo_bar"], ["alpha"],
                                          [("foo_bar", "alpha"), ("foo-bar", "alpha")],
                                          "safe_separators", True)
        self.assertEqual(predictions, ["alpha"])

    def test_alias_scores_are_masked_then_deduplicated(self):
        aliases = [("alias", "alpha"), ("other-alpha", "alpha"), ("other-beta", "beta")]
        scores = np.array([[0.2, 0.3, 1.0, 0.1, 0.15], [1.0, 0.0, 0.7, 0.9, 0.3]])
        result = aggregate_alias_scores(scores, ["alias", "alpha"], ["alpha", "beta"], aliases)
        np.testing.assert_allclose(result, [[0.2, 0.3], [1.0, 0.3]])
        self.assertEqual(scores[0, 2], 1.0)  # The reusable raw scores are unmodified.

    def test_rank_ties_are_deterministic_and_targets_not_repeated(self):
        ranks, top = rank_scores(np.array([[0.5, 0.5, 0.2], [0.2, 0.5, 0.5]]),
                                ["alpha", "beta", "gamma"], ["beta", "gamma"])
        np.testing.assert_array_equal(ranks, [2, 2])
        np.testing.assert_array_equal(top, [[0, 1, 2], [1, 2, 0]])

    def test_macro_metric_gives_each_canonical_target_equal_weight(self):
        result = group_statistics([True, True, True, False], ["a", "a", "a", "b"])
        self.assertEqual(result["micro"], 0.75)
        self.assertEqual(result["macro"], 0.5)
        self.assertEqual(result, group_statistics([True, True, True, False], ["a", "a", "a", "b"]))

    def test_embedding_response_reorders_by_index(self):
        payload = {"data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}]}
        with patch.dict(CONFIG, dimensions=2):
            np.testing.assert_array_equal(parse_vectors(payload, 2), np.eye(2))

    def test_bad_embedding_response_is_rejected(self):
        for data in ([{"index": 0, "embedding": [1, 0]}] * 2,
                     [{"index": 0, "embedding": [0, 0]}, {"index": 1, "embedding": [1, 0]}],
                     [{"index": 0, "embedding": [float("nan"), 1]}, {"index": 1, "embedding": [1, 0]}]):
            with self.subTest(data=data), patch.dict(CONFIG, dimensions=2), self.assertRaises(ValueError):
                parse_vectors({"data": data}, 2)

    def test_frozen_snapshot_loads_only_development_queries_and_aliases(self):
        path = Path(__file__).resolve().parents[1] / "data" / "stackoverflow"
        if not (path / "manifest.json").exists():
            self.skipTest("Frozen snapshot is not present")
        names, examples, aliases, metadata = load_benchmark(path)
        self.assertEqual(len(names), 2454)
        self.assertEqual(len(examples), 2453)
        self.assertEqual(len(aliases), 1033)
        self.assertTrue(all(r["split"] == "development" for r in examples))
        self.assertEqual({a for a, _ in aliases}, {r["incoming_tag"] for r in examples if r["example_kind"] == "synonym"})
        self.assertFalse(metadata["calibration_and_test_evaluated"])


if __name__ == "__main__":
    unittest.main()
