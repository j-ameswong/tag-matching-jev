#!/usr/bin/env python3
"""Independently recount top-ten Jev outcomes from benchmark IDs and API evidence."""

import csv
import json
from pathlib import Path

from ao3_benchmark import fingerprint, load_benchmark, sha256, write_json
from ao3_jev import BASELINES, CONDITIONS, NONE, payload_for
from run_ao3_jev import OUTPUT, ROOT, checkpoint_path, ordered_hash
from run_ao3_representations import read_predictions


def main():
    benchmark = load_benchmark()
    rankings, _ = read_predictions("semantic", "template_alias_max")
    originals = {q["source_id"]: (q, r) for q, r in zip(benchmark.examples, rankings)}
    inputs = json.loads((ROOT / "inputs.json").read_text())
    manifest = json.loads((OUTPUT / "inputs.json").read_text())
    if sha256(ROOT / "inputs.json") != manifest["inputs_sha256"]:
        raise ValueError("Input artifact hash mismatch")
    cases = {c["source_id"]: c for c in inputs["cases"]}
    for source, case in cases.items():
        query, ranking = originals[source]
        if (case["canonical_id"] != query["canonical_id"] or case["incoming_tag"] != query["incoming_tag"]
                or case["example_kind"] != query["example_kind"] or case["top_ids"] != ranking["top_ids"]):
            raise ValueError("Frozen input no longer agrees with original benchmark/ranking")
    summary = json.loads((OUTPUT / "summary.json").read_text())
    if summary["status"] != "complete_exploratory_development":
        raise ValueError("Cannot certify an incomplete Jev experiment")
    rows = list(csv.DictReader((OUTPUT / "predictions.csv").open()))
    counts, seen, response_checks = {}, set(), 0
    for row in rows:
        name, source = row["condition"], int(row["source_id"])
        key = (name, source)
        if key in seen or name not in (*BASELINES, *CONDITIONS):
            raise ValueError("Duplicate or unexpected prediction")
        seen.add(key)
        query, ranking = originals[source]
        target, top = query["canonical_id"], ranking["top_ids"]
        selected = int(row["selected_id"]) if row["selected_id"] else None
        forced = int(row["forced_id"])
        if (int(row["canonical_id"]) != target or int(row["expected_rank"]) != ranking["expected_rank"]
                or row["example_kind"] != query["example_kind"]
                or selected is not None and selected not in top or forced not in top):
            raise ValueError("Invalid prediction IDs or rank")
        if name in CONDITIONS:
            wrapped = json.loads(checkpoint_path(cases[source], name).read_text())
            payload = payload_for(cases[source], inputs["candidates"], name)
            if wrapped["payload_sha256"] != ordered_hash(payload) or wrapped["result_sha256"] != fingerprint(wrapped["result"]):
                raise ValueError("Request/response integrity failure")
            answers = wrapped["result"]["response"]["answers"]
            if "canonical" in answers:
                chosen = answers["canonical"]["choice"]
                probabilities = answers["canonical"]["probabilities"]
                api_selected = None if chosen == NONE else int(chosen.removeprefix("tag_"))
            else:
                probabilities = {k: a["noul"] for k, a in answers.items()}
                api_selected = int(min(probabilities, key=lambda k: (-probabilities[k], int(k.removeprefix("tag_")))).removeprefix("tag_"))
            forced_key = min((k for k in probabilities if k != NONE), key=lambda k: (-probabilities[k], int(k.removeprefix("tag_"))))
            if selected != api_selected or forced != int(forced_key.removeprefix("tag_")):
                raise ValueError("Prediction disagrees with raw API evidence")
            response_checks += 1
        correct, present, top1 = selected == target, target in top, top[0] == target
        facts = {"correct": correct, "target_present": present, "baseline_correct": top1,
                 "forced_correct": forced == target, "abstained": selected is None,
                 "rescue": correct and not top1, "regression": top1 and not correct,
                 "selection_error": present and selected is not None and not correct,
                 "unnecessary_abstention": present and selected is None,
                 "correct_shortlist_rejection": not present and selected is None,
                 "wrong_acceptance_on_retrieval_miss": not present and selected is not None}
        if any(row[k] != str(v) for k, v in facts.items()):
            raise ValueError("Per-query outcome disagrees with independent reconstruction")
        count = counts.setdefault((name, query["example_kind"]), dict.fromkeys(["queries", "forced_rescues", "forced_regressions", *facts], 0))
        count["queries"] += 1
        count["forced_rescues"] += forced == target and not top1
        count["forced_regressions"] += forced != target and top1
        for key, value in facts.items():
            count[key] += value
    if len(seen) != len(originals) * (len(BASELINES) + len(CONDITIONS)):
        raise ValueError("Missing predictions")
    for row in summary["metrics"]:
        count = counts[(row["condition"], row["example_kind"])]
        metric = row["metrics"]
        matches = {"queries": "queries", "successes": "correct", "retrieved": "target_present",
                   "abstentions": "abstained", "forced_candidate_successes": "forced_correct",
                   "forced_rescues": "forced_rescues", "forced_regressions": "forced_regressions",
                   **{k: k for k in ("rescue", "regression", "selection_error", "unnecessary_abstention",
                                     "correct_shortlist_rejection", "wrong_acceptance_on_retrieval_miss")}}
        if any(metric[key] != count[value] for key, value in matches.items()):
            raise ValueError("Aggregate count mismatch")
        if abs(metric["overall_accuracy"]["micro"] - count["correct"] / count["queries"]) > 1e-12:
            raise ValueError("Overall denominator mismatch")
        if abs(metric["selection_accuracy_if_retrieved"]["micro"] - count["correct"] / count["target_present"]) > 1e-12:
            raise ValueError("Conditional denominator mismatch")
        if count["correct"] != count["baseline_correct"] + count["rescue"] - count["regression"]:
            raise ValueError("Rescue/regression identity failed")
        if (count["forced_correct"] != count["baseline_correct"] + count["forced_rescues"] - count["forced_regressions"]
                or abs(metric["forced_candidate_accuracy"]["micro"] - count["forced_correct"] / count["queries"]) > 1e-12):
            raise ValueError("Forced-candidate accounting failed")
        if count["queries"] != count["correct"] + count["selection_error"] + count["unnecessary_abstention"] + metric["retrieval_misses"]:
            raise ValueError("Error decomposition failed")
    result = {"status": "passed", "prediction_rows": len(rows), "raw_api_decisions_checked": response_checks,
              "original_shortlists_checked": len(originals), "aggregate_metrics_checked": len(summary["metrics"]),
              "checks": ["IDs against original benchmark", "identical original ten-candidate lists", "request order and response hashes",
                         "per-query decisions against raw API answers", "independent conditional and overall denominators",
                         "rescues minus regressions identity", "disjoint error decomposition", "identity controls kept separate"],
              "artifact_hashes": {str(p): sha256(p) for p in (OUTPUT / "summary.json", OUTPUT / "metrics.csv", OUTPUT / "predictions.csv")},
              "validator_sha256": sha256(Path(__file__))}
    write_json(OUTPUT / "validation.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
