#!/usr/bin/env python3
"""Explicit development follow-up: retain separate vectors for permitted aliases."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import numpy as np

from ao3_benchmark import fingerprint, load_benchmark, sha256, write_json
from ao3_representations import OUTPUT, ROOT, freeze, selected_aliases
from ao3_scoring import GroupStatistics, KS, rank_row
from cache_ao3_embeddings import parse_vectors, request_batch, validate_vectors
from run_ao3_representations import normalize, read_predictions, save_predictions
from analyze_ao3_representations import validate_rows


DIRECTORY = ROOT / "alias-pooling"
PROTOCOL = {
    "version": "ao3-stage2-alias-pooling-followup-v1",
    "motivation": "Declared after observing Stage 2 raw/template/concatenated-alias Recall@10 of 80.95/84.55/78.35; separate exploratory follow-up, not part of the original 19 conditions",
    "model": "openai/text-embedding-3-small", "dimensions": 1536,
    "aliases": "Exactly the same globally excluded, hash-selected maximum-five examples as Stage 2; no evaluated or held-out aliases",
    "conditions": ["raw_alias_max", "template_alias_max"],
    "score": "Maximum exhaustive cosine across canonical name and its selected alias vectors; one score/shortlist position per canonical ID",
    "templates": {"raw": "{name}", "template": "Tag: {name}\nType: Freeform"},
    "queries": "Same 2000 development synonyms and 250 separate identities; no work context",
    "statistics": "Paired target-group bootstrap against the matching name-only view and selected-alias lexical reference; 2000 resamples, seed 20261003",
    "calibration_and_test_evaluated": False,
}


def pool_scores(canonical, aliases, target_positions):
    result = canonical.copy()
    np.maximum.at(result, target_positions, aliases)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    DIRECTORY.mkdir(exist_ok=True)
    benchmark = load_benchmark()
    selected = sorted((r for values in selected_aliases(benchmark).values() for r in values), key=lambda r: r["source_id"])
    recipe = {"protocol": PROTOCOL, "benchmark": benchmark.provenance,
              "alias_ids_sha256": fingerprint([r["source_id"] for r in selected])}
    freeze(OUTPUT / "alias-pooling-protocol.json", recipe)
    texts = sorted({template.format(name=r["name"]) for template in PROTOCOL["templates"].values() for r in selected})
    freeze(DIRECTORY / "inputs.json", {"recipe": recipe, "texts": texts})
    positions = {text: i for i, text in enumerate(texts)}

    def one(start):
        batch_texts = texts[start:start + 512]
        path = DIRECTORY / f"batch-{start // 512:04d}.npy"
        meta_path = path.with_suffix(".json")
        if path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text())
            if sha256(path) != meta["vectors_sha256"] or fingerprint(batch_texts) != meta["texts_sha256"]:
                raise ValueError("Alias embedding cache changed")
            vectors = np.load(path, allow_pickle=False)
            validate_vectors(vectors, len(batch_texts))
        else:
            if args.offline:
                raise ValueError("Missing alias embedding batch in offline mode")
            response, seconds, attempts = request_batch(batch_texts)
            if response.get("model") not in ("text-embedding-3-small", "openai/text-embedding-3-small"):
                raise ValueError("Unexpected embedding model")
            vectors = parse_vectors(response, len(batch_texts))
            temporary = path.with_suffix(".npy.tmp")
            with temporary.open("wb") as f:
                np.save(f, vectors, allow_pickle=False)
            temporary.replace(path)
            meta = {"texts_sha256": fingerprint(batch_texts), "vectors_sha256": sha256(path),
                    "usage": response.get("usage"), "provider": response.get("provider"), "model": response.get("model"),
                    "seconds": seconds, "attempts": attempts, "fetched_at_utc": datetime.now(timezone.utc).isoformat()}
            write_json(meta_path, meta)
        return start, meta

    starts = list(range(0, len(texts), 512))
    batches = {}
    first, meta = one(starts[0])
    batches[first] = meta
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(one, start) for start in starts[1:]]
        for future in as_completed(futures):
            start, meta = future.result()
            batches[start] = meta
            if len(batches) % 20 == 0 or len(batches) == len(starts):
                print(f"Alias pooling embeddings: {len(batches)}/{len(starts)} batches", flush=True)
    all_vectors = np.empty((len(texts), 1536), dtype=np.float32)
    for start in starts:
        batch = np.load(DIRECTORY / f"batch-{start // 512:04d}.npy", allow_pickle=False)
        all_vectors[start:start + len(batch)] = batch
    ordered = [batches[start] for start in starts]
    embedding_meta = {"new_strings": len(texts), "batches": len(starts), "batch_metadata_sha256": fingerprint(ordered),
                      "prompt_tokens": sum((m.get("usage") or {}).get("prompt_tokens", 0) for m in ordered),
                      "reported_cost_usd": sum(m["usage"]["cost"] for m in ordered) if all("cost" in (m.get("usage") or {}) for m in ordered) else None,
                      "providers": sorted({str(m["provider"]) for m in ordered}), "attempts": sum(m["attempts"] for m in ordered)}
    matrix = np.load(ROOT / "embeddings/vectors.npy", mmap_mode="r", allow_pickle=False)
    position_arrays = np.load(ROOT / "positions.npz", allow_pickle=False)
    ids = np.asarray([r["canonical_id"] for r in benchmark.catalogue])
    lookup = {int(tag): i for i, tag in enumerate(ids)}
    targets = np.asarray([lookup[r["canonical_id"]] for r in selected])
    expected = [lookup[r["canonical_id"]] for r in benchmark.examples]
    masks = {kind: np.asarray([r["example_kind"] == kind for r in benchmark.examples]) for kind in ("synonym", "canonical_name")}
    groups = np.asarray([r["canonical_id"] for r in benchmark.examples])
    statistics = {kind: GroupStatistics(groups[mask]) for kind, mask in masks.items()}
    metrics, paired, strata, checks = [], [], [], 0
    supported_targets = {r["canonical_id"] for r in selected}
    supported = np.asarray([r["canonical_id"] in supported_targets for r in benchmark.examples])
    for view, template in PROTOCOL["templates"].items():
        condition = view + "_alias_max"
        score_recipe = {**recipe, "view": view, "embeddings": embedding_meta,
                        "base_vectors_sha256": json.loads((ROOT / "embeddings/manifest.json").read_text())["vectors_sha256"]}
        result = read_predictions("semantic", condition, score_recipe)
        if result is None:
            canonical = normalize(matrix[position_arrays["candidate_" + view]])
            queries = normalize(matrix[position_arrays["query_" + view]])
            alternate = normalize(all_vectors[[positions[template.format(name=r["name"])] for r in selected]])
            rows = []
            started = time.perf_counter()
            for start in range(0, len(queries), 32):
                base_scores = queries[start:start + 32] @ canonical.T
                alias_scores = queries[start:start + 32] @ alternate.T
                for offset, (base, alias) in enumerate(zip(base_scores, alias_scores)):
                    rows.append(rank_row(pool_scores(base, alias, targets), ids, expected[start + offset]))
            seconds = time.perf_counter() - started
            save_predictions("semantic", condition, score_recipe, rows, seconds)
            del canonical, queries, alternate
        else:
            rows, metadata = result
            seconds = metadata["seconds"]
        checks += validate_rows(rows, benchmark.examples, set(ids.tolist()))
        ranks = np.asarray([r["expected_rank"] for r in rows])
        for kind, mask in masks.items():
            metrics.append({"condition": condition, "example_kind": kind, "examples": int(mask.sum()),
                            **{f"recall@{k}": statistics[kind].calculate(ranks[mask] <= k) for k in KS}})
        for method, reference in (("semantic", view), ("semantic", "aliases"), ("lexical", "selected_aliases")):
            baseline = np.asarray([r["expected_rank"] for r in read_predictions(method, reference)[0]])
            for k in KS:
                mask = masks["synonym"]
                paired.append({"a": condition, "b": method + "/" + reference, "k": k,
                               "difference": statistics["synonym"].calculate((ranks[mask] <= k).astype(float) - (baseline[mask] <= k).astype(float))})
        baseline = np.asarray([r["expected_rank"] for r in read_predictions("semantic", view)[0]])
        for label, support_mask in (("target_has_selected_aliases", supported), ("target_has_no_selected_aliases", ~supported)):
            mask = masks["synonym"] & support_mask
            strata.append({"condition": condition, "stratum": label, "examples": int(mask.sum()),
                           **{f"recall@{k}": float((ranks[mask] <= k).mean()) for k in KS},
                           "matching_name_only_recall@10": float((baseline[mask] <= 10).mean())})
        print(f"{condition}: R1={metrics[-2]['recall@1']['micro']:.4f}, R10={metrics[-2]['recall@10']['micro']:.4f} ({seconds:.1f}s)", flush=True)
    write_json(OUTPUT / "alias-pooling.json", {"protocol": recipe, "embeddings": embedding_meta,
               "metrics": metrics, "paired_comparisons": paired, "alias_coverage_strata": strata,
               "independent_membership_checks": checks,
               "selected_aliases": len(selected), "source_sha256": sha256(Path(__file__)),
               "status": "complete_exploratory_development_followup"})


if __name__ == "__main__":
    main()
