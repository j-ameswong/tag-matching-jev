"""Unlabelled data must not silently become no-match ground truth."""

import csv
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ao3_unmerged_review import (LABEL_FIELDS, PROPOSAL_FIELDS, initialize_editable_csv,
                                 is_eligible, sample_population, select_challenge, top_row)
from analyze_ao3_review import (baseline_proposals, measure_actions, proportion,
                                report_partition, validate_proposals, validate_references)
from ao3_review_html import write_review_html


def example(source_id=1, partition="calibration"):
    rankings = {m: {"top_ids": [10, 20], "top_scores": [.8, .7]}
                for m in ("template", "template_alias_max", "lexical_alias_max")}
    return {"source_id": source_id, "partition": partition, "arm": "random", "rankings": rankings}


def reference(source_id=1, judgment="synonym", target="10"):
    return {"source_id": str(source_id), "reviewer": "Independent reviewer", "judgment": judgment,
            "canonical_id": target, "proposed_name": "", "confidence": "high",
            "catalogue_checked": "yes", "rationale": "Fixture evidence"}


class UnmergedReviewTests(unittest.TestCase):
    def test_population_excludes_redacted_merged_canonical_and_other_types(self):
        row = {"type": "Freeform", "name": "Name", "canonical": "false", "merger_id": ""}
        self.assertTrue(is_eligible(row))
        for overrides in ({"name": "Redacted"}, {"name": " "}, {"merger_id": "5"},
                          {"canonical": "true"}, {"type": "Relationship"}):
            self.assertFalse(is_eligible({**row, **overrides}))

    def test_random_screen_and_review_splits_are_disjoint_and_order_independent(self):
        rows = [{"source_id": i, "name": str(i), "cached_count": 5} for i in range(150)]
        a, screen = sample_population(rows, 40, 100)
        b, other_screen = sample_population(list(reversed(rows)), 40, 100)
        self.assertEqual((a, screen), (b, other_screen))
        self.assertFalse({r["source_id"] for r in a} & {r["source_id"] for r in screen})
        self.assertEqual(sum(r["partition"] == "calibration" for r in a), 20)
        with self.assertRaises(ValueError):
            sample_population(rows + [rows[0]], 40, 100)

    def test_challenge_exhausted_strata_fall_back_without_duplicates(self):
        screen = [{"source_id": i, "name": "ASCII", "cached_count": i + 5} for i in range(150)]
        scores = {str(r["source_id"]): example()["rankings"] for r in screen}
        a = select_challenge(screen, scores, {10})
        b = select_challenge(list(reversed(screen)), scores, {10})
        self.assertEqual(a, b)
        self.assertEqual(len({r["source_id"] for r in a}), 100)
        self.assertTrue(any(r["selection_reason"].endswith("_fallback") for r in a))

    def test_top_k_handles_cutoff_ties_and_has_no_expected_label(self):
        ids = np.arange(100, 120)
        scores = np.array([.3] * 20)
        scores[19] = .9
        result = top_row(scores, ids)
        self.assertEqual(result["top_ids"], [119] + list(range(100, 109)))
        self.assertNotIn("expected_rank", result)

    def test_reproduction_preserves_existing_review_work(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "judgments.csv"
            initialize_editable_csv(path, LABEL_FIELDS, [{"source_id": 1}])
            with path.open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=LABEL_FIELDS)
                writer.writeheader()
                writer.writerow(reference())
            before = path.read_bytes()
            initialize_editable_csv(path, LABEL_FIELDS, [{"source_id": 1}])
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaises(ValueError):
                initialize_editable_csv(path, LABEL_FIELDS, [{"source_id": 2}])

    def test_reference_requires_review_evidence_and_search_for_novelty(self):
        cases = [example()]
        valid = validate_references([reference()], cases, {10, 20})
        self.assertEqual(valid[1]["canonical_id"], 10)
        unnamed = {**reference(), "judgment": "new_canonical", "canonical_id": "", "proposed_name": ""}
        self.assertEqual(validate_references([unnamed], cases, {10, 20})[1]["judgment"], "new_canonical")
        for change in ({"reviewer": ""}, {"rationale": ""}, {"canonical_id": "999"},
                       {"judgment": "new_canonical", "canonical_id": "", "proposed_name": "New", "catalogue_checked": "no"},
                       {"judgment": "unresolved"}):
            with self.assertRaises(ValueError):
                validate_references([{**reference(), **change}], cases, {10, 20})

    def test_missing_and_duplicate_references_fail_closed(self):
        with self.assertRaises(ValueError):
            validate_references([], [example()], {10})
        with self.assertRaises(ValueError):
            validate_references([reference(), reference()], [example()], {10})

    def test_blank_judgments_produce_null_accuracy_not_false_negatives(self):
        blank = {f: "1" if f == "source_id" else "" for f in LABEL_FIELDS}
        refs = validate_references([blank], [example()], {10})
        report = report_partition([example()], refs)
        self.assertEqual(report["status"], "reference_review_pending")
        self.assertIsNone(report["shortlist_recall_on_reviewed_synonyms"]["template/recall@10"]["estimate"])
        self.assertIsNone(report["decision_baselines"]["always_template"]["resolved_synonym_precision"]["estimate"])

    def test_wrong_target_new_concept_and_unresolved_have_different_denominators(self):
        cases = [example(i) for i in range(1, 6)]
        refs = {1: {"judgment": "synonym", "canonical_id": 10},
                2: {"judgment": "synonym", "canonical_id": 20},
                3: {"judgment": "new_canonical"}, 4: {"judgment": "unresolved"}}
        metrics = measure_actions(cases, refs, baseline_proposals(cases, "always_template"))
        self.assertEqual(metrics["resolved_synonym_precision"]["estimate"], 1 / 3)
        self.assertEqual(metrics["resolved_false_merge_fraction"]["estimate"], 2 / 3)
        self.assertEqual(metrics["false_merge_rate_on_reference_new_concepts"]["estimate"], 1)
        self.assertEqual(metrics["merges_on_unresolved_references"], 1)
        self.assertEqual(metrics["merges_without_reference"], 1)

    def test_new_concept_decisions_and_abstentions_scored_separately(self):
        cases = [example(1), example(2), example(3)]
        refs = {1: {"judgment": "new_canonical"}, 2: {"judgment": "synonym", "canonical_id": 10},
                3: {"judgment": "unresolved"}}
        proposals = {1: {"action": "new_canonical", "proposed_name": "A"},
                     2: {"action": "new_canonical", "proposed_name": "B"}, 3: {"action": "abstain"}}
        report = measure_actions(cases, refs, proposals)
        self.assertEqual(report["new_canonical_decision_precision"]["estimate"], .5)
        self.assertEqual(report["proposal_coverage"]["estimate"], 2 / 3)
        self.assertIsNone(report["resolved_synonym_precision"]["estimate"])

    def test_system_proposals_cannot_silently_fill_reference_labels(self):
        rows = [{"source_id": "1", "action": "new_canonical", "canonical_id": "", "proposed_name": "", "rationale": "Model proposal"}]
        proposals = validate_proposals(rows, [example()], {10})
        report = report_partition([example()], {}, proposals)
        self.assertEqual(report["reviewed"], 0)
        self.assertIsNone(report["decision_baselines"]["supplied_proposals"]["new_canonical_decision_precision"]["estimate"])
        with self.assertRaises(ValueError):
            validate_references(rows, [example()], {10})

    def test_full_catalogue_reference_outside_shortlist_is_allowed(self):
        refs = validate_references([{**reference(), "canonical_id": "30"}], [example()], {10, 20, 30})
        report = report_partition([example()], refs)
        self.assertEqual(report["shortlist_recall_on_reviewed_synonyms"]["union/shortlist"]["estimate"], 0)

    def test_wilson_bounds_keep_zero_errors_uncertain(self):
        self.assertLess(proportion(200, 200)["wilson95"][0], .99)
        self.assertGreater(proportion(0, 200)["wilson95"][1], .01)
        self.assertIsNone(proportion(0, 0)["estimate"])

    def test_html_payload_escapes_tag_markup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = '</script><script>alert("bad")</script>'
            write_review_html(root, [], [{"canonical_id": 10, "name": name, "cached_count": 5}], {}, [], LABEL_FIELDS)
            payload = (root / "review-data.js").read_text()
            self.assertNotIn("<script>", payload)
            self.assertIn("\\u003c", payload)
            self.assertNotIn("innerHTML", (root / "review.html").read_text())


if __name__ == "__main__":
    unittest.main()
