#!/usr/bin/env python3
"""Run AO3 Stage 1 with exhaustive scoring and resumable per-query rankings.

Use --phase lexical for PostgreSQL/RapidFuzz, --phase semantic for cached vectors,
and --phase report to assemble completed checkpoints without database/API access.
The default runs all three. No calibration/test query is scored.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import gzip
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import threading
import time

import numpy as np
import psycopg
import rapidfuzz
from rapidfuzz import fuzz, process
from rapidfuzz.distance import Levenshtein

from ao3_benchmark import (DEFAULT_CACHE, DEFAULT_DATASET, DEFAULT_OUTPUT, fingerprint,
                           load_benchmark, sha256, write_json)
from ao3_scoring import (FUZZ_METHODS, KS, NORMALIZATIONS, PG_METHODS, POLICIES, PROTOCOL,
                         SEMANTIC_METHOD, GroupStatistics, RankingContext, exact_matching,
                         rank_row, read_checkpoint, save_checkpoint)
from cache_ao3_embeddings import collect, load_normalized
from run_stage1_baselines import temporary_postgres


SOURCES = ("ao3_benchmark.py", "ao3_scoring.py", "run_ao3_baselines.py", "cache_ao3_embeddings.py", "run_stage1_baselines.py")


def run_postgres(context, cache, recipe, workers):
    paths = [cache / f"pg-{i:06d}.json" for i in range(len(context.benchmark.examples))]
    missing = [i for i, path in enumerate(paths) if read_checkpoint(path, recipe) is None]
    meta_path = cache / "postgres.json"
    if not missing:
        if not meta_path.exists():
            raise ValueError("Completed pg_trgm rows have no acquisition metadata")
        return
    with temporary_postgres() as connection:
        connection.execute("CREATE EXTENSION pg_trgm")
        connection.execute("CREATE TABLE term (id integer PRIMARY KEY, name text NOT NULL)")
        with connection.cursor().copy("COPY term (id, name) FROM STDIN") as copy:
            for i, name in enumerate(context.terms):
                copy.write_row((i, name))
        connection.execute("ANALYZE term")
        metadata = {
            "server_version": connection.execute("SELECT version()").fetchone()[0],
            "extension_version": connection.execute("SELECT extversion FROM pg_extension WHERE extname='pg_trgm'").fetchone()[0],
            "locale": "C", "query_workers": workers, "representations": len(context.terms),
            "argument_order": "query, candidate", "score_storage": "native PostgreSQL real (float32)",
            "postgresql_indexes_for_similarity": False,
            "punctuation_probe": dict(zip(("slash_vs_ampersand", "pipe_vs_space", "parentheses_vs_space"),
                                          connection.execute("SELECT similarity('A/B', 'A&B'), similarity('A|B', 'A B'), similarity('A (B)', 'A B')").fetchone())),
        }
        # Write before scoring so a fully completed interrupted run can be resumed.
        write_json(meta_path, metadata)
        parameters = connection.info.get_parameters()
        completed = len(paths) - len(missing)
        lock = threading.Lock()
        started = time.perf_counter()

        def partition(indexes):
            nonlocal completed
            with psycopg.connect(**parameters, autocommit=True) as worker:
                for index in indexes:
                    query = context.benchmark.examples[index]
                    before = time.perf_counter()
                    with worker.cursor(binary=True) as cursor:
                        cursor.execute("SELECT similarity(%s, name), word_similarity(%s, name), "
                                       "strict_word_similarity(%s, name) FROM term ORDER BY id",
                                       (query["incoming_tag"],) * 3)
                        scores = np.asarray(cursor.fetchall(), dtype=np.float32)
                    if scores.shape != (len(context.terms), 3) or not np.isfinite(scores).all():
                        raise ValueError("Invalid PostgreSQL score response")
                    predictions = {method: context.policies(scores[:, j], index) for j, method in enumerate(PG_METHODS)}
                    save_checkpoint(paths[index], recipe,
                                    {"example_id": query["example_id"], "predictions": predictions,
                                     "seconds": time.perf_counter() - before})
                    with lock:
                        completed += 1
                        if completed % 25 == 0 or completed == len(paths):
                            print(f"pg_trgm: {completed}/{len(paths)} queries ({time.perf_counter() - started:.1f}s this session)", flush=True)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(partition, [missing[i::workers] for i in range(workers)]))
        metadata["last_session_wall_seconds"] = time.perf_counter() - started
        metadata["last_session_queries"] = len(missing)
        write_json(meta_path, metadata)


def run_rapidfuzz(context, cache, recipe, workers, batch_size):
    for method, scorer in zip(FUZZ_METHODS, (Levenshtein.normalized_similarity, fuzz.WRatio)):
        missing = [i for i in range(len(context.benchmark.examples))
                   if read_checkpoint(cache / f"{method}-{i:06d}.json", recipe) is None]
        started = time.perf_counter()
        for offset in range(0, len(missing), batch_size):
            indexes = missing[offset:offset + batch_size]
            queries = [context.benchmark.examples[i]["incoming_tag"] for i in indexes]
            before = time.perf_counter()
            scores = process.cdist(queries, context.terms, scorer=scorer, processor=None,
                                   dtype=np.float64, workers=workers)
            elapsed = time.perf_counter() - before
            for row, index in zip(scores, indexes):
                predictions = context.policies(row, index)
                save_checkpoint(cache / f"{method}-{index:06d}.json", recipe,
                                {"example_id": context.benchmark.examples[index]["example_id"],
                                 "predictions": {method: predictions}, "scoring_seconds_per_batch_query": elapsed / len(indexes)})
            del scores
            print(f"{method}: {len(context.benchmark.examples) - len(missing) + offset + len(indexes)}/{len(context.benchmark.examples)} queries ({time.perf_counter() - started:.1f}s)", flush=True)
        write_json(cache / (method + "-runtime.json"), {"workers": workers, "batch_size": batch_size})


def run_semantic(context, cache, recipe, embedding_cache, batch_size, offline):
    texts, directory, metadata = collect(context.benchmark, embedding_cache, offline=offline)
    semantic_recipe = {**recipe, "embeddings_sha256": metadata["batch_metadata_sha256"]}
    missing = [i for i in range(len(context.benchmark.examples))
               if read_checkpoint(cache / f"semantic-{i:06d}.json", semantic_recipe) is None]
    if missing:
        vectors = load_normalized(texts, directory)
        positions = {text: i for i, text in enumerate(texts)}
        canonical = vectors[[positions[row["name"]] for row in context.benchmark.catalogue]]
        queries = vectors[[positions[row["incoming_tag"]] for row in context.benchmark.examples]]
        del vectors
        started = time.perf_counter()
        for offset in range(0, len(missing), batch_size):
            indexes = missing[offset:offset + batch_size]
            before = time.perf_counter()
            scores = queries[indexes] @ canonical.T
            elapsed = time.perf_counter() - before
            for row, index in zip(scores, indexes):
                prediction = rank_row(row, context.ids, context.expected[index])
                save_checkpoint(cache / f"semantic-{index:06d}.json", semantic_recipe,
                                {"example_id": context.benchmark.examples[index]["example_id"],
                                 "predictions": {SEMANTIC_METHOD: {POLICIES[0]: prediction}},
                                 "scoring_seconds_per_batch_query": elapsed / len(indexes)})
            print(f"cosine: {len(context.benchmark.examples) - len(missing) + offset + len(indexes)}/{len(context.benchmark.examples)} queries ({time.perf_counter() - started:.1f}s)", flush=True)
    write_json(cache / "embedding.json", metadata)
    write_json(cache / "semantic-runtime.json", {"batch_size": batch_size,
                                                "openblas_num_threads": os.environ.get("OPENBLAS_NUM_THREADS", "library default")})


def report(context, cache, recipe, output):
    benchmark = context.benchmark
    examples = benchmark.examples
    predictions, collisions, timings = {}, [], {}
    for mode in NORMALIZATIONS:
        for policy in POLICIES:
            rows, collision = exact_matching(benchmark, mode, policy == POLICIES[1])
            predictions[("exact_" + mode, policy)] = rows
            collisions.append({"method": "exact_" + mode, "candidate_policy": policy, **collision})
    metadata = json.loads((cache / "embedding.json").read_text())
    semantic_recipe = {**recipe, "embeddings_sha256": metadata["batch_metadata_sha256"]}
    for prefix, row_recipe in [("pg", recipe), *[(m, recipe) for m in FUZZ_METHODS], ("semantic", semantic_recipe)]:
        timings[prefix] = 0.0
        for i, example in enumerate(examples):
            row = read_checkpoint(cache / f"{prefix}-{i:06d}.json", row_recipe)
            if row is None or row["example_id"] != example["example_id"]:
                raise ValueError(f"Missing or incorrect {prefix} checkpoint for query {i}")
            timings[prefix] += row.get("seconds", row.get("scoring_seconds_per_batch_query", 0))
            for method, policies in row["predictions"].items():
                for policy, result in policies.items():
                    predictions.setdefault((method, policy), []).append(result)
    expected_keys = {(f"exact_{m}", p) for m in NORMALIZATIONS for p in POLICIES}
    expected_keys |= {(m, p) for m in (*PG_METHODS, *FUZZ_METHODS) for p in POLICIES}
    expected_keys.add((SEMANTIC_METHOD, POLICIES[0]))
    if set(predictions) != expected_keys or any(len(rows) != len(examples) for rows in predictions.values()):
        raise ValueError("Incomplete baseline conditions")
    masks = {kind: np.asarray([r["example_kind"] == kind for r in examples]) for kind in ("synonym", "canonical_name")}
    group_ids = np.asarray([r["canonical_id"] for r in examples])
    statistics = {kind: GroupStatistics(group_ids[mask]) for kind, mask in masks.items()}
    metrics, exact_details, rank_arrays = [], [], {}
    candidate_ids = set(context.ids.tolist())
    for (method, policy), rows in predictions.items():
        ranks = np.asarray([row["expected_rank"] for row in rows])
        rank_arrays[(method, policy)] = ranks
        for query, row in zip(examples, rows):
            top = row["top_ids"]
            if len(top) != len(set(top)) or not set(top) <= candidate_ids:
                raise ValueError("Duplicate or out-of-catalogue shortlist IDs")
            if not method.startswith("exact_"):
                if not 1 <= row["expected_rank"] <= len(candidate_ids) or len(top) != min(10, len(candidate_ids)):
                    raise ValueError("Invalid full rank or shortlist length")
                if row["expected_rank"] <= len(top):
                    if top[row["expected_rank"] - 1] != query["canonical_id"]:
                        raise ValueError("Full rank and shortlist disagree")
                elif query["canonical_id"] in top:
                    raise ValueError("Missed target incorrectly appears in shortlist")
        for kind, mask in masks.items():
            entry = {"method": method, "candidate_policy": policy, "example_kind": kind,
                     "examples": int(mask.sum()), "canonical_groups": len(set(group_ids[mask]))}
            for k in KS:
                hits = (ranks[mask] > 0) & (ranks[mask] <= k)
                entry[f"hits@{k}"] = int(hits.sum())
                entry[f"recall@{k}"] = statistics[kind].calculate(hits)
            metrics.append(entry)
            if method.startswith("exact_"):
                statuses = np.asarray([row["status"] for row in rows])[mask]
                exact_details.append({"method": method, "candidate_policy": policy, "example_kind": kind,
                                      "correct": int((ranks[mask] == 1).sum()),
                                      "wrong": int(((statuses == "unique") & (ranks[mask] != 1)).sum()),
                                      "ambiguous": int((statuses == "ambiguous").sum()),
                                      "no_match": int((statuses == "no_match").sum())})
        print(f"{method} / {policy}: " + ", ".join(f"R@{k}={metrics[-2][f'hits@{k}']}/{metrics[-2]['examples']}" for k in KS), flush=True)
    paired = []
    synonym_mask = masks["synonym"]
    semantic = rank_arrays[(SEMANTIC_METHOD, POLICIES[0])][synonym_mask]
    for method in (*PG_METHODS, *FUZZ_METHODS):
        lexical = rank_arrays[(method, POLICIES[0])][synonym_mask]
        for k in KS:
            sh, lh = semantic <= k, lexical <= k
            paired.append({"semantic_minus": method, "k": k, "semantic_only_hits": int((sh & ~lh).sum()),
                           "lexical_only_hits": int((lh & ~sh).sum()), "both_hit": int((sh & lh).sum()),
                           "neither_hit": int((~sh & ~lh).sum()),
                           "paired_difference": statistics["synonym"].calculate(sh.astype(float) - lh.astype(float)),
                           "union_recall_upper_bound_at_up_to_2k": float((sh | lh).mean())})
    # Predeclared descriptive strata, not tuned thresholds or language inference.
    strata = []
    sampled = [r for r in examples if r["example_kind"] == "synonym"]
    strata_masks = {
        "uses_5_to_9": [r["cached_count"] < 10 for r in sampled],
        "uses_10_to_99": [10 <= r["cached_count"] < 100 for r in sampled],
        "uses_at_least_100": [r["cached_count"] >= 100 for r in sampled],
        "name_at_most_20_chars": [len(r["incoming_tag"]) <= 20 for r in sampled],
        "name_21_to_50_chars": [20 < len(r["incoming_tag"]) <= 50 for r in sampled],
        "name_over_50_chars": [len(r["incoming_tag"]) > 50 for r in sampled],
        "contains_non_ascii": [not r["incoming_tag"].isascii() for r in sampled],
        "ascii_only": [r["incoming_tag"].isascii() for r in sampled],
        "contains_slash_ampersand_pipe_or_parenthesis": [any(c in r["incoming_tag"] for c in "/&|()") for r in sampled],
    }
    for name, values in strata_masks.items():
        mask = np.asarray(values)
        for (method, policy), ranks in rank_arrays.items():
            if method.startswith("exact_") or policy != POLICIES[0] or not mask.any():
                continue
            selected = ranks[synonym_mask][mask]
            strata.append({"stratum": name, "method": method, "examples": len(selected),
                           **{f"recall@{k}": float((selected <= k).mean()) for k in KS}})
    prediction_path = cache / "predictions.jsonl.gz"
    with prediction_path.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8") as handle:
                for (method, policy), rows in sorted(predictions.items()):
                    for query, row in zip(examples, rows):
                        entry = {"example_id": query["example_id"], "source_id": query["source_id"],
                                 "expected_canonical_id": query["canonical_id"], "type": query["type"],
                                 "example_kind": query["example_kind"], "split": "development",
                                 "method": method, "candidate_policy": policy, **row}
                        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    summary = {
        "stage": 1, "status": "complete", "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": benchmark.provenance, "protocol": PROTOCOL, "source_sha256": recipe["source_sha256"],
        "environment": {"python": platform.python_version(), "numpy": np.__version__, "rapidfuzz": rapidfuzz.__version__,
                        "psycopg": psycopg.__version__, "platform": platform.platform()},
        "postgres": json.loads((cache / "postgres.json").read_text()), "embedding": metadata,
        "runtime_configuration": {name: json.loads((cache / (name + "-runtime.json")).read_text())
                                  for name in (*FUZZ_METHODS, "semantic")},
        "timings": {"accumulated_seconds": timings,
                    "interpretation": "pg sums concurrent query elapsed times including ranking; other methods sum batch scoring wall times; these are not comparable serving latencies"},
        "metrics": metrics, "exact_matching": exact_details, "normalization_collisions": collisions,
        "paired_comparisons": paired, "descriptive_strata": strata,
        "predictions": {"path": str(prediction_path), "sha256": sha256(prediction_path),
                        "rows": len(examples) * len(predictions), "conditions": len(predictions)},
        "limitations": ["Development pilot on hash-sampled, capped visible-name positives; no final test estimate",
                        "Same-type, positive-only evaluation; no rejection accuracy or automation precision established",
                        "Alias-expanded condition has more lexical information than name-only semantic condition",
                        "Identity controls are separate and do not estimate synonym performance",
                        "Pretrained model contamination cannot be ruled out by local group splits",
                        "Unversioned provider model alias; offline vectors freeze only this acquisition"],
    }
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "summary.json", summary)
    write_json(output / "protocol.json", PROTOCOL)
    fields = ["method", "candidate_policy", "example_kind", "examples", "canonical_groups"]
    fields += [f"{stat}@{k}" for k in KS for stat in ("hits", "recall", "macro_recall", "ci95_low", "ci95_high")]
    with (output / "metrics.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in metrics:
            flat = {key: row[key] for key in fields[:5]}
            for k in KS:
                stat = row[f"recall@{k}"]
                flat.update({f"hits@{k}": row[f"hits@{k}"], f"recall@{k}": stat["micro"],
                             f"macro_recall@{k}": stat["macro"], f"ci95_low@{k}": stat["micro_ci95"][0],
                             f"ci95_high@{k}": stat["micro_ci95"][1]})
            writer.writerow(flat)
    write_json(output / "validation.json", {"status": "passed", "benchmark_manifest_sha256": benchmark.provenance["manifest_sha256"],
                                            "summary_sha256": sha256(output / "summary.json"),
                                            "metrics_sha256": sha256(output / "metrics.csv"),
                                            "predictions_sha256": sha256(prediction_path),
                                            "checks": ["export checksums and frozen sampling rule", "source IDs, target IDs, names and types agree",
                                                       "canonical groups follow frozen split", "development-only query and alias pools",
                                                       "all baseline conditions complete", "checkpoint recipe and content hashes",
                                                       "shortlists contain unique catalogue IDs", "full ranks agree with top-10 lists"]})
    print(f"Wrote {output / 'summary.json'} and {output / 'metrics.csv'}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE / "stage1")
    parser.add_argument("--embedding-cache", type=Path, default=DEFAULT_CACHE / "embeddings")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--phase", choices=("all", "lexical", "semantic", "report"), default="all")
    parser.add_argument("--offline", action="store_true", help="Forbid embedding API calls")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    if args.workers < 1 or args.batch_size < 1:
        parser.error("--workers and --batch-size must be positive")
    context = RankingContext(load_benchmark(args.dataset))
    recipe = {"benchmark": context.benchmark.provenance, "protocol": PROTOCOL,
              "source_sha256": {name: sha256(Path(__file__).parent / name) for name in SOURCES},
              "numpy": np.__version__, "rapidfuzz": rapidfuzz.__version__, "psycopg": psycopg.__version__,
              "postgres_binary": subprocess.check_output(["postgres", "--version"], text=True).strip()}
    cache = args.cache / fingerprint(recipe)[:20]
    cache.mkdir(parents=True, exist_ok=True)
    write_json(cache / "recipe.json", recipe)
    args.output.mkdir(parents=True, exist_ok=True)
    write_json(args.output / "protocol.json", PROTOCOL)
    print(f"AO3 {len(context.benchmark.examples)} development queries, {len(context.ids):,} candidates; cache {cache}", flush=True)
    if args.phase in ("all", "lexical"):
        run_postgres(context, cache, recipe, args.workers)
        run_rapidfuzz(context, cache, recipe, args.workers, args.batch_size)
    if args.phase in ("all", "semantic"):
        run_semantic(context, cache, recipe, args.embedding_cache, args.batch_size, args.offline)
    if args.phase in ("all", "report"):
        report(context, cache, recipe, args.output)


if __name__ == "__main__":
    main()
