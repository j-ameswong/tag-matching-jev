"""Regression checks for AO3 export, leakage rules and exhaustive ranking."""

import base64
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from rapidfuzz import process
from rapidfuzz.distance import Levenshtein

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ao3_benchmark import Benchmark, load_benchmark, sample_synonyms, sha256, split_for, write_json
from ao3_scoring import (POLICIES, GroupStatistics, RankingContext, exact_matching, normalize,
                         rank_row, read_checkpoint, save_checkpoint)
from build_ao3_benchmark import build
from cache_ao3_embeddings import parse_vectors


def fixture():
    catalogue = [{"canonical_id": 10, "name": "A/B"}, {"canonical_id": 20, "name": "A&B"},
                 {"canonical_id": 30, "name": "same NAME"}, {"canonical_id": 40, "name": "same name"}]
    aliases = [{"source_id": 100, "name": "incoming", "canonical_id": 10},
               {"source_id": 101, "name": "Incoming", "canonical_id": 10},
               {"source_id": 102, "name": "other alias", "canonical_id": 10},
               {"source_id": 103, "name": "distractor alias", "canonical_id": 20}]
    examples = [{"source_id": 100, "incoming_tag": "incoming", "canonical_id": 10},
                {"source_id": 10, "incoming_tag": "A/B", "canonical_id": 10},
                {"source_id": 200, "incoming_tag": "SAME NAME", "canonical_id": 30}]
    return Benchmark(catalogue, examples, aliases, {}, {})


class AO3BaselineTests(unittest.TestCase):
    def test_sampling_is_order_invariant_and_respects_cap(self):
        rows = [{"source_id": i, "canonical_id": i // 5} for i in range(40)]
        selected = sample_synonyms(rows, size=16, cap=2)
        self.assertEqual(selected, sample_synonyms(list(reversed(rows)), size=16, cap=2))
        self.assertEqual(len(selected), 16)
        self.assertTrue(all(sum(r["canonical_id"] == target for r in selected) == 2 for target in range(8)))
        with self.assertRaises(ValueError):
            sample_synonyms(rows, size=17, cap=2)

    def test_exact_lookup_masks_query_before_normalization_and_abstains_on_collision(self):
        benchmark = fixture()
        raw, _ = exact_matching(benchmark, "raw", True)
        self.assertEqual(raw[0]["status"], "no_match")
        folded, collisions = exact_matching(benchmark, "casefold", True)
        self.assertEqual(folded[0]["top_ids"], [10])
        self.assertEqual(folded[2]["status"], "ambiguous")
        self.assertEqual(collisions["cross_target_collision_keys_before_query_mask"], 1)
        benchmark.aliases = benchmark.aliases[:1]
        masked, _ = exact_matching(benchmark, "casefold", True)
        self.assertEqual(masked[0]["status"], "no_match")
        self.assertEqual(masked[1]["expected_rank"], 1)

    def test_safe_normalization_preserves_ao3_punctuation_and_diacritics(self):
        a, b = "  André/A & B | C (Fándom)  ", "André&A & B | C (Fándom)"
        self.assertEqual(normalize(a, "safe_punctuation"), "andré/a & b | c (fándom)")
        self.assertNotEqual(normalize(a, "safe_punctuation"), normalize(b, "safe_punctuation"))
        self.assertEqual(normalize("self\u2011insert character\u2019s", "safe_punctuation"), "self-insert character's")
        self.assertNotEqual(normalize("a—b", "safe_punctuation"), normalize("a-b", "safe_punctuation"))

    def test_top_k_and_full_rank_match_stable_reference_even_with_boundary_ties(self):
        rng = np.random.default_rng(71)
        ids = np.arange(1, 602, 3, dtype=np.int64)
        for scores in (np.zeros(len(ids)), rng.integers(0, 4, len(ids)).astype(float), rng.normal(size=len(ids))):
            order = np.lexsort((ids, -scores))
            for position in (0, 13, len(ids) - 1):
                result = rank_row(scores, ids, position)
                self.assertEqual(result["top_ids"], ids[order[:10]].tolist())
                self.assertEqual(result["expected_rank"], int(np.flatnonzero(order == position)[0]) + 1)

    def test_aliases_aggregate_by_id_after_self_mask(self):
        context = RankingContext(fixture())
        scores = np.array([0.1, 0.5, 0.2, 0.0, 1.0, 0.3, 0.4, 0.6])
        rows = context.policies(scores, 0)
        self.assertEqual(rows[POLICIES[0]]["expected_rank"], 3)
        self.assertEqual(rows[POLICIES[1]]["expected_rank"], 2)
        self.assertEqual(rows[POLICIES[1]]["expected_score"], 0.4)
        self.assertEqual(rows[POLICIES[1]]["top_ids"], [20, 10, 30, 40])
        np.testing.assert_array_equal(scores, [0.1, 0.5, 0.2, 0.0, 1.0, 0.3, 0.4, 0.6])

    def test_batched_lexical_and_cosine_scores_match_single_matrix(self):
        context = RankingContext(fixture())
        queries = [r["incoming_tag"] for r in context.benchmark.examples]
        full = process.cdist(queries, context.terms, scorer=Levenshtein.normalized_similarity, dtype=np.float64)
        for i, query in enumerate(queries):
            chunk = process.cdist([query], context.terms, scorer=Levenshtein.normalized_similarity, dtype=np.float64)
            self.assertEqual(context.policies(full[i], i), context.policies(chunk[0], i))
        rng = np.random.default_rng(4)
        q, c = rng.normal(size=(3, 7)), rng.normal(size=(4, 7))
        q /= np.linalg.norm(q, axis=1)[:, None]
        c /= np.linalg.norm(c, axis=1)[:, None]
        full = q @ c.T
        for i in range(len(q)):
            scores = (q[i:i+1] @ c.T)[0]
            np.testing.assert_allclose(scores, full[i], atol=1e-15)
            self.assertEqual(rank_row(scores, context.ids, 0)["top_ids"], rank_row(full[i], context.ids, 0)["top_ids"])

    def test_embedding_response_order_and_integrity(self):
        def entry(i, v):
            return {"index": i, "embedding": base64.b64encode(np.asarray(v, dtype="<f4").tobytes()).decode()}
        payload = {"data": [entry(1, [3, 4]), entry(0, [1, 2])]}
        np.testing.assert_array_equal(parse_vectors(payload, 2, dimensions=2), [[1, 2], [3, 4]])
        for invalid in ({"data": [entry(0, [1, 2]), entry(0, [3, 4])]},
                        {"data": [entry(0, [0, 0])]}, {"data": [entry(0, [np.nan, 1])]}):
            with self.assertRaises(ValueError):
                parse_vectors(invalid, len(invalid["data"]), dimensions=2)

    def test_checkpoint_rejects_content_or_recipe_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            save_checkpoint(path, {"snapshot": "one"}, {"rank": 7})
            self.assertEqual(read_checkpoint(path, {"snapshot": "one"}), {"rank": 7})
            with self.assertRaises(ValueError):
                read_checkpoint(path, {"snapshot": "two"})
            content = json.loads(path.read_text())
            content["result"]["rank"] = 1
            write_json(path, content)
            with self.assertRaises(ValueError):
                read_checkpoint(path, {"snapshot": "one"})

    def test_macro_and_micro_differ_for_unequal_families(self):
        stats = GroupStatistics([1, 1, 1, 2])
        result = stats.calculate([1, 1, 1, 0])
        self.assertEqual(result["micro"], 0.75)
        self.assertEqual(result["macro"], 0.5)
        self.assertEqual(stats.calculate([1, 1, 1, 1])["micro_ci95"], [1.0, 1.0])

    def test_export_adapter_keeps_chains_splits_and_full_catalogue(self):
        dev = [i for i in range(1, 50) if split_for(i) == "development"][:2]
        holdout = next(i for i in range(50, 100) if split_for(i) == "test")
        rows = [[dev[0], "Freeform", "First", "true", 0, ""],
                [dev[1], "Freeform", "Distractor", "true", 0, ""],
                [holdout, "Freeform", "Heldout canonical", "true", 0, ""],
                [1000, "Freeform", "First alias", "false", 5, dev[0]],
                [1001, "Freeform", "Chain alias", "false", 5, 1000],
                [1002, "Freeform", "Hidden test alias", "false", 5, holdout],
                [1003, "Freeform", "Redacted", "false", 50, dev[0]],
                [1004, "Freeform", "Unlabelled", "false", 9, ""],
                [1005, "Freeform", "Dangling", "false", 5, 9999],
                [1006, "Freeform", "Conflicting", "true", 5, dev[0]],
                [1007, "Freeform", "Conflict path", "false", 5, 1006]]
        with tempfile.TemporaryDirectory() as directory:
            source, output, report = [Path(directory) / name for name in ("source", "output", "report")]
            source.mkdir()
            with (source / "tags-20210226.csv").open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["id", "type", "name", "canonical", "cached_count", "merger_id"])
                writer.writerows(rows)
            (source / "works-20210226.csv").write_text("unused by Stage 1\n")
            manifest = build(source, output, report, size=2, cap=2, identities=1)
            benchmark = load_benchmark(output)
            self.assertEqual(len(benchmark.catalogue), 3)
            self.assertEqual({r["source_id"] for r in benchmark.aliases}, {1000, 1001})
            self.assertEqual(benchmark.aliases[1]["merger_id"], 1000)
            self.assertEqual(benchmark.aliases[1]["canonical_id"], dev[0])
            self.assertEqual(benchmark.aliases[1]["resolution_hops"], 2)
            self.assertFalse(benchmark.provenance["calibration_and_test_evaluated"])
            self.assertEqual(manifest["excluded_named_merged"]["missing_target"], 1)
            self.assertEqual(manifest["excluded_named_merged"]["canonical_merger_conflict"], 1)
            # Integrity failures must not silently result in a different benchmark.
            with (output / "development.csv").open("a") as handle:
                handle.write("corruption\n")
            with self.assertRaisesRegex(ValueError, "checksum"):
                load_benchmark(output)


if __name__ == "__main__":
    unittest.main()
