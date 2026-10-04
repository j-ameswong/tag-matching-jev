#!/usr/bin/env python3
"""Prepare, run, resume and analyze Jev on the frozen best AO3 top-ten lists."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import numpy as np

from ao3_benchmark import fingerprint, load_benchmark, sha256, write_csv, write_json
from ao3_jev import (BASELINES, CONDITIONS, ENDPOINT, PROTOCOL, baseline_decision,
                     context_names, outcome, parse_response, payload_for, summarize)
from ao3_representations import ROOT as REPRESENTATIONS, freeze, selected_aliases
from ao3_scoring import GroupStatistics
from analyze_ao3_representations import validate_rows
from run_ao3_representations import prediction_path, read_predictions


ROOT = Path("data/ao3-jev-top10")
OUTPUT = Path("results/ao3/jev-top10")


def prepare():
    ROOT.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    benchmark = load_benchmark()
    ranking, ranking_meta = read_predictions("semantic", "template_alias_max")
    catalogue = {r["canonical_id"]: r for r in benchmark.catalogue}
    if ranking_meta["recipe"]["benchmark"] != benchmark.provenance:
        raise ValueError("Ranking belongs to a different benchmark")
    checks = validate_rows(ranking, benchmark.examples, set(catalogue))
    aliases = selected_aliases(benchmark)
    alias_rows = sorted((r for rows in aliases.values() for r in rows), key=lambda r: r["source_id"])
    if fingerprint([r["source_id"] for r in alias_rows]) != ranking_meta["recipe"]["alias_ids_sha256"]:
        raise ValueError("Alias support differs from retrieval")
    profile_meta = json.loads((REPRESENTATIONS / "profiles-manifest.json").read_text())
    if sha256(REPRESENTATIONS / "profiles.json") != profile_meta["profiles_sha256"]:
        raise ValueError("Work profiles changed")
    recipe = {"protocol": PROTOCOL, "benchmark": benchmark.provenance,
              "rankings_sha256": ranking_meta["sha256"],
              "rankings_metadata_sha256": sha256(prediction_path("semantic", "template_alias_max").with_suffix(".meta.json")),
              "profiles_sha256": profile_meta["profiles_sha256"],
              "selected_alias_ids_sha256": ranking_meta["recipe"]["alias_ids_sha256"]}
    freeze(OUTPUT / "protocol.json", recipe)
    path = ROOT / "inputs.json"
    if path.exists():
        inputs = json.loads(path.read_text())
        manifest = json.loads((OUTPUT / "inputs.json").read_text())
        if inputs["recipe"] != recipe or sha256(path) != manifest["inputs_sha256"]:
            raise ValueError("Frozen Jev inputs changed")
        return inputs
    profiles = json.loads((REPRESENTATIONS / "profiles.json").read_text())
    needed = {tag for row in ranking for tag in row["top_ids"]}
    candidates = {str(tag): {"name": catalogue[tag]["name"],
                            "aliases": [r["name"] for r in aliases.get(tag, [])],
                            "context": context_names(profiles["candidate"][str(tag)])}
                  for tag in sorted(needed)}
    cases = []
    for query, row in zip(benchmark.examples, ranking):
        profile = profiles["query"][str(query["source_id"])]
        if any(r["id"] == query["canonical_id"] for rows in profile["co_tags"].values() for r in rows):
            raise ValueError("Expected target leaked into query context")
        cases.append({**{key: query[key] for key in ("example_id", "example_kind", "source_id", "incoming_tag", "canonical_id", "canonical_name")},
                      "top_ids": row["top_ids"], "expected_rank": row["expected_rank"],
                      "context": context_names(profile), "context_works": profile["n"],
                      "target_has_aliases": bool(aliases.get(query["canonical_id"]))})
    sampled = [c for c in cases if c["example_kind"] == "synonym"]
    if len(sampled) != 2000 or sum(c["canonical_id"] in c["top_ids"] for c in sampled) != 1841:
        raise ValueError("Best Recall@10 reference was not reproduced")
    inputs = {"recipe": recipe, "cases": cases, "candidates": candidates}
    write_json(path, inputs)
    write_json(OUTPUT / "inputs.json", {
        "inputs_path": str(path), "inputs_sha256": sha256(path), "cases": len(cases),
        "synonyms": len(sampled), "identity_controls": len(cases) - len(sampled),
        "unique_shortlisted_canonicals": len(needed), "selected_aliases": len(alias_rows),
        "independent_top_k_membership_checks": checks,
        "query_profiles_with_context": sum(bool(c["context"]) for c in sampled),
        "source_hashes_at_preparation": {str(p): sha256(p) for p in (Path(__file__), Path(__file__).with_name("ao3_jev.py"))},
        "prepared_at_utc": datetime.now(timezone.utc).isoformat(),
    })
    return inputs


def request_decision(payload):
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key or any(c in key for c in "\r\n\0"):
        raise ValueError("Source the OpenRouter secret file to set OPENROUTER_API_KEY")
    started = time.perf_counter()
    attempts = []
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        handle.flush()
        command = ["curl", "--silent", "--show-error", "--connect-timeout", "10", "--max-time", "60",
                   "--header", "@-", "--data-binary", "@" + handle.name, "--write-out", "\n%{http_code}", ENDPOINT]
        for attempt in range(4):
            response = subprocess.run(command, input=(f"Authorization: Bearer {key}\nContent-Type: application/json\n").encode(),
                                      capture_output=True, check=False)
            raw, _, status_raw = response.stdout.rpartition(b"\n")
            status = int(status_raw) if status_raw.isdigit() else 0
            attempts.append({"curl_exit": response.returncode, "http_status": status})
            if response.returncode == 0 and status == 200:
                return {"response": json.loads(raw), "seconds": time.perf_counter() - started,
                        "attempts": attempts, "fetched_at_utc": datetime.now(timezone.utc).isoformat()}
            transient = response.returncode in (7, 18, 28, 52, 56) or status in (408, 429) or status >= 500
            if not transient or attempt == 3:
                # Never log response bodies or credentials on failed requests.
                raise RuntimeError(f"Jev request failed: curl={response.returncode}, HTTP={status}, attempts={attempt + 1}")
            time.sleep(2 ** (attempt + 1))
    raise AssertionError("Unreachable")


def checkpoint_path(case, condition):
    return ROOT / "responses" / condition / f"{case['example_kind']}-{case['source_id']}.json"


def read_result(case, candidates, condition):
    path = checkpoint_path(case, condition)
    if not path.exists():
        return None
    cached = json.loads(path.read_text())
    payload = payload_for(case, candidates, condition)
    # fingerprint() sorts object keys, so also hash serialized request bytes to bind option order.
    if cached["payload_sha256"] != ordered_hash(payload) or cached["result_sha256"] != fingerprint(cached["result"]):
        raise ValueError(f"Jev cache integrity failure: {path}")
    result = cached["result"]
    return parse_response(result["response"], payload), result


def ordered_hash(payload):
    import hashlib
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def run_one(case, candidates, condition):
    cached = read_result(case, candidates, condition)
    if cached is not None:
        return False
    payload = payload_for(case, candidates, condition)
    result = request_decision(payload)
    # Save provider evidence before parsing, so a malformed success response stays inspectable.
    write_json(checkpoint_path(case, condition), {"payload_sha256": ordered_hash(payload),
               "result_sha256": fingerprint(result), "result": result})
    parse_response(result["response"], payload)
    return True


def run(inputs, conditions, workers, limit=None):
    cases, candidates = inputs["cases"], inputs["candidates"]
    # Pilot prefix is in frozen source-ID order; it is an API/schema check, not prompt tuning.
    ordered = [c for c in cases if c["example_kind"] == "synonym"] + [c for c in cases if c["example_kind"] != "synonym"]
    if limit is not None:
        ordered = ordered[:limit]
    jobs = [(c, name) for c in ordered for name in conditions]
    missing = [(c, name) for c, name in jobs if read_result(c, candidates, name) is None]
    print(f"Jev: {len(jobs) - len(missing)}/{len(jobs)} cached; {len(missing)} requests pending", flush=True)
    if not missing:
        return
    # Fail quickly on authentication/schema errors before submitting the full pool.
    first, name = missing.pop(0)
    run_one(first, candidates, name)
    completed, started = 1, time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        # Keep at most one job per worker outstanding, so an error does not drain a long queue.
        pending, iterator = {}, iter(missing)
        def submit_next():
            item = next(iterator, None)
            if item is not None:
                case, name = item
                pending[executor.submit(run_one, case, candidates, name)] = item
        for _ in range(workers):
            submit_next()
        while pending:
            future = next(as_completed(pending))
            pending.pop(future)
            future.result()
            completed += 1
            if completed % 100 == 0 or not pending:
                print(f"Jev: {completed}/{len(missing) + 1} new requests, {time.perf_counter() - started:.1f}s", flush=True)
            submit_next()


def operations(records):
    usages = [r["response"].get("usage") or {} for r in records]
    return {"requests": len(records),
            "reported_cost_usd": sum(u["cost"] for u in usages) if usages and all("cost" in u for u in usages) else None,
            "input_tokens": sum(u["input_tokens"] for u in usages) if usages and all("input_tokens" in u for u in usages) else None,
            "output_tokens": sum(u["output_tokens"] for u in usages) if usages and all("output_tokens" in u for u in usages) else None,
            "latency_seconds_p50_p95": np.quantile([r["seconds"] for r in records], [.5, .95]).tolist() if records else None,
            "attempts": sum(len(r["attempts"]) for r in records),
            "models": sorted({r["response"].get("model", "") for r in records}),
            "providers": sorted({r["response"].get("provider", "") for r in records})}


def analyze(inputs):
    candidates, cases = inputs["candidates"], inputs["cases"]
    metrics, predictions, strata, pairwise = [], [], [], []
    cache_manifest, all_records = [], []
    for kind in ("synonym", "canonical_name"):
        subset = [c for c in cases if c["example_kind"] == kind]
        present = [c for c in subset if c["canonical_id"] in c["top_ids"]]
        stats = GroupStatistics([c["canonical_id"] for c in subset])
        pstats = GroupStatistics([c["canonical_id"] for c in present])
        completed = {}
        for name in (*BASELINES, *CONDITIONS):
            decisions, records, available = [], [], []
            for case in subset:
                if name in BASELINES:
                    decision = baseline_decision(case, candidates, name)
                else:
                    cached = read_result(case, candidates, name)
                    if cached is None:
                        continue
                    decision, record = cached
                    records.append(record)
                    path = checkpoint_path(case, name)
                    cache_manifest.append({"path": str(path), "sha256": sha256(path)})
                available.append(case)
                decisions.append(decision)
                predictions.append({"condition": name, "example_kind": kind,
                    **{key: case[key] for key in ("source_id", "incoming_tag", "canonical_id", "canonical_name", "expected_rank")},
                    "selected_id": decision["selected_id"],
                    "selected_name": candidates[str(decision["selected_id"])]["name"] if decision["selected_id"] is not None else NONE_LABEL,
                    "forced_id": decision["forced_id"], "selected_probability": decision["selected_probability"],
                    "none_probability": decision["none_probability"],
                    "choice_disagrees_with_argmax": decision["choice_disagrees_with_argmax"], **outcome(case, decision)})
            full = len(available) == len(subset)
            metric = {"condition": name, "example_kind": kind, "status": "complete" if full else "pending",
                      "expected_queries": len(subset), "completed_queries": len(available),
                      "choice_argmax_disagreements": sum(d["choice_disagrees_with_argmax"] for d in decisions),
                      "metrics": summarize(subset, decisions, stats, pstats) if full else None,
                      "operations": operations(records) if name in CONDITIONS else None}
            metrics.append(metric)
            all_records.extend(records)
            if not full:
                continue
            completed[name] = decisions
            if kind == "synonym":
                for label, mask in (
                    ("target_with_aliases", [c["target_has_aliases"] for c in subset]),
                    ("target_without_aliases", [not c["target_has_aliases"] for c in subset]),
                    ("query_with_cotags", [bool(c["context"]) for c in subset]),
                    ("query_without_cotags", [not c["context"] for c in subset]),
                ):
                    selected = [(c, d) for c, d, keep in zip(subset, decisions, mask) if keep]
                    if selected:
                        successes = sum(d["selected_id"] == c["canonical_id"] for c, d in selected)
                        hits = sum(c["canonical_id"] in c["top_ids"] for c, _ in selected)
                        strata.append({"condition": name, "stratum": label, "queries": len(selected),
                                       "retrieved": hits, "successes": successes, "overall_accuracy": successes / len(selected),
                                       "selection_accuracy_if_retrieved": successes / hits if hits else None})
        for a, b in (("ao3_names", "strict_names"), ("ao3_aliases", "ao3_names"),
                     ("ao3_context", "ao3_aliases"), ("ao3_aliases_shuffled", "ao3_aliases"),
                     ("ao3_pairwise_aliases", "ao3_aliases")):
            if a in completed and b in completed:
                ca = np.array([d["selected_id"] == c["canonical_id"] for c, d in zip(subset, completed[a])], dtype=float)
                cb = np.array([d["selected_id"] == c["canonical_id"] for c, d in zip(subset, completed[b])], dtype=float)
                pairwise.append({"a": a, "b": b, "example_kind": kind, "difference": stats.calculate(ca - cb),
                                 "changed_selections": sum(x["selected_id"] != y["selected_id"] for x, y in zip(completed[a], completed[b]))})
    write_json(ROOT / "response-manifest.json", cache_manifest)
    write_csv(OUTPUT / "predictions.csv", list(predictions[0]), predictions)
    write_csv(OUTPUT / "strata.csv", list(strata[0]), strata)
    flat = []
    for row in metrics:
        m = row["metrics"]
        flat.append({"condition": row["condition"], "example_kind": row["example_kind"], "status": row["status"],
                     "completed_queries": row["completed_queries"], "expected_queries": row["expected_queries"],
                     **{key: m[key] if m else None for key in ("successes", "retrieved", "retrieval_misses", "rescue", "regression", "abstentions", "selection_error", "unnecessary_abstention", "correct_shortlist_rejection", "forced_candidate_successes", "forced_rescues", "forced_regressions", "forced_selection_accuracy_if_retrieved")},
                     "forced_overall_accuracy": m["forced_candidate_accuracy"]["micro"] if m else None,
                     "overall_accuracy": m["overall_accuracy"]["micro"] if m else None,
                     "selection_accuracy_if_retrieved": m["selection_accuracy_if_retrieved"]["micro"] if m and m["selection_accuracy_if_retrieved"] else None,
                     "macro_accuracy": m["overall_accuracy"]["macro"] if m else None})
    write_csv(OUTPUT / "metrics.csv", list(flat[0]), flat)
    complete = all(r["status"] == "complete" for r in metrics)
    summary = {"status": "complete_exploratory_development" if complete else "prepared_incomplete_jev",
               "protocol_sha256": sha256(OUTPUT / "protocol.json"), "metrics": metrics,
               "paired_comparisons": pairwise, "operations": operations(all_records),
               "response_manifest": {"path": str(ROOT / "response-manifest.json"), "sha256": sha256(ROOT / "response-manifest.json")},
               "calibration_and_test_evaluated": False, "natural_no_match_evaluated": False,
               "source_hashes_at_analysis": {str(p): sha256(p) for p in (Path(__file__), Path(__file__).with_name("ao3_jev.py"))}}
    write_json(OUTPUT / "summary.json", summary)
    print(json.dumps({"status": summary["status"], "operations": summary["operations"],
                      "synonym_metrics": [r for r in flat if r["example_kind"] == "synonym"]}, indent=2), flush=True)


NONE_LABEL = "none_of_these"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "analyze"))
    parser.add_argument("--conditions", nargs="+", choices=list(CONDITIONS), default=list(CONDITIONS))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, help="Run only this prefix for API checks; do not treat it as the full evaluation")
    args = parser.parse_args()
    if args.workers < 1 or (args.limit is not None and args.limit < 1):
        parser.error("workers and limit must be positive")
    inputs = prepare()
    if args.action == "run":
        run(inputs, args.conditions, args.workers, args.limit)
    analyze(inputs)


if __name__ == "__main__":
    main()
