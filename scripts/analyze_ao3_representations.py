#!/usr/bin/env python3
"""Independent shortlist checks, paired estimates and coverage strata for Stage 2."""

from collections import Counter
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path

import numpy as np

from ao3_benchmark import fingerprint, hash_order, load_benchmark, sha256, write_csv, write_json
from ao3_representations import CONTEXT_VIEWS, OUTPUT, PROTOCOL, ROOT, selected_aliases, work_assignment
from ao3_scoring import GroupStatistics, KS
from run_ao3_representations import read_predictions


def validate_rows(rows, examples, candidate_ids):
    if len(rows) != len(examples):
        raise ValueError("Wrong prediction count")
    checks = 0
    for row, query in zip(rows, examples):
        rank, top, scores = row["expected_rank"], row["top_ids"], row["top_scores"]
        if not 1 <= rank <= len(candidate_ids) or len(top) != 10 or len(set(top)) != 10 or not set(top) <= candidate_ids:
            raise ValueError("Invalid rank or canonical shortlist")
        if not np.isfinite(scores + [row["expected_score"]]).all():
            raise ValueError("Non-finite prediction score")
        if any(scores[i] < scores[i + 1] or (scores[i] == scores[i + 1] and top[i] > top[i + 1]) for i in range(9)):
            raise ValueError("Ranking order/tie policy violated")
        if rank <= 10 and top[rank - 1] != query["canonical_id"]:
            raise ValueError("Target rank disagrees with shortlist")
        for k in KS:
            if (rank <= k) != (query["canonical_id"] in top[:k]):
                raise ValueError("Independent membership check failed")
            checks += 1
    return checks


def stage1_agreement(benchmark, predictions):
    summary = json.loads(Path("results/ao3/stage1/summary.json").read_text())
    path = Path(summary["predictions"]["path"])
    if sha256(path) != summary["predictions"]["sha256"]:
        raise ValueError("Stage 1 prediction checksum failed")
    indexes = {r["example_id"]: i for i, r in enumerate(benchmark.examples)}
    methods = {"similarity": "lexical/raw", "text_embedding_3_small_cosine": "semantic/raw"}
    checked, mismatch = Counter(), Counter()
    with gzip.open(path, "rt") as handle:
        for line in handle:
            row = json.loads(line)
            if row["candidate_policy"] != "canonical_names" or row["method"] not in methods:
                continue
            key = methods[row["method"]]
            current = predictions[key][indexes[row["example_id"]]]
            checked[key] += 1
            mismatch[key] += current["expected_rank"] != row["expected_rank"] or current["top_ids"] != row["top_ids"]
    if any(n != len(benchmark.examples) for n in checked.values()) or len(checked) != 2 or any(mismatch.values()):
        raise ValueError(f"Name-only reference did not reproduce Stage 1: {dict(mismatch)}")
    return {"checked_queries": dict(checked), "rank_or_shortlist_mismatches": dict(mismatch)}


def validate_profiles(benchmark, profiles):
    manifest = json.loads((ROOT / "profiles-manifest.json").read_text())
    if manifest["profiles_sha256"] != sha256(ROOT / "profiles.json") or manifest["protocol_sha256"] != fingerprint(PROTOCOL):
        raise ValueError("Profile provenance changed")
    support, query_works = set(), set()
    expected = {str(r["source_id"]): r["canonical_id"] for r in benchmark.examples}
    for side in ("candidate", "query"):
        for key, profile in profiles[side].items():
            target = int(key) if side == "candidate" else expected[key]
            if profile["words_n"] != sum(profile["bands"]) or profile["words_n"] > profile["n"]:
                raise ValueError("Length denominators do not agree")
            if len(profile["sample_work_ordinals"]) != profile["sample_n"] or profile["sample_n"] > 32:
                raise ValueError("Work sample size changed")
            if any(r["id"] == target for rows in profile["co_tags"].values() for r in rows):
                raise ValueError("Equivalent canonical leaked into context")
            (support if side == "candidate" else query_works).update(profile["sample_work_ordinals"])
    if support & query_works:
        raise ValueError("Candidate and query context share a work")
    for ordinal in support:
        if work_assignment(manifest["works_sha256"], ordinal)[0] >= 6:
            raise ValueError("Candidate profile includes a non-support work")
    for ordinal in query_works:
        if work_assignment(manifest["works_sha256"], ordinal)[0] not in (6, 7):
            raise ValueError("Query profile includes a reserved/support work")
    blocked = {r["source_id"] for r in benchmark.examples if r["example_kind"] == "synonym"}
    chosen = [r for values in selected_aliases(benchmark).values() for r in values]
    if any(r["source_id"] in blocked or r["split"] != "development" for r in chosen):
        raise ValueError("Forbidden alias in candidate examples")
    return {"distinct_sampled_support_works": len(support), "distinct_sampled_query_works": len(query_works),
            "overlapping_sampled_works": 0, "selected_alias_examples_checked": len(chosen),
            "profile_file_sha256_verified": True, "equivalent_context_mask_verified": True}


def report(benchmark):
    profiles = json.loads((ROOT / "profiles.json").read_text())
    profile_validation = validate_profiles(benchmark, profiles)
    inputs = json.loads((ROOT / "inputs-manifest.json").read_text())
    if inputs["protocol_sha256"] != fingerprint(PROTOCOL) or inputs["benchmark"] != benchmark.provenance:
        raise ValueError("Inputs belong to another protocol or benchmark")
    for filename in ("texts.json", "positions.npz", "profiles.json"):
        if sha256(ROOT / filename) != inputs[filename + "_sha256"]:
            raise ValueError(f"Representation source changed: {filename}")
    embedding_metadata = json.loads((ROOT / "embeddings/manifest.json").read_text())
    if sha256(ROOT / "embeddings/vectors.npy") != embedding_metadata["vectors_sha256"]:
        raise ValueError("Merged embedding matrix changed")
    predictions, metadata = {}, {}
    checks = 0
    candidates = {r["canonical_id"] for r in benchmark.catalogue}
    for method, conditions in (("lexical", PROTOCOL["lexical_conditions"]), ("semantic", PROTOCOL["semantic_conditions"])):
        for condition in conditions:
            result = read_predictions(method, condition)
            if result is None:
                raise ValueError(f"Missing {method}/{condition}")
            rows, meta = result
            recipe = meta["recipe"]
            if recipe["protocol_sha256"] != fingerprint(PROTOCOL):
                raise ValueError("Prediction belongs to another protocol")
            if method == "semantic":
                if (recipe["inputs_sha256"] != fingerprint(inputs)
                        or recipe["vectors_sha256"] != embedding_metadata["vectors_sha256"]
                        or recipe["condition"] != condition):
                    raise ValueError("Prediction inputs or vector provenance changed")
            elif recipe["benchmark"] != benchmark.provenance:
                raise ValueError("Lexical prediction benchmark changed")
            key = method + "/" + condition
            predictions[key], metadata[key] = rows, meta
            checks += validate_rows(rows, benchmark.examples, candidates)
    replay = stage1_agreement(benchmark, predictions)
    masks = {kind: np.asarray([r["example_kind"] == kind for r in benchmark.examples]) for kind in ("synonym", "canonical_name")}
    groups = np.asarray([r["canonical_id"] for r in benchmark.examples])
    statistics = {kind: GroupStatistics(groups[mask]) for kind, mask in masks.items()}
    ranks = {key: np.asarray([r["expected_rank"] for r in rows]) for key, rows in predictions.items()}
    metrics, table = [], []
    for key, values in ranks.items():
        method, condition = key.split("/")
        for kind, mask in masks.items():
            entry = {"method": method, "condition": condition, "example_kind": kind, "examples": int(mask.sum()),
                     "canonical_groups": len(set(groups[mask]))}
            flat = dict(entry)
            for k in KS:
                entry[f"recall@{k}"] = statistics[kind].calculate(values[mask] <= k)
                flat[f"recall@{k}"] = entry[f"recall@{k}"]["micro"]
                flat[f"macro_recall@{k}"] = entry[f"recall@{k}"]["macro"]
            flat["recall@10_ci95_low"], flat["recall@10_ci95_high"] = entry["recall@10"]["micro_ci95"]
            metrics.append(entry)
            table.append(flat)
    pairs = [("semantic/template", "semantic/raw"), ("semantic/aliases", "semantic/template"),
             ("semantic/aliases", "semantic/raw"),
             ("lexical/global_aliases", "lexical/raw"), ("lexical/selected_aliases", "lexical/raw"),
             ("semantic/aliases", "lexical/selected_aliases"), ("semantic/aliases", "lexical/global_aliases"),
             ("semantic/cotags_one_work", "semantic/cotags_both"), ("semantic/all_name_anchor", "semantic/raw"),
             ("semantic/all_name_anchor", "semantic/aliases"), ("semantic/cotags_both", "semantic/fandom_both"),
             ("semantic/cotags_length_both", "semantic/cotags_both"), ("semantic/all_both", "semantic/cotags_length_both")]
    for view in CONTEXT_VIEWS:
        pairs += [(f"semantic/{view}_candidate", "semantic/aliases"),
                  (f"semantic/{view}_both", "semantic/aliases"),
                  (f"semantic/{view}_both", f"semantic/{view}_candidate")]
    paired = []
    mask = masks["synonym"]
    for a, b in pairs:
        for k in KS:
            ah, bh = ranks[a][mask] <= k, ranks[b][mask] <= k
            paired.append({"a": a, "b": b, "k": k, "a_only_hits": int((ah & ~bh).sum()),
                           "b_only_hits": int((bh & ~ah).sum()),
                           "difference": statistics["synonym"].calculate(ah.astype(float) - bh.astype(float))})
    sampled = [r for r in benchmark.examples if r["example_kind"] == "synonym"]
    query_n = np.asarray([profiles["query"][str(r["source_id"])]["n"] for r in sampled])
    candidate_n = np.asarray([profiles["candidate"][str(r["canonical_id"])]["n"] for r in sampled])
    alias_groups = selected_aliases(benchmark)
    has_aliases = np.asarray([bool(alias_groups.get(r["canonical_id"])) for r in sampled])
    strata_masks = {"all": np.ones(len(sampled), dtype=bool), "query_no_works": query_n == 0,
                    "target_has_selected_aliases": has_aliases, "target_has_no_selected_aliases": ~has_aliases,
                    "query_has_any_work": query_n > 0,
                    "query_1_to_2_works": (query_n > 0) & (query_n < 3),
                    "query_3_to_9_works": (query_n >= 3) & (query_n < 10), "query_at_least_10_works": query_n >= 10,
                    "both_sides_at_least_3_works": (query_n >= 3) & (candidate_n >= 3),
                    "non_ascii": np.asarray([not r["incoming_tag"].isascii() for r in sampled])}
    positions = np.load(ROOT / "positions.npz", allow_pickle=False)
    target_positions = np.searchsorted(np.asarray([r["canonical_id"] for r in benchmark.catalogue]),
                                      np.asarray([r["canonical_id"] for r in sampled]))
    for view in CONTEXT_VIEWS:
        strata_masks[f"query_has_{view}_text"] = (positions["query_" + view] != positions["query_template"])[mask]
        target_has_text = (positions["candidate_" + view] != positions["candidate_aliases"])[target_positions]
        strata_masks[f"both_sides_have_{view}_text"] = strata_masks[f"query_has_{view}_text"] & target_has_text
    strata = []
    for name, selected in strata_masks.items():
        if not selected.any():
            continue
        for key, values in ranks.items():
            chosen = values[mask][selected]
            strata.append({"stratum": name, "condition": key, "examples": len(chosen),
                           **{f"recall@{k}": float((chosen <= k).mean()) for k in KS}})
    # Deterministic diagnostic sample of gains/losses for each context configuration.
    names = {r["canonical_id"]: r["name"] for r in benchmark.catalogue}
    errors = []
    example_indexes = np.flatnonzero(mask)
    reference = ranks["semantic/aliases"][mask] <= 10
    for key in [f"semantic/{v}_both" for v in CONTEXT_VIEWS] + ["semantic/all_name_anchor"]:
        hits = ranks[key][mask] <= 10
        for change, selected in (("gain", hits & ~reference), ("loss", reference & ~hits)):
            indexes = sorted(np.flatnonzero(selected), key=lambda i: hash_order("ao3-stage2-diagnostics-v1", sampled[i]["source_id"]))[:5]
            for i in indexes:
                query, row_index = sampled[i], example_indexes[i]
                result = predictions[key][row_index]
                errors.append({"condition": key, "change_vs_aliases": change, "source_id": query["source_id"],
                               "query": query["incoming_tag"], "expected": query["canonical_name"],
                               "expected_rank": result["expected_rank"], "top_prediction": names[result["top_ids"][0]],
                               "query_context_works": int(query_n[i]), "candidate_support_works": int(candidate_n[i])})
    validation = {"status": "passed", "prediction_conditions": len(predictions),
                  "prediction_rows": len(predictions) * len(benchmark.examples), "independent_membership_checks": checks,
                  "stage1_name_only_replay": replay, "profiles": profile_validation,
                  "embedding_matrix_and_representation_checksums_verified": True,
                  "calibration_and_test_evaluated": False}
    summary = {"stage": 2, "status": "complete_for_dump_based_representations",
               "generated_at_utc": datetime.now(timezone.utc).isoformat(), "protocol": PROTOCOL,
               "benchmark": benchmark.provenance, "coverage": json.loads((OUTPUT / "coverage.json").read_text()),
               "representations": inputs, "embeddings": json.loads((OUTPUT / "embeddings.json").read_text()),
               "metrics": metrics, "paired_comparisons": paired, "validation": validation,
               "predictions": metadata,
               "source_sha256": {p.name: sha256(p) for p in [Path(__file__), Path("scripts/ao3_representations.py"),
                                    Path("scripts/run_ao3_representations.py"), Path("scripts/ao3_work_html.py")]},
               "limitations": ["Development exploration across multiple prespecified conditions; no final-test winner established",
                               "Query profiles describe recurring tags with historical context; one-work condition covers a different input setting",
                               "Canonical names from all splits are candidates; support profiles may use their direct canonical occurrences, but no held-out alias membership",
                               "Co-tags are associations, not synonym labels or curated definitions",
                               "Fixed-size work samples and rare context coverage limit profile estimates",
                               "Bootstrap groups by canonical target; shared query works can create dependence between different target groups",
                               "Single HTML fixture validates extraction only; summaries and engagement were not benchmarked",
                               "Provider supplies an unversioned model alias; vector checksums freeze these acquisitions"]}
    write_json(OUTPUT / "summary.json", summary)
    write_json(OUTPUT / "validation.json", validation)
    write_csv(OUTPUT / "metrics.csv", list(table[0]), table)
    write_csv(OUTPUT / "strata.csv", list(strata[0]), strata)
    if errors:
        write_csv(OUTPUT / "diagnostics.csv", list(errors[0]), errors)
    print(json.dumps(validation, indent=2), flush=True)
    for entry in table:
        if entry["example_kind"] == "synonym":
            print(f"{entry['method']}/{entry['condition']}: R1={entry['recall@1']:.4f} R10={entry['recall@10']:.4f}", flush=True)


if __name__ == "__main__":
    report(load_benchmark())
