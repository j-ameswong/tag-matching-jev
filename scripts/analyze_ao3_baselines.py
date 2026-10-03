#!/usr/bin/env python3
"""Independently check reported hit counts and summarize development errors.

The lexical-difficulty bands and error examples are descriptive follow-up
analysis, not frozen selection criteria, retrieval features or tuned thresholds.
"""

import argparse
from collections import defaultdict
import gzip
import json
from pathlib import Path

from rapidfuzz.distance import Levenshtein

from ao3_benchmark import (DEFAULT_DATASET, DEFAULT_OUTPUT, hash_order, load_benchmark,
                           sha256, write_csv, write_json)


KS = (1, 3, 5, 10)
ERROR_SEED = "ao3-stage1-error-review-v1"


def analyze(dataset, output):
    benchmark = load_benchmark(dataset)
    summary = json.loads((output / "summary.json").read_text())
    if benchmark.provenance != summary["dataset"]:
        raise ValueError("Results do not belong to the frozen benchmark")
    for name, expected in summary["source_sha256"].items():
        if sha256(Path(__file__).parent / name) != expected:
            raise ValueError(f"Scoring source changed: {name}")
    examples = {r["example_id"]: r for r in benchmark.examples}
    names = {r["canonical_id"]: r["name"] for r in benchmark.catalogue}
    candidate_ids = set(names)
    predictions = defaultdict(dict)
    prediction_path = Path(summary["predictions"]["path"])
    if sha256(prediction_path) != summary["predictions"]["sha256"]:
        raise ValueError("Prediction checksum mismatch")
    with gzip.open(prediction_path, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            query = examples[row["example_id"]]
            key = (row["method"], row["candidate_policy"], row["example_kind"])
            if (row["example_id"] in predictions[key] or row["source_id"] != query["source_id"]
                    or row["expected_canonical_id"] != query["canonical_id"]
                    or row["split"] != "development" or row["type"] != "Freeform"
                    or row["example_kind"] != query["example_kind"]):
                raise ValueError("Prediction/source mismatch or duplicate query")
            if len(row["top_ids"]) != len(set(row["top_ids"])) or not set(row["top_ids"]) <= candidate_ids:
                raise ValueError("Invalid or duplicate candidate IDs")
            predictions[key][row["example_id"]] = row
    checks = 0
    for metric in summary["metrics"]:
        rows = predictions[(metric["method"], metric["candidate_policy"], metric["example_kind"])]
        if len(rows) != metric["examples"]:
            raise ValueError("Reported number of examples disagrees with predictions")
        for k in KS:
            successes, groups = [], defaultdict(list)
            for example_id, row in rows.items():
                # Recompute hits from candidate IDs, independently of cached rank fields.
                target = examples[example_id]["canonical_id"]
                hit = target in row["top_ids"][:k]
                successes.append(hit)
                groups[target].append(hit)
            micro = sum(successes) / len(successes)
            macro = sum(sum(values) / len(values) for values in groups.values()) / len(groups)
            if (sum(successes) != metric[f"hits@{k}"] or len(groups) != metric["canonical_groups"]
                    or abs(micro - metric[f"recall@{k}"]["micro"]) > 1e-12
                    or abs(macro - metric[f"recall@{k}"]["macro"]) > 1e-12):
                raise ValueError("Summary metric disagrees with candidate membership")
            checks += 1
    # Difficulty uses the labelled pair only for analysis, never for retrieval.
    synonyms = [r for r in benchmark.examples if r["example_kind"] == "synonym"]
    bands = defaultdict(list)
    for row in synonyms:
        similarity = Levenshtein.normalized_similarity(row["incoming_tag"], row["canonical_name"])
        band = "0.00-<0.25" if similarity < 0.25 else "0.25-<0.50" if similarity < 0.5 else "0.50-<0.75" if similarity < 0.75 else "0.75-1.00"
        bands[band].append(row)
    difficulty = []
    methods = [m for m, p, kind in predictions if p == "canonical_names" and kind == "synonym" and not m.startswith("exact_")]
    for band, rows in sorted(bands.items()):
        for method in sorted(methods):
            pred = predictions[(method, "canonical_names", "synonym")]
            entry = {"raw_pair_levenshtein_similarity": band, "method": method, "examples": len(rows),
                     "canonical_groups": len({r["canonical_id"] for r in rows})}
            for k in KS:
                entry[f"recall@{k}"] = sum(r["canonical_id"] in pred[r["example_id"]]["top_ids"][:k] for r in rows) / len(rows)
            difficulty.append(entry)
    artifacts = {"lexical_difficulty.csv": write_csv(output / "lexical_difficulty.csv", list(difficulty[0]), difficulty)}
    semantic = predictions[("text_embedding_3_small_cosine", "canonical_names", "synonym")]
    misses = [r for r in synonyms if r["canonical_id"] not in semantic[r["example_id"]]["top_ids"]]
    errors = []
    for row in sorted(misses, key=lambda r: (hash_order(ERROR_SEED, r["source_id"]), r["source_id"]))[:10]:
        item = {"source_id": row["source_id"], "query": row["incoming_tag"], "canonical_id": row["canonical_id"],
                "expected_name": row["canonical_name"]}
        for method in sorted(methods):
            item[method + "_rank"] = predictions[(method, "canonical_names", "synonym")][row["example_id"]]["expected_rank"]
        item["semantic_top3_names"] = json.dumps([names[i] for i in semantic[row["example_id"]]["top_ids"][:3]], ensure_ascii=False)
        errors.append(item)
    if errors:
        artifacts["semantic_errors.csv"] = write_csv(output / "semantic_errors.csv", list(errors[0]), errors)
    analysis = {"status": "complete", "kind": "descriptive development follow-up",
                "source_sha256": sha256(Path(__file__)), "summary_sha256": sha256(output / "summary.json"),
                "independent_membership_metric_checks": checks,
                "error_selection": {"seed": ERROR_SEED, "pool": "semantic canonical-name Recall@10 misses",
                                    "pool_size": len(misses), "selected": len(errors),
                                    "rule": "first ten by SHA256(seed:source_id), numeric ID tie break"},
                "difficulty": "Raw query/label name normalized Levenshtein similarity, quartile-width bands; descriptive only",
                "artifacts": artifacts}
    write_json(output / "analysis.json", analysis)
    print(json.dumps(analysis, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    analyze(args.dataset, args.output)


if __name__ == "__main__":
    main()
