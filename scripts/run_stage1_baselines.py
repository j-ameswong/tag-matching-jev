#!/usr/bin/env python3
"""Evaluate Stage 1 on development examples, preserving calibration and test.

Uses real PostgreSQL pg_trgm in a disposable local cluster, RapidFuzz, and exact
NumPy cosine search. Raw scoring and embedding caches make reruns inexpensive.
"""

import argparse
from collections import defaultdict
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import getpass
import gzip
import io
import json
from pathlib import Path
import platform
import re
import subprocess
import tempfile
import time

import numpy as np
import psycopg
import rapidfuzz
from rapidfuzz import fuzz, process
from rapidfuzz.distance import Levenshtein

from cache_stage1_embeddings import cache_embeddings
from stage1_data import digest, fingerprint, load_benchmark, write_json


KS = (1, 3, 5, 10)
BOOTSTRAPS = 2000
SEED = 20261003
NORMALIZATIONS = ("raw", "casefold", "whitespace", "safe_separators")
POLICIES = ("canonical_names", "development_aliases_leave_one_out")


def normalize(text, mode):
    if mode == "raw":
        return text
    text = text.casefold()
    if mode == "casefold":
        return text
    text = " ".join(text.split())
    if mode == "whitespace":
        return text
    if mode != "safe_separators":
        raise ValueError(f"Unknown normalization: {mode}")
    # Only internal word separators are unified. +, #, ., and leading punctuation survive.
    return re.sub(r"(?<=[^\W_])[_\s\u2010-\u2014]+(?=[^\W_])", "-", text)


def exact_predictions(queries, names, aliases, mode, allow_aliases):
    lookup = defaultdict(set)
    for name in names:
        lookup[normalize(name, mode)].add((name, name, False))
    if allow_aliases:
        for alias, target in aliases:
            lookup[normalize(alias, mode)].add((alias, target, True))
    predictions, statuses = [], []
    for query in queries:
        # Mask the evaluated alias before normalization, including every retrieval path.
        targets = {target for term, target, is_alias in lookup[normalize(query, mode)]
                   if not (is_alias and term == query)}
        predictions.append(next(iter(targets)) if len(targets) == 1 else None)
        statuses.append("unique" if len(targets) == 1 else "ambiguous" if targets else "no_match")
    return predictions, statuses


def aggregate_alias_scores(scores, queries, names, aliases):
    """Each canonical target gets its best permitted representation score."""
    output = scores[:, :len(names)].copy()
    positions = {name: i for i, name in enumerate(names)}
    query_rows = defaultdict(list)
    for i, query in enumerate(queries):
        query_rows[query].append(i)
    for j, (alias, target) in enumerate(aliases, len(names)):
        column = scores[:, j].copy()
        column[query_rows[alias]] = -np.inf
        target_id = positions[target]
        output[:, target_id] = np.maximum(output[:, target_id], column)
    return output


def rank_scores(scores, names, expected):
    # The catalogue is alphabetically sorted; stable sorting breaks score ties by name.
    order = np.argsort(-scores, axis=1, kind="stable")
    expected_ids = np.asarray([names.index(name) for name in expected])
    ranks = np.argmax(order == expected_ids[:, None], axis=1) + 1
    return ranks, order[:, :10]


def group_statistics(values, groups):
    """Micro, macro, and a canonical-group percentile bootstrap interval."""
    _, inverse = np.unique(groups, return_inverse=True)
    counts = np.bincount(inverse)
    successes = np.bincount(inverse, weights=np.asarray(values, dtype=float))
    rng = np.random.default_rng(SEED)
    samples = rng.integers(0, len(counts), size=(BOOTSTRAPS, len(counts)))
    estimates = successes[samples].sum(axis=1) / counts[samples].sum(axis=1)
    return {
        "micro": float(np.mean(values)), "macro": float(np.mean(successes / counts)),
        "micro_ci95": [float(x) for x in np.quantile(estimates, [0.025, 0.975])],
    }


@contextmanager
def temporary_postgres():
    with tempfile.TemporaryDirectory(prefix="tag-baselines-pg-") as temporary:
        cluster, log = Path(temporary) / "cluster", Path(temporary) / "server.log"
        subprocess.run(["initdb", "-D", str(cluster), "--encoding=UTF8", "--locale=C",
                        "--auth-local=trust", "--auth-host=reject", "--no-instructions"],
                       check=True, capture_output=True)
        options = f"-F -k {temporary} -h '' -p 55432 -c shared_buffers=32MB -c jit=off"
        started = False
        try:
            launch = subprocess.run(["pg_ctl", "-D", str(cluster), "-l", str(log),
                                     "-o", options, "-w", "start"], capture_output=True)
            if launch.returncode:
                raise RuntimeError("Local PostgreSQL startup failed: " + log.read_text())
            started = True
            with psycopg.connect(host=temporary, port=55432, dbname="postgres",
                                 user=getpass.getuser(), autocommit=True) as connection:
                yield connection
        finally:
            if started:
                subprocess.run(["pg_ctl", "-D", str(cluster), "-m", "fast", "-w", "stop"],
                               check=True, capture_output=True)


def postgres_scores(queries, terms, cache, recipe):
    metadata_path, scores_path = cache / "postgres.json", cache / "postgres.npz"
    version = subprocess.check_output(["postgres", "--version"], text=True).strip()
    config = {"recipe": recipe, "server_binary": version, "argument_order": "query, candidate",
              "scorers": ["similarity", "word_similarity", "strict_word_similarity"]}
    if metadata_path.exists() and scores_path.exists():
        metadata = json.loads(metadata_path.read_text())
        if metadata["configuration"] != config or metadata["scores_sha256"] != digest(scores_path):
            raise ValueError("PostgreSQL score cache mismatch; use a new --cache directory")
        with np.load(scores_path, allow_pickle=False) as arrays:
            return {name: arrays[name] for name in config["scorers"]}, metadata
    arrays = {name: np.empty((len(queries), len(terms)), dtype=np.float32) for name in config["scorers"]}
    with temporary_postgres() as connection:
        connection.execute("CREATE EXTENSION pg_trgm")
        extension_version = connection.execute("SELECT extversion FROM pg_extension WHERE extname='pg_trgm'").fetchone()[0]
        connection.execute("CREATE TABLE term (id integer PRIMARY KEY, name text NOT NULL)")
        with connection.cursor().copy("COPY term (id, name) FROM STDIN") as copy:
            for i, term in enumerate(terms):
                copy.write_row((i, term))
        started = time.perf_counter()
        for i, query in enumerate(queries):
            rows = connection.execute(
                "SELECT similarity(%s, name), word_similarity(%s, name), "
                "strict_word_similarity(%s, name) FROM term ORDER BY id",
                (query, query, query)).fetchall()
            matrix = np.asarray(rows, dtype=np.float32)
            for j, name in enumerate(config["scorers"]):
                arrays[name][i] = matrix[:, j]
            if (i + 1) % 100 == 0 or i + 1 == len(queries):
                print(f"pg_trgm: {i + 1}/{len(queries)} queries scored", flush=True)
        seconds = time.perf_counter() - started
        edge_probe = connection.execute("SELECT similarity('c', 'c++'), similarity('c', 'c#')").fetchone()
    np.savez_compressed(scores_path, **arrays)
    metadata = {"configuration": config, "extension_version": extension_version,
                "scores_sha256": digest(scores_path), "all_three_scorers_seconds": seconds,
                "punctuation_probe": {"similarity(c,c++)": edge_probe[0], "similarity(c,c#)": edge_probe[1]},
                "search": "exhaustive scores, no threshold or approximate index"}
    write_json(metadata_path, metadata)
    return arrays, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("data/stackoverflow"))
    parser.add_argument("--cache", type=Path, default=Path("data/baselines/stage1"))
    parser.add_argument("--embedding-cache", type=Path, default=Path("data/baselines/embeddings"))
    parser.add_argument("--output", type=Path, default=Path("results/stage1"))
    parser.add_argument("--lexical-only", action="store_true")
    parser.add_argument("--offline", action="store_true", help="Forbid embedding API calls")
    args = parser.parse_args()
    names, examples, aliases, provenance = load_benchmark(args.dataset)
    queries = [r["incoming_tag"] for r in examples]
    expected = [r["canonical_tag"] for r in examples]
    terms = names + [alias for alias, _ in aliases]
    recipe = {"provenance": provenance, "queries": queries, "terms": terms, "aliases": aliases}
    # JSON round trip canonicalizes tuples so cache comparisons are repeatable.
    recipe = json.loads(json.dumps(recipe))
    cache = args.cache / fingerprint(recipe)[:20]
    cache.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    metrics, predictions, rank_arrays, exact_details = [], [], {}, []

    def record(method, policy, ranks, top_ids=None, exact=None, statuses=None):
        rank_arrays[(method, policy)] = ranks
        for kind in ("synonym", "canonical_name"):
            mask = np.asarray([r["example_kind"] == kind for r in examples])
            groups = np.asarray(expected)[mask]
            entry = {"method": method, "candidate_policy": policy, "example_kind": kind,
                     "examples": int(mask.sum()), "canonical_groups": len(set(groups))}
            for k in KS:
                hits = (ranks[mask] > 0) & (ranks[mask] <= k)
                entry[f"hits@{k}"] = int(hits.sum())
                entry[f"recall@{k}"] = group_statistics(hits, groups)
            metrics.append(entry)
            if statuses is not None:
                status_array = np.asarray(statuses)[mask]
                unique = status_array == "unique"
                correct = ranks[mask] == 1
                exact_details.append({
                    "method": method, "candidate_policy": policy, "example_kind": kind,
                    "examples": int(mask.sum()), "correct": int(correct.sum()),
                    "wrong": int((unique & ~correct).sum()),
                    "ambiguous": int((status_array == "ambiguous").sum()),
                    "no_match": int((status_array == "no_match").sum()),
                })
        for i, example in enumerate(examples):
            candidates = ([names[j] for j in top_ids[i]] if top_ids is not None
                          else [exact[i]] if exact[i] is not None else [])
            predictions.append({"example_id": example["example_id"], "incoming_tag": queries[i],
                                "canonical_tag": expected[i], "example_kind": example["example_kind"],
                                "split": "development", "method": method, "candidate_policy": policy,
                                "expected_rank": int(ranks[i]), "top_candidates": candidates})
        row = metrics[-2]
        print(f"{method} / {policy}: synonym hits "
              + ", ".join(f"R@{k}={row[f'hits@{k}']}/{row['examples']}" for k in KS), flush=True)

    for mode in NORMALIZATIONS:
        for policy in POLICIES:
            predicted, statuses = exact_predictions(queries, names, aliases, mode, policy != POLICIES[0])
            ranks = np.asarray([int(p == target) for p, target in zip(predicted, expected)])
            record("exact_" + mode, policy, ranks, exact=predicted, statuses=statuses)

    arrays, pg_metadata = postgres_scores(queries, terms, cache, recipe)
    lexical_times = {}
    for method, scorer in (("levenshtein_normalized", Levenshtein.normalized_similarity), ("rapidfuzz_wratio", fuzz.WRatio)):
        started = time.perf_counter()
        arrays[method] = process.cdist(queries, terms, scorer=scorer, processor=None,
                                       dtype=np.float64, workers=1)
        lexical_times[method] = time.perf_counter() - started
    for method, scores in arrays.items():
        if not np.isfinite(scores).all():
            raise ValueError(f"Non-finite scores: {method}")
        for policy in POLICIES:
            canonical_scores = (scores[:, :len(names)] if policy == POLICIES[0]
                                else aggregate_alias_scores(scores, queries, names, aliases))
            ranks, top_ids = rank_scores(canonical_scores, names, expected)
            record(method, policy, ranks, top_ids=top_ids)

    embedding_metadata, semantic_seconds = None, None
    if not args.lexical_only:
        texts, vectors, embedding_metadata = cache_embeddings(args.dataset, args.embedding_cache, args.offline)
        positions = {text: i for i, text in enumerate(texts)}
        canonical_vectors = vectors[[positions[name] for name in names]]
        query_vectors = vectors[[positions[query] for query in queries]]
        started = time.perf_counter()
        scores = query_vectors @ canonical_vectors.T
        ranks, top_ids = rank_scores(scores, names, expected)
        semantic_seconds = time.perf_counter() - started
        record("text_embedding_3_small_cosine", POLICIES[0], ranks, top_ids=top_ids)

    synonym_mask = np.asarray([r["example_kind"] == "synonym" for r in examples])
    comparisons = []
    semantic_key = ("text_embedding_3_small_cosine", POLICIES[0])
    if semantic_key in rank_arrays:
        semantic = rank_arrays[semantic_key][synonym_mask]
        for method in arrays:
            lexical = rank_arrays[(method, POLICIES[0])][synonym_mask]
            for k in KS:
                sh, lh = semantic <= k, lexical <= k
                delta = sh.astype(float) - lh.astype(float)
                comparisons.append({
                    "semantic_minus": method, "k": k,
                    "semantic_only_hits": int((sh & ~lh).sum()), "lexical_only_hits": int((lh & ~sh).sum()),
                    "both_hit": int((sh & lh).sum()), "neither_hit": int((~sh & ~lh).sum()),
                    "paired_difference": group_statistics(delta, np.asarray(expected)[synonym_mask]),
                })

    predictions_path = cache / ("predictions-lexical.jsonl.gz" if args.lexical_only else "predictions.jsonl.gz")
    with predictions_path.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8") as handle:
                for row in predictions:
                    handle.write(json.dumps(row, sort_keys=True) + "\n")
    summary = {
        "stage": 1, "status": "lexical_only" if args.lexical_only else "complete",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(), "dataset": provenance,
        "protocol": {"ks": KS, "bootstrap_replicates": BOOTSTRAPS, "bootstrap_seed": SEED,
                     "bootstrap_unit": "canonical target; percentile intervals for micro recall and paired differences",
                     "tie_break": "canonical name ascending", "alias_policies": POLICIES,
                     "normalizations": NORMALIZATIONS, "threshold_pruning": False,
                     "jev_used": False, "fitted_parameters": False},
        "environment": {"python": platform.python_version(), "numpy": np.__version__,
                        "rapidfuzz": rapidfuzz.__version__, "psycopg": psycopg.__version__,
                        "platform": platform.platform()},
        "source_sha256": {name: digest(Path(__file__).parent / name) for name in
                          ("run_stage1_baselines.py", "cache_stage1_embeddings.py", "stage1_data.py")},
        "postgres": pg_metadata, "embedding": embedding_metadata,
        "timings": {"rapidfuzz_scoring_seconds": lexical_times, "semantic_matrix_and_ranking_seconds": semantic_seconds,
                    "interpretation": "batch experiment timings, not comparable per-request serving latency"},
        "predictions": {"path": str(predictions_path), "sha256": digest(predictions_path), "rows": len(predictions)},
        "exact_matching": exact_details, "metrics": metrics, "paired_comparisons": comparisons,
    }
    # Large input lists belong to the hashed cache, not the reviewable summary.
    summary["postgres"] = dict(pg_metadata, configuration={k: v for k, v in pg_metadata["configuration"].items() if k != "recipe"})
    summary["postgres"]["recipe_sha256"] = fingerprint(recipe)
    write_json(args.output / "summary.json", summary)
    with (args.output / "metrics.csv").open("w", newline="") as handle:
        fields = ["method", "candidate_policy", "example_kind", "examples", "canonical_groups"]
        fields += [f"{stat}@{k}" for k in KS for stat in ("hits", "recall", "macro_recall", "ci95_low", "ci95_high")]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in metrics:
            flat = {field: row[field] for field in fields[:5]}
            for k in KS:
                metric = row[f"recall@{k}"]
                flat.update({f"hits@{k}": row[f"hits@{k}"], f"recall@{k}": metric["micro"],
                             f"macro_recall@{k}": metric["macro"], f"ci95_low@{k}": metric["micro_ci95"][0],
                             f"ci95_high@{k}": metric["micro_ci95"][1]})
            writer.writerow(flat)
    print(f"Wrote {args.output / 'summary.json'} and {args.output / 'metrics.csv'}", flush=True)


if __name__ == "__main__":
    main()
