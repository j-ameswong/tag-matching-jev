#!/usr/bin/env python3
"""Frozen, work-disjoint AO3 representation inputs; no retrieval or model calls."""

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import heapq
import json
from pathlib import Path
import time

from ao3_benchmark import csv_rows, fingerprint, hash_order, load_benchmark, sha256, write_json


ROOT = Path("data/ao3-representations")
OUTPUT = Path("results/ao3/stage2")
VIEWS = ("raw", "template", "aliases", "fandom", "cotags", "length", "usage",
         "metadata", "cotags_length", "all")
CONTEXT_VIEWS = VIEWS[3:]
COTAG_LIMITS = {"Fandom": 3, "Character": 3, "Relationship": 2, "Freeform": 4}
META_TYPES = ("Rating", "ArchiveWarning", "Category")
PROTOCOL = {
    "version": "ao3-freeform-representations-v1",
    "lexical_method": "PostgreSQL pg_trgm similarity(query, candidate); raw names and separate alias representations",
    "embedding_model": "openai/text-embedding-3-small", "dimensions": 1536,
    "metric": "exhaustive cosine, float64 L2 normalization and scoring",
    "candidates": "all 181067 Freeform canonicals; no candidate pruning or ANN",
    "sample": "unchanged Stage 1: 2000 development aliases; 250 separate identity controls",
    "evaluated_alias_policy": "Globally exclude every sampled synonym ID from candidate examples and co-tags; also discard support works containing any of these IDs",
    "alias_examples": {"maximum": 5, "order_seed": "ao3-stage2-alias-v1", "max_name_characters": 160},
    "work_assignment": {"seed": "ao3-stage2-works-v1", "identity": "source file SHA256 and 1-based logical CSV record ordinal",
                        "rule": "first 64 SHA256 bits modulo 10: 0-5 support, 6-7 development query context, 8 calibration context, 9 test context",
                        "sampling_priority": "next 64 SHA256 bits; smallest first"},
    "candidate_work_membership": "Direct canonical IDs and permitted development Freeform aliases only; deduplicate target ID within each work",
    "query_work_membership": "Raw source tag ID only; never resolve source to target to collect its works",
    "co_tags": "Only visible canonical IDs from the frozen catalogue; exclude the represented target ID; all noncanonical co-tags omitted",
    "mask_ground_truth": "Expected canonical ID is used only to remove equivalent context from query works, never to select query works or add features",
    "profile_sample_works": 32, "max_co_tags_per_type_per_work": 24,
    "profile_minimum_works": 3, "co_tag_minimum_sample_support": 2,
    "co_tag_selection": "Count in bottom-hash sample, then smoothed association (count+1)/(support background count+10), then numeric ID; fixed per-type quotas",
    "co_tag_quotas": COTAG_LIMITS,
    "length_bands_words": {"short": [0, 5000], "medium": [5000, 20000], "long": [20000, 100000], "epic": [100000, None]},
    "length_features": "Percent of nonmissing lengths per band; completed and incomplete works both included; no missing-as-zero",
    "usage_feature": "Distinct eligible work share: <1e-6 very rare, <1e-4 uncommon, <1e-2 common, otherwise very common; not reader engagement",
    "metadata_features": "Top two work language codes; complete and restricted percentages; common canonical rating/warning/category",
    "text_views": list(VIEWS),
    "semantic_conditions": ["raw", "template", "aliases"] +
        [f"{v}_{side}" for v in CONTEXT_VIEWS for side in ("candidate", "both")] +
        ["cotags_one_work", "all_name_anchor"],
    "one_work_query": "Lowest-hash observed development context work, same masking and quotas; candidate co-tag profile",
    "name_anchor": "L2 normalize 0.75 * normalized raw-name vector + 0.25 * normalized all-features vector, on both sides",
    "lexical_conditions": ["raw", "global_aliases", "selected_aliases"],
    "statistics": "Recall@1/3/5/10; macro target recall; 2000 paired target-group bootstrap resamples, seed 20261003; descriptive development comparisons, no multiplicity correction",
    "holdouts": "Calibration and test queries are not scored; their aliases are not indexed, embedded or used to assign support works to candidates",
    "html_scope": "Single supplied fixture: extraction validation only; no representative summary or engagement corpus and no original work-ID join in dump",
    "generated_definitions": False, "jev_used": False,
}


def freeze(path, payload):
    if path.exists():
        if json.loads(path.read_text()) != payload:
            raise ValueError(f"Frozen input changed: {path}; use a new version/directory")
    else:
        write_json(path, payload)


def work_assignment(file_hash, ordinal):
    digest = hashlib.sha256(f"ao3-stage2-works-v1:{file_hash}:{ordinal}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % 10, int.from_bytes(digest[8:16], "big")


def length_band(words):
    if words is None:
        return None
    return 0 if words < 5000 else 1 if words < 20000 else 2 if words < 100000 else 3


def new_profile():
    return {"n": 0, "words_n": 0, "words_sum": 0, "bands": [0, 0, 0, 0],
            "complete": 0, "restricted": 0, "languages": Counter(), "sample": []}


def add_work(profile, priority, ordinal, context, language, complete, restricted, words):
    profile["n"] += 1
    profile["complete"] += complete
    profile["restricted"] += restricted
    profile["languages"][language] += 1
    if words is not None:
        profile["words_n"] += 1
        profile["words_sum"] += words
        profile["bands"][length_band(words)] += 1
    heap = profile["sample"]
    value = (-priority, -ordinal, context)
    if len(heap) < PROTOCOL["profile_sample_works"]:
        heapq.heappush(heap, value)
    elif value[:2] > heap[0][:2]:
        heapq.heapreplace(heap, value)


def visible_context(tag_ids, metadata):
    groups = defaultdict(list)
    for tag_id in tag_ids:
        item = metadata.get(tag_id)
        if item and item[1] in (*COTAG_LIMITS, *META_TYPES):
            groups[item[1]].append(tag_id)
    # Numeric ID cap bounds pathological crossovers without depending on labels.
    return tuple(sorted(t for values in groups.values()
                        for t in sorted(set(values))[:PROTOCOL["max_co_tags_per_type_per_work"]]))


def summarize_profile(profile, target_id, metadata, background):
    counts = Counter(t for _, _, context in profile["sample"] for t in context if t != target_id)
    ranked = sorted(counts, key=lambda t: (-counts[t], -(counts[t] + 1) / (background.get(t, 0) + 10), t))
    selected = {}
    for kind, limit in {**COTAG_LIMITS, **dict.fromkeys(META_TYPES, 1)}.items():
        selected[kind] = [{"id": t, "name": metadata[t][0], "sample_works": counts[t]}
                          for t in ranked if metadata[t][1] == kind
                          and counts[t] >= PROTOCOL["co_tag_minimum_sample_support"]][:limit]
    result = {k: v for k, v in profile.items() if k != "sample"}
    result["languages"] = dict(profile["languages"])
    result["sample_n"] = len(profile["sample"])
    result["sample_work_ordinals"] = sorted(-item[1] for item in profile["sample"])
    result["co_tags"] = selected
    if profile["sample"]:
        first = max(profile["sample"], key=lambda item: item[:2])
        result["one_work"] = {kind: [metadata[t][0] for t in first[2]
                                     if t != target_id and metadata[t][1] == kind][:limit]
                              for kind, limit in COTAG_LIMITS.items()}
    else:
        result["one_work"] = {}
    return result


def build_profiles(benchmark, dataset, work_path, root):
    destination = root / "profiles.json"
    manifest_path = root / "profiles-manifest.json"
    file_hash = sha256(work_path)
    expected_hash = benchmark.manifest["source_files"][work_path.name]["sha256"]
    if file_hash != expected_hash:
        raise ValueError("Works CSV differs from the audited source")
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if (manifest["profiles_sha256"] != sha256(destination)
                or manifest["protocol_sha256"] != fingerprint(PROTOCOL) or manifest["works_sha256"] != file_hash):
            raise ValueError("Profile checksum/protocol mismatch")
        return json.loads(destination.read_text()), manifest
    metadata = {int(r["canonical_id"]): (r["name"], r["type"]) for r in csv_rows(dataset / "catalogue.csv")}
    queries = {r["source_id"]: r for r in benchmark.examples}
    blocked = {r["source_id"] for r in benchmark.examples if r["example_kind"] == "synonym"}
    permitted = [r for r in benchmark.aliases if r["source_id"] not in blocked]
    membership = {r["canonical_id"]: r["canonical_id"] for r in benchmark.catalogue}
    membership.update({r["source_id"]: r["canonical_id"] for r in permitted})
    candidate = {r["canonical_id"]: new_profile() for r in benchmark.catalogue}
    incoming = {source: new_profile() for source in queries}
    counters, background = Counter(), Counter()
    started = time.perf_counter()
    with work_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        if header[:6] != ["creation date", "language", "restricted", "complete", "word_count", "tags"]:
            raise ValueError("Unexpected works header")
        for ordinal, row in enumerate(reader, 1):
            if len(row) != 6 or row[2] not in ("true", "false") or row[3] not in ("true", "false"):
                raise ValueError(f"Malformed work {ordinal}")
            counters["works_total"] += 1
            bucket, priority = work_assignment(file_hash, ordinal)
            if bucket >= 8:
                counters["reserved_calibration_test_works"] += 1
                continue
            tags = set(map(int, row[5].split("+"))) if row[5] else set()
            if bucket < 6:
                if tags & blocked:
                    counters["support_works_excluded_evaluated_alias"] += 1
                    continue
                counters["support_works"] += 1
                targets = {membership[t] for t in tags if t in membership}
                # Background counts use the identical eligible support partition.
                context = visible_context(tags, metadata)
                background.update(context)
                recipients = [(candidate[t], t) for t in targets]
            else:
                counters["development_context_works"] += 1
                sources = tags & queries.keys()
                if not sources:
                    continue
                counters["development_works_with_query"] += 1
                context = visible_context(tags, metadata)
                recipients = [(incoming[t], queries[t]["canonical_id"]) for t in sources]
            words = int(row[4]) if row[4].strip() else None
            if words is not None and words < 0:
                raise ValueError("Negative word count")
            for profile, target in recipients:
                # Remove the entire canonical equivalent before storing any context.
                masked = tuple(t for t in context if t != target)
                add_work(profile, priority, ordinal, masked, row[1], row[3] == "true", row[2] == "true", words)
            if ordinal % 500000 == 0:
                print(f"Work profiles: {ordinal:,} rows ({time.perf_counter() - started:.1f}s)", flush=True)
    profiles = {"candidate": {str(t): summarize_profile(p, t, metadata, background) for t, p in candidate.items()},
                "query": {str(t): summarize_profile(p, queries[t]["canonical_id"], metadata, background) for t, p in incoming.items()},
                "counts": dict(counters)}
    write_json(destination, profiles)
    sampled = [r for r in benchmark.examples if r["example_kind"] == "synonym"]
    supported = lambda r: profiles["query"][str(r["source_id"])]["n"]
    manifest = {"protocol_sha256": fingerprint(PROTOCOL), "works_sha256": file_hash,
                "profiles_sha256": sha256(destination), "counts": dict(counters),
                "candidate_profiles_with_works": sum(p["n"] > 0 for p in candidate.values()),
                "synonym_queries_with_works": sum(supported(r) > 0 for r in sampled),
                "synonym_queries_with_at_least_three_works": sum(supported(r) >= 3 for r in sampled),
                "permitted_aliases": len(permitted), "excluded_evaluation_aliases": len(blocked),
                "elapsed_seconds": time.perf_counter() - started}
    write_json(manifest_path, manifest)
    return profiles, manifest


def selected_aliases(benchmark):
    blocked = {r["source_id"] for r in benchmark.examples if r["example_kind"] == "synonym"}
    groups = defaultdict(list)
    for row in benchmark.aliases:
        if row["source_id"] not in blocked and len(row["name"]) <= 160:
            groups[row["canonical_id"]].append(row)
    return {target: sorted(rows, key=lambda r: (hash_order("ao3-stage2-alias-v1", r["source_id"]), r["source_id"]))[:5]
            for target, rows in groups.items()}


def render(name, view, profile=None, aliases=(), denominator=1, one_work=False):
    if view == "raw":
        return name
    lines = [f"Tag: {name}", "Type: Freeform"]
    if view == "template":
        return "\n".join(lines)
    if aliases:
        lines.append("Other names: " + "; ".join(aliases))
    if not profile or not profile["n"] or view == "aliases":
        return "\n".join(lines)
    if view in ("fandom", "cotags", "cotags_length", "all"):
        kinds = ("Fandom",) if view == "fandom" else COTAG_LIMITS
        for kind in kinds:
            names = (profile.get("one_work", {}).get(kind, []) if one_work else
                     [r["name"] for r in profile["co_tags"].get(kind, [])])
            names = [n for n in names if len(n) <= 160]
            if names:
                lines.append(f"Observed {kind.lower()} context: " + "; ".join(names))
    if profile["n"] >= PROTOCOL["profile_minimum_works"]:
        if view in ("length", "cotags_length", "all") and profile["words_n"]:
            lines.append("Work lengths: " + ", ".join(f"{label} {100 * n / profile['words_n']:.0f}%"
                         for label, n in zip(("short", "medium", "long", "epic"), profile["bands"])))
        if view in ("usage", "all"):
            share = profile["n"] / denominator
            tier = "very rare" if share < 1e-6 else "uncommon" if share < 1e-4 else "common" if share < 1e-2 else "very common"
            lines.append(f"Observed usage frequency: {tier}")
        if view in ("metadata", "all"):
            languages = sorted(profile["languages"], key=lambda lang: (-profile["languages"][lang], lang))[:2]
            lines.append("Work language codes: " + ", ".join(languages))
            lines.append(f"Works complete: {100 * profile['complete'] / profile['n']:.0f}%; restricted: {100 * profile['restricted'] / profile['n']:.0f}%")
            for kind in META_TYPES:
                names = [r["name"] for r in profile["co_tags"].get(kind, [])]
                if names:
                    lines.append(f"Observed {kind}: " + "; ".join(names))
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("data/ao3-benchmark"))
    parser.add_argument("--works", type=Path, default=Path("data/ao3-dump/works-20210226.csv"))
    args = parser.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    benchmark = load_benchmark(args.dataset)
    freeze(OUTPUT / "protocol.json", {"protocol": PROTOCOL, "benchmark": benchmark.provenance})
    profiles, manifest = build_profiles(benchmark, args.dataset, args.works, ROOT)
    write_json(OUTPUT / "coverage.json", manifest)
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == "__main__":
    main()
