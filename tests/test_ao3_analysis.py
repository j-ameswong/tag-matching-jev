"""Graph integrity, redaction, and nullable works fields in the AO3 audit."""

import csv
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from analyze_ao3_dataset import (CONFLICT, CYCLE, MISSING, UNMERGED,
                                 audit_tags, audit_works, resolve_mergers)


class AO3AuditTests(unittest.TestCase):
    def test_resolves_chains_and_quarantines_broken_graphs(self):
        ids = np.arange(1, 12)
        mergers = np.array([0, 1, 2, 999, 0, 7, 6, 6, 1, 9, 0])
        canonical = np.array([True, False, False, False, False, False, False, False, True, False, True])
        terminal, depth, missing = resolve_mergers(ids, mergers, canonical)
        np.testing.assert_array_equal(terminal, [0, 0, 0, MISSING, UNMERGED, CYCLE, CYCLE, CYCLE, CONFLICT, CONFLICT, 10])
        np.testing.assert_array_equal(depth[:5], [0, 1, 2, 1, 0])
        np.testing.assert_array_equal(missing, [3])

    def test_duplicate_ids_cannot_be_resolved(self):
        with self.assertRaises(ValueError):
            resolve_mergers(np.array([1, 1]), np.array([0, 0]), np.array([True, True]))

    def test_redaction_type_mismatch_and_canonical_conflicts_are_excluded(self):
        rows = [
            [1, "Freeform", "Canonical", "true", 0, ""],
            [2, "Freeform", "Alias", "false", 5, 1],
            [3, "Freeform", "Redacted", "false", 100, 1],
            [4, "Freeform", "Unlabelled", "false", 8, ""],
            [5, "Freeform", "Conflicting canonical", "true", 10, 1],
            [6, "Freeform", "Points at conflict", "false", 5, 5],
            [7, "Fandom", "Other type", "true", 5, ""],
            [8, "Freeform", "Wrong type", "false", 5, 7],
            [9, "Freeform", "", "false", 5, 1],
            [10, "Freeform", "Redacted", "true", 0, ""],
            [11, "Freeform", "Points at hidden name", "false", 5, 10],
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tags.csv"
            with path.open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["id", "type", "name", "canonical", "cached_count", "merger_id"])
                writer.writerows(rows)
            data, report = audit_tags(path)
        self.assertEqual(report["usable_same_type_synonyms"], 1)
        self.assertEqual(data["id"][data["eligible"]].tolist(), [2])
        self.assertEqual(report["named_canonical_catalogue"], 2)
        self.assertEqual(report["checks"]["redacted_at_least_five_uses"], 1)
        self.assertEqual(report["visible_unmerged_noncanonical"], 1)
        self.assertEqual(report["cross_type_named_positives"], 1)
        self.assertEqual(report["named_merged_reaching_canonical_merger_conflict"], 1)
        self.assertEqual(report["merged_to_canonical_without_name"], 1)

    def test_works_accepts_missing_word_count_and_checks_tag_references(self):
        data = {"id": np.array([1, 2, 3]), "visible": np.array([True, True, False]),
                "eligible": np.array([False, True, False])}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "works.csv"
            with path.open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["creation date", "language", "restricted", "complete", "word_count", "tags", ""])
                writer.writerow(["2021-02-26", "en", "false", "true", "", "1+2+999"])
                writer.writerow(["2021-02-25", "en", "true", "false", 20, "3"])
            report = audit_works(path, data)
        self.assertEqual(report["rows"], 2)
        self.assertEqual(report["missing_word_counts"], 1)
        self.assertEqual(report["missing_tag_references"], 1)
        self.assertEqual(report["redacted_or_blank_tag_references"], 1)
        self.assertEqual(report["works_with_usable_synonym"], 1)
        self.assertEqual(report["distinct_usable_synonyms_observed_in_works"], 1)


if __name__ == "__main__":
    unittest.main()
