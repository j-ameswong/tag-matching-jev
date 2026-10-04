"""Representation experiments must not recover answers through context labels."""

import csv
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ao3_benchmark import sha256
from ao3_representations import (add_work, build_profiles, length_band, new_profile,
                                 render, selected_aliases, summarize_profile, work_assignment)
from ao3_work_html import parse_work
from run_ao3_representations import embedding_batches, normalize
from run_ao3_alias_pooling import pool_scores
import numpy as np


class RepresentationTests(unittest.TestCase):
    def test_length_boundaries_and_missingness(self):
        self.assertEqual([length_band(x) for x in (None, 0, 4999, 5000, 19999, 20000, 99999, 100000)],
                         [None, 0, 0, 1, 1, 2, 2, 3])
        p = new_profile()
        add_work(p, 10, 1, (), "en", True, False, None)
        add_work(p, 20, 2, (), "en", True, False, 0)
        self.assertEqual((p["n"], p["words_n"], p["bands"]), (2, 1, [1, 0, 0, 0]))

    def test_bottom_hash_sample_is_order_independent_and_masks_target(self):
        profiles = []
        for order in (range(50), reversed(range(50))):
            p = new_profile()
            for i in order:
                add_work(p, i, i + 1, (1, 2), "en", True, False, 200)
            profiles.append(summarize_profile(p, 1, {1: ("Answer", "Freeform"), 2: ("Context", "Fandom")}, {}))
        self.assertEqual(profiles[0], profiles[1])
        self.assertEqual(profiles[0]["sample_work_ordinals"], list(range(1, 33)))
        self.assertEqual(profiles[0]["co_tags"]["Freeform"], [])
        self.assertNotIn("Answer", render("Query", "all", profiles[0], denominator=100))

    def test_all_evaluated_aliases_are_globally_excluded(self):
        aliases = [{"source_id": i, "canonical_id": 1, "name": f"Alias {i}"} for i in range(10, 20)]
        b = SimpleNamespace(aliases=aliases, examples=[{"source_id": 10, "example_kind": "synonym"},
                                                       {"source_id": 11, "example_kind": "synonym"}])
        selected = selected_aliases(b)[1]
        self.assertEqual(len(selected), 5)
        self.assertFalse({10, 11} & {r["source_id"] for r in selected})

    def test_work_disjointness_raw_query_membership_and_alias_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (root / "catalogue.csv").open("w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["canonical_id", "name", "type"])
                writer.writerows([[1, "Answer", "Freeform"], [2, "Other", "Freeform"], [4, "Fandom", "Fandom"]])
            work = root / "works.csv"
            with work.open("w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["creation date", "language", "restricted", "complete", "word_count", "tags", ""])
                writer.writerows([["2020-01-01", "en", "false", "true", 100, tags]
                                  for tags in ("10+1+4", "11+1+4", "10+1+4+12", "10+2+4", "12+4")])
            b = SimpleNamespace(catalogue=[{"canonical_id": 1}, {"canonical_id": 2}],
                                aliases=[{"source_id": 10, "canonical_id": 1}, {"source_id": 11, "canonical_id": 1}],
                                examples=[{"source_id": 10, "canonical_id": 1, "example_kind": "synonym"}],
                                manifest={"source_files": {"works.csv": {"sha256": sha256(work)}}})
            with patch("ao3_representations.work_assignment", side_effect=[(0, 1), (0, 2), (6, 3), (8, 4), (0, 5)]):
                profiles, manifest = build_profiles(b, root, work, root)
            self.assertEqual(profiles["candidate"]["1"]["n"], 1)
            self.assertEqual(profiles["candidate"]["2"]["n"], 0)
            self.assertEqual(profiles["query"]["10"]["n"], 1)
            self.assertEqual(profiles["candidate"]["1"]["sample_work_ordinals"], [2])
            self.assertEqual(profiles["query"]["10"]["sample_work_ordinals"], [3])
            self.assertEqual(profiles["query"]["10"]["one_work"]["Freeform"], [])
            self.assertEqual(manifest["counts"]["support_works_excluded_evaluated_alias"], 1)
            work.write_text(work.read_text() + "\n")
            with self.assertRaisesRegex(ValueError, "audited source"):
                build_profiles(b, root, work, root)

    def test_assignment_depends_on_snapshot_and_record_not_target(self):
        self.assertEqual(work_assignment("snapshot-a", 123), work_assignment("snapshot-a", 123))
        self.assertNotEqual(work_assignment("snapshot-a", 123), work_assignment("snapshot-b", 123))

    def test_request_batches_respect_utf8_byte_budget_without_skipping_text(self):
        texts = ["already cached"] + ["é" * 900] * 1000
        batches = embedding_batches(texts, 1)
        self.assertEqual([i for start, end in batches for i in range(start, end)], list(range(1, len(texts))))
        for start, end in batches:
            self.assertLessEqual(end - start, 512)
            self.assertLessEqual(sum(len(t.encode("utf-8")) for t in texts[start:end]), 280000)

    def test_anchor_normalization_preserves_direction_and_rejects_zero(self):
        vectors = normalize(np.array([[3, 4], [5, 0]], dtype=np.float32))
        np.testing.assert_allclose(vectors, [[0.6, 0.8], [1, 0]])
        self.assertEqual(vectors.dtype, np.float64)
        with self.assertRaises(ValueError):
            normalize(np.zeros((1, 2)))

    def test_multiple_alias_vectors_produce_one_maximum_per_canonical(self):
        canonical = np.asarray([0.2, 0.6, 0.4])
        pooled = pool_scores(canonical, np.asarray([0.8, 0.3, 0.9]), np.asarray([0, 0, 1]))
        np.testing.assert_allclose(pooled, [0.8, 0.9, 0.4])
        np.testing.assert_allclose(canonical, [0.2, 0.6, 0.4])

    def test_fixture_distinguishes_work_stats_from_chapter_navigation(self):
        fixture = Path(__file__).parent / "fixtures/ao3_work.html"
        parsed = parse_work(fixture.read_text())
        self.assertEqual(parsed["work_id"], 6623293)
        self.assertEqual(parsed["stats"]["comments"], 103)
        self.assertEqual(parsed["stats"]["words"], 65871)
        self.assertEqual(parsed["stats"]["chapters"], "25/25")
        self.assertEqual(parsed["saved_chapter_containers"], ["chapter-1"])
        self.assertTrue(parsed["summary"])
        self.assertEqual([t["name"] for t in parsed["tags"]["Freeform"]], ["Time Travel", "World War II", "Nazis"])
        self.assertNotIn("Log In", parsed["summary"])
        self.assertFalse(parsed["canonical_or_merger_labels_supplied"])

    def test_login_page_is_not_a_work_and_missing_metrics_stay_unknown(self):
        with self.assertRaises(ValueError):
            parse_work("<html><h1>Log In</h1></html>")
        parsed = parse_work('<dl class="work meta"><dd class="freeform tags"><a class="tag" href="/tags/A/works">A &amp; B</a></dd></dl>')
        self.assertIsNone(parsed["stats"]["kudos"])
        self.assertIsNone(parsed["summary"])
        self.assertEqual(parsed["tags"]["Freeform"][0]["name"], "A & B")


if __name__ == "__main__":
    unittest.main()
