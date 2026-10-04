"""Guard the Jev experiment's label boundary, cached requests and denominators."""

import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ao3_jev import (CONDITIONS, MODEL, NONE, outcome, parse_response,
                     payload_for, summarize)
from ao3_benchmark import fingerprint, write_json
from run_ao3_jev import ordered_hash, read_result, analyze


def fixture():
    case = {"source_id": 900, "incoming_tag": "test incoming", "top_ids": list(range(1, 11)),
            "canonical_id": 2, "canonical_name": "ANSWER MUST STAY PRIVATE",
            "expected_rank": 2, "context": {"Fandom": ["Example fandom"]},
            "example_kind": "synonym", "context_works": 3, "target_has_aliases": True}
    candidates = {str(i): {"name": f"candidate {i}", "aliases": [f"alias {i}"],
                           "context": {"Freeform": ["Context only"]}} for i in case["top_ids"]}
    return case, candidates


def response_for(payload, selected="tag_2"):
    values = {key: float(key == selected) for key in payload["questions"]["canonical"]["criteria"]}
    return {"model": MODEL + "-20260917", "answers": {
        "canonical": {"type": "choice", "choice": selected, "probabilities": values}}}


class JevTests(unittest.TestCase):
    def test_changing_labels_cannot_change_any_prompt(self):
        case, candidates = fixture()
        changed = {**case, "canonical_id": 999, "canonical_name": "OTHER SECRET", "expected_rank": 99}
        for name in CONDITIONS:
            self.assertEqual(payload_for(case, candidates, name), payload_for(changed, candidates, name))
            self.assertNotIn(case["canonical_name"], json.dumps(payload_for(case, candidates, name)))

    def test_name_only_and_alias_and_context_ablation(self):
        case, candidates = fixture()
        names = json.dumps(payload_for(case, candidates, "ao3_names"))
        aliases = json.dumps(payload_for(case, candidates, "ao3_aliases"))
        context = json.dumps(payload_for(case, candidates, "ao3_context"))
        self.assertNotIn("alias 1", names)
        self.assertIn("alias 1", aliases)
        self.assertNotIn("Context only", aliases)
        self.assertIn("Context only", context)
        self.assertIn("Example fandom", context)

    def test_shuffle_changes_order_but_neither_choices_nor_keys(self):
        case, candidates = fixture()
        ranked = payload_for(case, candidates, "ao3_aliases")
        shuffled = payload_for(case, candidates, "ao3_aliases_shuffled")
        a, b = (p["questions"]["canonical"]["criteria"] for p in (ranked, shuffled))
        self.assertEqual(a, b)
        self.assertNotEqual(list(a), list(b))
        self.assertEqual(list(b)[-1], NONE)
        self.assertNotEqual(ordered_hash(ranked), ordered_hash(shuffled))

    def test_pairwise_questions_do_not_depend_on_other_candidates(self):
        case, candidates = fixture()
        before = payload_for(case, candidates, "ao3_pairwise_aliases")
        candidates["3"]["name"] = "changed distractor"
        after = payload_for(case, candidates, "ao3_pairwise_aliases")
        self.assertEqual(before["questions"]["tag_2"], after["questions"]["tag_2"])
        self.assertEqual(before["state"], after["state"])

    def test_none_is_not_a_correct_canonical_match_on_retrieval_miss(self):
        case, _ = fixture()
        case["canonical_id"] = 100
        result = outcome(case, {"selected_id": None, "forced_id": 1})
        self.assertFalse(result["correct"])
        self.assertTrue(result["correct_shortlist_rejection"])
        self.assertFalse(result["unnecessary_abstention"])

    def test_retrieval_and_selection_denominators_and_rescue_accounting(self):
        template, _ = fixture()
        cases = [{**template, "canonical_id": t} for t in (1, 2, 3, 100)]
        decisions = [{"selected_id": p, "forced_id": f} for p, f in ((2, 2), (2, 2), (None, 3), (None, 1))]
        metrics = summarize(cases, decisions)
        self.assertEqual(metrics["successes"], 1)
        self.assertEqual(metrics["overall_accuracy"]["micro"], .25)
        self.assertAlmostEqual(metrics["selection_accuracy_if_retrieved"]["micro"], 1 / 3)
        self.assertEqual(metrics["shortlist_decision_accuracy"], .5)
        self.assertEqual((metrics["rescue"], metrics["regression"]), (1, 1))
        self.assertEqual((metrics["selection_error"], metrics["unnecessary_abstention"], metrics["retrieval_misses"]), (1, 1, 1))
        self.assertEqual(metrics["forced_candidate_successes"], 2)
        self.assertEqual((metrics["forced_rescues"], metrics["forced_regressions"]), (2, 1))
        self.assertEqual(metrics["forced_difference_vs_retrieval_top1"]["micro"], .25)

    def test_no_retrieved_targets_has_null_conditional_accuracy(self):
        case, _ = fixture()
        case["canonical_id"] = 100
        result = summarize([case], [{"selected_id": None, "forced_id": 1}])
        self.assertIsNone(result["selection_accuracy_if_retrieved"])
        self.assertIsNone(result["mapping_precision_on_known_positives"])

    def test_invalid_responses_fail_instead_of_guessing(self):
        case, candidates = fixture()
        payload = payload_for(case, candidates, "ao3_aliases")
        good = response_for(payload)
        self.assertEqual(parse_response(good, payload)["selected_id"], 2)
        for bad in ({"error": "failure"}, {**good, "model": "another/model"}, {**good, "answers": {}}):
            with self.assertRaises(ValueError):
                parse_response(bad, payload)
        for value in (float("nan"), -1, 2, True):
            bad = copy.deepcopy(good)
            bad["answers"]["canonical"]["probabilities"]["tag_2"] = value
            with self.assertRaises(ValueError):
                parse_response(bad, payload)
        disagreement = copy.deepcopy(good)
        disagreement["answers"]["canonical"]["choice"] = "tag_3"
        parsed = parse_response(disagreement, payload)
        self.assertEqual(parsed["selected_id"], 3)
        self.assertEqual(parsed["forced_id"], 2)
        self.assertTrue(parsed["choice_disagrees_with_argmax"])

    def test_choice_abstention_and_numeric_forced_tie(self):
        case, candidates = fixture()
        payload = payload_for(case, candidates, "ao3_aliases")
        response = response_for(payload, NONE)
        result = parse_response(response, payload)
        self.assertIsNone(result["selected_id"])
        self.assertEqual(result["forced_id"], 1)

    def test_pairwise_probabilities_are_not_a_choice_distribution(self):
        case, candidates = fixture()
        payload = payload_for(case, candidates, "ao3_pairwise_aliases")
        response = {"model": MODEL, "answers": {key: {"type": "noul", "noul": .8} for key in payload["questions"]}}
        self.assertEqual(parse_response(response, payload)["selected_id"], 1)

    def test_cached_response_is_bound_to_prompt_and_response(self):
        case, candidates = fixture()
        payload = payload_for(case, candidates, "ao3_aliases")
        result = {"response": response_for(payload)}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "response.json"
            write_json(path, {"payload_sha256": ordered_hash(payload), "result_sha256": fingerprint(result), "result": result})
            with patch("run_ao3_jev.checkpoint_path", return_value=path):
                self.assertEqual(read_result(case, candidates, "ao3_aliases")[0]["selected_id"], 2)
                with self.assertRaises(ValueError):
                    read_result(case, candidates, "ao3_aliases_shuffled")
                result["response"]["model"] = "bad/model"
                write_json(path, {"payload_sha256": ordered_hash(payload), "result_sha256": "tampered", "result": result})
                with self.assertRaises(ValueError):
                    read_result(case, candidates, "ao3_aliases")

    def test_pending_requests_keep_accuracy_null_and_identities_separate(self):
        case, candidates = fixture()
        cases = [case, {**case, "source_id": 901},
                 {**case, "source_id": 1, "canonical_id": 1, "example_kind": "canonical_name"}]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_json(output / "protocol.json", {})
            with patch("run_ao3_jev.ROOT", output), patch("run_ao3_jev.OUTPUT", output), \
                    patch("run_ao3_jev.read_result", return_value=None), redirect_stdout(io.StringIO()):
                analyze({"cases": cases, "candidates": candidates})
            summary = json.loads((output / "summary.json").read_text())
            for row in summary["metrics"]:
                self.assertEqual(row["expected_queries"], 2 if row["example_kind"] == "synonym" else 1)
                if row["condition"] in CONDITIONS:
                    self.assertEqual(row["status"], "pending")
                    self.assertEqual(row["completed_queries"], 0)
                    self.assertIsNone(row["metrics"])


if __name__ == "__main__":
    unittest.main()
