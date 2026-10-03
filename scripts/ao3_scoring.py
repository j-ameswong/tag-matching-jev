"""Bounded-memory exact ranking and collision-aware matching for AO3 IDs."""

from collections import defaultdict
import json
from pathlib import Path
import re

import numpy as np

from ao3_benchmark import fingerprint, write_json


KS = (1, 3, 5, 10)
SEED = 20261003
BOOTSTRAPS = 2000
POLICIES = ("canonical_names", "development_aliases_leave_one_out")
NORMALIZATIONS = ("raw", "casefold", "whitespace", "safe_punctuation")
PG_METHODS = ("similarity", "word_similarity", "strict_word_similarity")
FUZZ_METHODS = ("levenshtein_normalized", "rapidfuzz_wratio")
SEMANTIC_METHOD = "text_embedding_3_small_cosine"
PROTOCOL = {
    "version": "ao3-freeform-stage1-v1", "ks": list(KS), "tie_break": "numeric canonical ID ascending",
    "normalizations": list(NORMALIZATIONS), "alias_policies": list(POLICIES),
    "safe_punctuation": "After casefold and whitespace collapse, map internal U+2010/U+2011 to hyphen and U+2018/U+2019 to apostrophe, only between alphanumeric characters",
    "lexical_input": "raw strings; no RapidFuzz processor; PostgreSQL function arguments query, candidate",
    "alias_aggregation": "maximum permitted representation score per canonical ID; mask queried source ID before aggregation",
    "search": "exhaustive, all same-type candidates, no score threshold or approximate index",
    "bootstrap_unit": "canonical target; percentile intervals for micro recall and paired differences",
    "bootstrap_replicates": BOOTSTRAPS, "bootstrap_seed": SEED,
    "query_features": "raw tag name; observed type restricts candidate catalogue",
    "calibration_and_test_evaluated": False, "jev_used": False, "fitted_parameters": False,
}


def normalize(text, mode):
    if mode == "raw":
        return text
    text = text.casefold()
    if mode == "casefold":
        return text
    text = " ".join(text.split())
    if mode == "whitespace":
        return text
    if mode != "safe_punctuation":
        raise ValueError(f"Unknown normalization {mode}")
    text = re.sub(r"(?<=[^\W_])[\u2010\u2011](?=[^\W_])", "-", text)
    return re.sub(r"(?<=[^\W_])[\u2018\u2019](?=[^\W_])", "'", text)


def exact_matching(benchmark, mode, allow_aliases):
    lookup = defaultdict(list)
    for row in benchmark.catalogue:
        lookup[normalize(row["name"], mode)].append((row["canonical_id"], row["canonical_id"], False))
    if allow_aliases:
        for row in benchmark.aliases:
            lookup[normalize(row["name"], mode)].append((row["source_id"], row["canonical_id"], True))
    collisions = sum(len({target for _, target, _ in bucket}) > 1 for bucket in lookup.values())
    results = []
    for query in benchmark.examples:
        targets = {target for source, target, is_alias in lookup.get(normalize(query["incoming_tag"], mode), [])
                   if not (is_alias and source == query["source_id"])}
        predicted = next(iter(targets)) if len(targets) == 1 else None
        results.append({"expected_rank": 1 if predicted == query["canonical_id"] else 0,
                        "top_ids": [] if predicted is None else [predicted],
                        "status": "unique" if predicted is not None else "ambiguous" if targets else "no_match"})
    return results, {"normalized_keys": len(lookup), "cross_target_collision_keys_before_query_mask": collisions}


def rank_row(scores, canonical_ids, expected_position, k=10):
    """Exact full rank plus top k, with numeric-ID tie resolution at the cutoff.

    canonical_ids must be strictly increasing; callers validate this once. Unlike
    bare argpartition, ties spanning the cutoff cannot select arbitrary IDs.
    """
    if scores.shape != canonical_ids.shape or not np.isfinite(scores).all():
        raise ValueError("Invalid canonical score vector")
    k = min(k, len(scores))
    target_score = scores[expected_position]
    rank = 1 + int(np.count_nonzero(scores > target_score)) + int(np.count_nonzero(scores[:expected_position] == target_score))
    partial = np.argpartition(-scores, k - 1)[:k]
    cutoff = scores[partial].min()
    better = np.flatnonzero(scores > cutoff)
    tied = np.flatnonzero(scores == cutoff)[:k - len(better)]
    selected = np.concatenate((better, tied))
    selected = selected[np.lexsort((canonical_ids[selected], -scores[selected]))]
    return {"expected_rank": rank, "top_ids": canonical_ids[selected].tolist(),
            "top_scores": scores[selected].astype(float).tolist(), "expected_score": float(target_score)}


class RankingContext:
    def __init__(self, benchmark):
        self.benchmark = benchmark
        self.ids = np.asarray([r["canonical_id"] for r in benchmark.catalogue], dtype=np.int64)
        if not len(self.ids) or np.any(self.ids[1:] <= self.ids[:-1]):
            raise ValueError("Canonical catalogue must have distinct ascending numeric IDs")
        positions = {int(tag_id): i for i, tag_id in enumerate(self.ids)}
        self.expected = [positions[r["canonical_id"]] for r in benchmark.examples]
        self.alias_targets = np.asarray([positions[r["canonical_id"]] for r in benchmark.aliases], dtype=np.int64)
        self.alias_positions = {r["source_id"]: i + len(self.ids) for i, r in enumerate(benchmark.aliases)}
        self.terms = [r["name"] for r in benchmark.catalogue] + [r["name"] for r in benchmark.aliases]

    def policies(self, scores, query_index):
        if scores.shape != (len(self.terms),):
            raise ValueError("Wrong number of representation scores")
        query = self.benchmark.examples[query_index]
        canonical = scores[:len(self.ids)]
        result = {POLICIES[0]: rank_row(canonical, self.ids, self.expected[query_index])}
        expanded = canonical.copy()
        alias_scores = scores[len(self.ids):].copy()
        alias_position = self.alias_positions.get(query["source_id"])
        if alias_position is not None:
            alias_scores[alias_position - len(self.ids)] = -np.inf
        np.maximum.at(expanded, self.alias_targets, alias_scores)
        result[POLICIES[1]] = rank_row(expanded, self.ids, self.expected[query_index])
        return result


def save_checkpoint(path, recipe, result):
    write_json(path, {"recipe_sha256": fingerprint(recipe), "result_sha256": fingerprint(result), "result": result})


def read_checkpoint(path, recipe):
    if not Path(path).exists():
        return None
    wrapped = json.loads(Path(path).read_text())
    if wrapped["recipe_sha256"] != fingerprint(recipe) or wrapped["result_sha256"] != fingerprint(wrapped["result"]):
        raise ValueError(f"Score checkpoint integrity failure: {path}")
    return wrapped["result"]


class GroupStatistics:
    def __init__(self, groups):
        _, self.inverse = np.unique(groups, return_inverse=True)
        self.counts = np.bincount(self.inverse)
        self.samples = np.random.default_rng(SEED).integers(0, len(self.counts), size=(BOOTSTRAPS, len(self.counts)))
        self.denominators = self.counts[self.samples].sum(axis=1)

    def calculate(self, values):
        values = np.asarray(values, dtype=float)
        successes = np.bincount(self.inverse, weights=values)
        estimates = successes[self.samples].sum(axis=1) / self.denominators
        return {"micro": float(values.mean()), "macro": float((successes / self.counts).mean()),
                "micro_ci95": np.quantile(estimates, [0.025, 0.975]).tolist()}
