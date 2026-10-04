#!/usr/bin/env python3
"""Freeze a review sample of unmerged AO3 tags and collect retrieval evidence.

No reference labels are inferred from retrieval scores or the absence of a merger.
The existing benchmark, its held-out aliases, and its vectors remain unchanged.
"""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import csv
import json
from pathlib import Path
import time

import numpy as np
import psycopg

from ao3_benchmark import csv_rows, fingerprint, hash_order, load_benchmark, sha256, write_csv, write_json
from ao3_representations import (ROOT as STAGE2, add_work, freeze, new_profile,
                                 selected_aliases, summarize_profile, visible_context, work_assignment)
from ao3_scoring import rank_row
from cache_ao3_embeddings import parse_vectors, request_batch, validate_vectors
from run_ao3_alias_pooling import pool_scores
from run_ao3_representations import embedding_batches, normalize
from run_stage1_baselines import temporary_postgres


ROOT = Path("data/ao3-unmerged-review")
OUTPUT = Path("results/ao3/unmerged-review")
SEED = "ao3-unmerged-review-v1"
METHODS = ("template", "template_alias_max", "lexical_alias_max")
LABEL_FIELDS = ["source_id", "reviewer", "judgment", "canonical_id", "proposed_name",
                "confidence", "catalogue_checked", "rationale"]
PROPOSAL_FIELDS = ["source_id", "action", "canonical_id", "proposed_name", "rationale"]
PROTOCOL = {
    "version": SEED,
    "population": "Visible, named, noncanonical Freeform rows with an empty merger_id in the 2021-02-26 snapshot; unlabelled, not negatives",
    "sampling": {"seed": SEED, "random": 400, "screen": 2000, "challenge": 100,
                 "random_rule": "400 smallest SHA256(seed:random:source_id), numeric ID ties; equal-probability tag sample",
                 "screen_rule": "2000 smallest independent SHA256(seed:screen:source_id) among the remaining population",
                 "random_split": "Independent SHA256(seed:partition:source_id) order: first 200 review calibration, remaining 200 review validation",
                 "challenge_rule": "Sequential disjoint quotas from the screen: low pooled score 20, low pooled margin 20, different semantic top IDs 20, unsupported template top ID 20, highest approximate usage 10, non-ASCII names 10; source-ID hash breaks ties; hash-order fallback if a stratum is exhausted",
                 "estimation": "Only the 400 random tags estimate population proportions; challenge cases are diagnostic and never pooled into population estimates"},
    "retrieval": {"model": "openai/text-embedding-3-small", "dimensions": 1536,
                  "text": "Tag: {name}\nType: Freeform", "candidates": 181067,
                  "aliases": "The same 48080 selected Stage 2 development aliases; globally exclude all 2000 evaluated aliases; no calibration/test aliases",
                  "semantic": "Exhaustive float64 cosine, template-only and maximum over individual canonical/alias vectors",
                  "lexical": "Exhaustive PostgreSQL pg_trgm similarity(query, raw name), maximum over canonical and the same selected alias names, on the final 500",
                  "shortlist": "Top 10 unique canonical IDs per method; retain their union; numeric canonical ID ascending on ties",
                  "scores": "Similarity and margins are descriptive, not probabilities or acceptance thresholds"},
    "context": "Raw source-ID occurrences in Stage 2 work buckets 6-7 only; up to 32 hash-sampled works for canonical co-tags; Stage 2 candidate support profiles are reused; evidence is not embedded or treated as equivalence",
    "reference": {"labels": ["synonym", "new_canonical", "unresolved"],
                  "synonym": "One existing Freeform canonical preserves the whole meaning, including scope and qualifiers",
                  "new_canonical": "A distinct useful concept, after a documented search beyond the shortlist; a proposal for this snapshot, not an AO3 wrangling decision",
                  "unresolved": "Ambiguous, composite, expressive, wrong-type, insufficient evidence, or no useful filterable concept",
                  "required": "Named reviewer, rationale, confidence; canonical ID for synonym; proposed name and catalogue_checked=yes for new_canonical",
                  "independence": "Keep model/system proposals separate from reference judgments; model-generated labels cannot establish their own accuracy",
                  "review": "Search the full catalogue beyond the shortlist; record scope differences; independently review new-canonical decisions and disagreements before final claims"},
    "evaluation": {"baselines": ["always_template", "always_alias_max", "semantic_agreement_else_abstain"],
                   "metrics": "Synonym precision and false merges on resolved judgments, coverage, unresolved acceptance, shortlist recall for reviewed synonyms, and new-canonical proposal precision for separately supplied proposals",
                   "uncertainty": "Wilson 95% intervals for descriptive tag-level proportions; tags may share concepts; report target overlap between review splits",
                   "calibration": "Tune any future thresholds using review calibration only; freeze them before reporting review validation; challenge cases are development diagnostics",
                   "no_labels": "Report pending with null precision/recall; never substitute similarity or an LLM judgment for human reference labels"},
    "original_calibration_and_test_evaluated": False,
}

# Preserve the original acquisition protocol and its cache fingerprints. This
# user-requested amendment changes review requirements, not retrieval or scoring.
REVIEW_AMENDMENT = {
    "version": "ao3-unmerged-review-optional-name-v1", "date": "2026-10-04",
    "original_protocol_sha256": fingerprint(PROTOCOL),
    "requested_change": "Allow proposed_name to remain blank for new_canonical reference judgments and system proposals",
    "reason": "This stage evaluates whether a concept needs a new canonical; choosing its name can follow later",
    "reference_requirements": "Named reviewer, rationale, confidence and catalogue_checked=yes; no existing canonical ID; proposed_name optional",
    "system_proposal_requirements": "new_canonical action with no existing canonical ID; proposed_name optional",
    "unchanged": "Sampling, partitions, retrieval, label meanings, metrics and the existing review packet identity",
}


def checked_cache(path, recipe, calculate):
    if path.exists():
        value = json.loads(path.read_text())
        if value["recipe"] != recipe or value["payload_sha256"] != fingerprint(value["payload"]):
            raise ValueError(f"Changed cached inputs or content: {path}")
        return value["payload"]
    payload = calculate()
    write_json(path, {"recipe": recipe, "payload_sha256": fingerprint(payload), "payload": payload})
    return payload


def is_eligible(row):
    return (row["type"] == "Freeform" and row["canonical"] == "false" and not row["merger_id"]
            and row["name"] != "Redacted" and bool(row["name"].strip()))


def sample_population(rows, random_n=400, screen_n=2000):
    if len({r["source_id"] for r in rows}) != len(rows) or len(rows) < random_n + screen_n:
        raise ValueError("Duplicate IDs or insufficient eligible population")
    random_rows = sorted(rows, key=lambda r: (hash_order(SEED + ":random", r["source_id"]), r["source_id"]))[:random_n]
    chosen = {r["source_id"] for r in random_rows}
    screen = sorted((r for r in rows if r["source_id"] not in chosen),
                    key=lambda r: (hash_order(SEED + ":screen", r["source_id"]), r["source_id"]))[:screen_n]
    calibration = {r["source_id"] for r in sorted(random_rows, key=lambda r: hash_order(SEED + ":partition", r["source_id"]))[:random_n // 2]}
    return ([{**r, "arm": "random", "partition": "calibration" if r["source_id"] in calibration else "validation",
              "selection_reason": "random"} for r in random_rows], screen)


def prepare(benchmark):
    ROOT.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    freeze(OUTPUT / "protocol.json", {"protocol": PROTOCOL, "benchmark": benchmark.provenance})
    freeze(OUTPUT / "review-amendment-2026-10-04.json", REVIEW_AMENDMENT)
    tag_path = Path(benchmark.manifest["source_directory"]) / "tags-20210226.csv"
    source_hash = sha256(tag_path)
    if source_hash != benchmark.manifest["source_files"][tag_path.name]["sha256"]:
        raise ValueError("Tag dump differs from audited source")

    def scan():
        rows = [{"source_id": int(r["id"]), "name": r["name"], "cached_count": int(r["cached_count"])}
                for r in csv_rows(tag_path) if is_eligible(r)]
        expected = next(r["visible_unmerged_noncanonical"] for r in benchmark.manifest["by_type"] if r["type"] == "Freeform")
        if len(rows) != expected:
            raise ValueError("Unmerged population differs from audit")
        return rows

    population = checked_cache(ROOT / "population.json", {"tags_sha256": source_hash, "rule": PROTOCOL["population"]}, scan)
    random_rows, screen = sample_population(population)
    recipe = {"protocol_sha256": fingerprint(PROTOCOL), "population_sha256": fingerprint(population),
              "population": len(population), "random": random_rows, "screen": screen}
    freeze(ROOT / "screen.json", recipe)
    print(f"Frozen {len(random_rows)} random tags and {len(screen)} screening tags from {len(population):,}", flush=True)
    return population, random_rows, screen


def query_vectors(rows, offline):
    texts = [PROTOCOL["retrieval"]["text"].format(name=r["name"]) for r in rows]
    freeze(ROOT / "query-inputs.json", {"model": PROTOCOL["retrieval"]["model"], "texts": texts})
    directory = ROOT / "embeddings"
    directory.mkdir(exist_ok=True)
    vectors = np.empty((len(texts), 1536), dtype=np.float32)
    metadata = []
    for batch_number, (start, end) in enumerate(embedding_batches(texts, 0)):
        batch = texts[start:end]
        path = directory / f"batch-{batch_number:03d}.npy"
        meta_path = path.with_suffix(".json")
        if path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text())
            if meta["texts_sha256"] != fingerprint(batch) or meta["vectors_sha256"] != sha256(path):
                raise ValueError("Review query embedding checksum mismatch")
            array = np.load(path, allow_pickle=False)
            validate_vectors(array, len(batch))
        else:
            if offline:
                raise ValueError("Missing query embedding batch in offline mode")
            response, seconds, attempts = request_batch(batch)
            if response.get("model") not in ("text-embedding-3-small", PROTOCOL["retrieval"]["model"]):
                raise ValueError("Unexpected embedding model")
            array = parse_vectors(response, len(batch))
            np.save(path, array, allow_pickle=False)
            meta = {"texts_sha256": fingerprint(batch), "vectors_sha256": sha256(path),
                    "model": response.get("model"), "provider": response.get("provider"),
                    "usage": response.get("usage"), "seconds": seconds, "attempts": attempts,
                    "fetched_at_utc": datetime.now(timezone.utc).isoformat()}
            write_json(meta_path, meta)
        vectors[start:end] = array
        metadata.append(meta)
        print(f"Review query embeddings: {end}/{len(texts)}", flush=True)
    report = {"strings": len(texts), "batches": len(metadata), "metadata_sha256": fingerprint(metadata),
              "prompt_tokens": sum((m.get("usage") or {}).get("prompt_tokens", 0) for m in metadata),
              "reported_cost_usd": sum(m["usage"]["cost"] for m in metadata) if all("cost" in (m.get("usage") or {}) for m in metadata) else None,
              "model": PROTOCOL["retrieval"]["model"], "dimensions": 1536,
              "providers": sorted({str(m["provider"]) for m in metadata}), "attempts": sum(m["attempts"] for m in metadata)}
    write_json(OUTPUT / "embeddings.json", report)
    return normalize(vectors), report


def top_row(scores, ids):
    # Reuse the tested cutoff/tie handling; unlabelled queries have no expected ID.
    row = rank_row(scores, ids, 0)
    return {key: row[key] for key in ("top_ids", "top_scores")}


def load_alias_vectors(selected):
    directory = STAGE2 / "alias-pooling"
    inputs = json.loads((directory / "inputs.json").read_text())
    if inputs["recipe"]["alias_ids_sha256"] != fingerprint([r["source_id"] for r in selected]):
        raise ValueError("Selected aliases differ from Stage 2")
    texts = inputs["texts"]
    lookup = {text: i for i, text in enumerate(texts)}
    all_vectors = np.empty((len(texts), 1536), dtype=np.float32)
    metas = []
    for start in range(0, len(texts), 512):
        path = directory / f"batch-{start // 512:04d}.npy"
        meta = json.loads(path.with_suffix(".json").read_text())
        if meta["texts_sha256"] != fingerprint(texts[start:start + 512]) or meta["vectors_sha256"] != sha256(path):
            raise ValueError("Stage 2 alias vector checksum mismatch")
        array = np.load(path, allow_pickle=False)
        validate_vectors(array, min(512, len(texts) - start))
        all_vectors[start:start + len(array)] = array
        metas.append(meta)
    stage2_report = json.loads(Path("results/ao3/stage2/alias-pooling.json").read_text())
    if fingerprint(metas) != stage2_report["embeddings"]["batch_metadata_sha256"]:
        raise ValueError("Alias vector metadata differs from recorded experiment")
    positions = [lookup[PROTOCOL["retrieval"]["text"].format(name=r["name"])] for r in selected]
    return normalize(all_vectors[positions]), fingerprint(metas)


def semantic(rows, benchmark, offline):
    selected = sorted((a for group in selected_aliases(benchmark).values() for a in group), key=lambda r: r["source_id"])
    inputs = json.loads((STAGE2 / "inputs-manifest.json").read_text())
    base = json.loads((STAGE2 / "embeddings/manifest.json").read_text())
    if (sha256(STAGE2 / "positions.npz") != inputs["positions.npz_sha256"]
            or sha256(STAGE2 / "embeddings/vectors.npy") != base["vectors_sha256"]
            or base["recipe"]["inputs_sha256"] != fingerprint(inputs)
            or inputs["benchmark"] != benchmark.provenance):
        raise ValueError("Stage 2 canonical vectors/positions differ from frozen benchmark")
    queries, embedding_meta = query_vectors(rows, offline)
    aliases, alias_hash = load_alias_vectors(selected)
    recipe = {"queries_sha256": fingerprint(rows), "embeddings": embedding_meta,
              "candidate_vectors_sha256": base["vectors_sha256"], "alias_metadata_sha256": alias_hash,
              "protocol_sha256": fingerprint(PROTOCOL)}

    def score():
        ids = np.asarray([r["canonical_id"] for r in benchmark.catalogue])
        lookup = {int(t): i for i, t in enumerate(ids)}
        targets = np.asarray([lookup[a["canonical_id"]] for a in selected])
        matrix = np.load(STAGE2 / "embeddings/vectors.npy", mmap_mode="r", allow_pickle=False)
        positions = np.load(STAGE2 / "positions.npz", allow_pickle=False)["candidate_template"]
        canonical = normalize(matrix[positions])
        results = {}
        started = time.perf_counter()
        for start in range(0, len(rows), 32):
            base_scores = queries[start:start + 32] @ canonical.T
            alias_scores = queries[start:start + 32] @ aliases.T
            for i, (one, extra) in enumerate(zip(base_scores, alias_scores), start):
                results[str(rows[i]["source_id"])] = {"template": top_row(one, ids),
                    "template_alias_max": top_row(pool_scores(one, extra, targets), ids)}
            if start % 320 == 0:
                print(f"Semantic screening: {min(start + 32, len(rows))}/{len(rows)} ({time.perf_counter() - started:.1f}s)", flush=True)
        return results

    return checked_cache(ROOT / "semantic.json", recipe, score)


def select_challenge(screen, scores, supported):
    def features(row):
        s = scores[str(row["source_id"])]
        name, pooled = s["template"], s["template_alias_max"]
        return {"score": pooled["top_scores"][0], "margin": pooled["top_scores"][0] - pooled["top_scores"][1],
                "disagree": name["top_ids"][0] != pooled["top_ids"][0],
                "unsupported": name["top_ids"][0] not in supported}

    values = {r["source_id"]: features(r) for r in screen}
    rules = [("low_similarity", 20, lambda r: True, lambda r: values[r["source_id"]]["score"]),
             ("small_margin", 20, lambda r: True, lambda r: values[r["source_id"]]["margin"]),
             ("semantic_disagreement", 20, lambda r: values[r["source_id"]]["disagree"], lambda r: 0),
             ("unsupported_name_target", 20, lambda r: values[r["source_id"]]["unsupported"], lambda r: 0),
             ("high_usage", 10, lambda r: True, lambda r: -r["cached_count"]),
             ("non_ascii", 10, lambda r: not r["name"].isascii(), lambda r: 0)]
    chosen, results = set(), []
    for label, count, eligible, priority in rules:
        ordered = sorted((r for r in screen if r["source_id"] not in chosen and eligible(r)),
                         key=lambda r: (priority(r), hash_order(SEED + ":challenge:" + label, r["source_id"]), r["source_id"]))
        selected = [(r, label) for r in ordered[:count]]
        current_ids = chosen | {r["source_id"] for r, _ in selected}
        fallback = sorted((r for r in screen if r["source_id"] not in current_ids),
                          key=lambda r: hash_order(SEED + ":fallback:" + label, r["source_id"]))
        selected.extend((r, label + "_fallback") for r in fallback[:count - len(selected)])
        for row, reason in selected:
            chosen.add(row["source_id"])
            results.append({**row, "arm": "challenge", "partition": "challenge", "selection_reason": reason})
    if len(results) != 100 or len(chosen) != 100:
        raise ValueError("Challenge screening pool is too small")
    return results


def lexical(rows, benchmark):
    selected = sorted((a for group in selected_aliases(benchmark).values() for a in group), key=lambda r: r["source_id"])
    ids = np.asarray([r["canonical_id"] for r in benchmark.catalogue])
    lookup = {int(t): i for i, t in enumerate(ids)}
    targets = np.asarray([lookup[a["canonical_id"]] for a in selected])
    terms = [r["name"] for r in benchmark.catalogue] + [r["name"] for r in selected]
    recipe = {"queries_sha256": fingerprint(rows), "terms_sha256": fingerprint(terms),
              "protocol_sha256": fingerprint(PROTOCOL)}
    directory = ROOT / "lexical"
    directory.mkdir(exist_ok=True)
    missing = [r for r in rows if not (directory / f"{r['source_id']}.json").exists()]
    if missing:
        with temporary_postgres() as connection:
            connection.execute("CREATE EXTENSION pg_trgm")
            connection.execute("CREATE TABLE term (id integer PRIMARY KEY, name text NOT NULL)")
            with connection.cursor().copy("COPY term (id, name) FROM STDIN") as copy:
                for i, term in enumerate(terms):
                    copy.write_row((i, term))
            write_json(OUTPUT / "postgres.json", {"server": connection.execute("SELECT version()").fetchone()[0],
                       "pg_trgm": connection.execute("SELECT extversion FROM pg_extension WHERE extname='pg_trgm'").fetchone()[0],
                       "locale": "C", "function": "similarity(query, raw_name)", "exhaustive": True,
                       "terms": len(terms), "canonical_ids": len(ids), "workers": 4})
            parameters = connection.info.get_parameters()

            def partition(part):
                with psycopg.connect(**parameters, autocommit=True) as worker:
                    for row in part:
                        def calculate():
                            with worker.cursor(binary=True) as cursor:
                                cursor.execute("SELECT similarity(%s, name) FROM term ORDER BY id", (row["name"],))
                                values = np.asarray(cursor.fetchall(), dtype=np.float32).reshape(-1)
                            if values.shape != (len(terms),):
                                raise ValueError("Incorrect lexical score count")
                            return top_row(pool_scores(values[:len(ids)], values[len(ids):], targets), ids)
                        checked_cache(directory / f"{row['source_id']}.json", recipe, calculate)

            started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(partition, missing[i:i + 20]) for i in range(0, len(missing), 20)]
                for i, future in enumerate(futures):
                    future.result()
                    print(f"Lexical review candidates: {min((i + 1) * 20, len(missing))}/{len(missing)} ({time.perf_counter() - started:.1f}s)", flush=True)
    return {str(r["source_id"]): checked_cache(directory / f"{r['source_id']}.json", recipe,
            lambda: (_ for _ in ()).throw(ValueError("Missing lexical result"))) for r in rows}


def work_profiles(rows, benchmark):
    path = Path(benchmark.manifest["source_directory"]) / "works-20210226.csv"
    file_hash = sha256(path)
    if file_hash != benchmark.manifest["source_files"][path.name]["sha256"]:
        raise ValueError("Works dump differs from audited source")
    recipe = {"source_ids": sorted(r["source_id"] for r in rows), "works_sha256": file_hash,
              "protocol_sha256": fingerprint(PROTOCOL)}

    def scan():
        metadata = {int(r["canonical_id"]): (r["name"], r["type"]) for r in csv_rows(Path("data/ao3-benchmark/catalogue.csv"))}
        profiles = {r["source_id"]: new_profile() for r in rows}
        query_ids = set(profiles)
        selected_works = set()
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            if next(reader)[:6] != ["creation date", "language", "restricted", "complete", "word_count", "tags"]:
                raise ValueError("Unexpected works header")
            for ordinal, row in enumerate(reader, 1):
                if len(row) != 6:
                    raise ValueError("Malformed work row")
                tags = set(map(int, row[5].split("+"))) if row[5] else set()
                present = tags & query_ids
                if not present:
                    continue
                bucket, priority = work_assignment(file_hash, ordinal)
                if bucket not in (6, 7):
                    continue
                selected_works.add(ordinal)
                context = visible_context(tags, metadata)
                for tag_id in present:
                    add_work(profiles[tag_id], priority, ordinal, context, row[1], row[3] == "true", row[2] == "true",
                             int(row[4]) if row[4] else None)
        # Counts dominate co-tag ordering; no background association tie-break in this review-only evidence.
        result = {str(t): summarize_profile(p, t, metadata, {}) for t, p in profiles.items()}
        return {"query": result, "unique_context_works": len(selected_works), "total_work_rows": ordinal,
                "tags_with_context": sum(p["n"] > 0 for p in profiles.values()),
                "tags_with_at_least_three_context_works": sum(p["n"] >= 3 for p in profiles.values()),
                "co_tag_order": "sample frequency, then numeric ID; no background association"}

    return checked_cache(ROOT / "profiles.json", recipe, scan)


def initialize_editable_csv(path, fields, rows):
    # Reproduction must never erase a reviewer's work.
    if path.exists():
        existing = list(csv_rows(path))
        if (not existing or set(existing[0]) != set(fields)
                or len(existing) != len(rows)
                or {int(r["source_id"]) for r in existing} != {r["source_id"] for r in rows}):
            raise ValueError(f"Review file has changed IDs/schema: {path}")
        return
    write_csv(path, fields, [{key: row["source_id"] if key == "source_id" else "" for key in fields} for row in rows])


def build_packet(rows, population, semantic_rows, lexical_rows, profiles, benchmark):
    catalogue = {r["canonical_id"]: r for r in benchmark.catalogue}
    selected = selected_aliases(benchmark)
    profile_manifest = json.loads((STAGE2 / "profiles-manifest.json").read_text())
    if sha256(STAGE2 / "profiles.json") != profile_manifest["profiles_sha256"]:
        raise ValueError("Stage 2 candidate profiles changed")
    support = json.loads((STAGE2 / "profiles.json").read_text())["candidate"]
    cases = []
    for row in rows:
        key = str(row["source_id"])
        rankings = {**semantic_rows[key], "lexical_alias_max": lexical_rows[key]}
        # Present candidates in ID order by default to reduce rank anchoring.
        union = sorted({t for method in rankings.values() for t in method["top_ids"]})
        candidates = [{"canonical_id": t, "name": catalogue[t]["name"], "cached_count": catalogue[t]["cached_count"],
                       "aliases": [{"source_id": a["source_id"], "name": a["name"]} for a in selected.get(t, [])],
                       "profile": support[str(t)]} for t in union]
        cases.append({**row, "profile": profiles["query"][key], "rankings": rankings, "candidates": candidates})
    freeze(ROOT / "cases.json", cases)
    sample_fields = ["source_id", "name", "cached_count", "arm", "partition", "selection_reason"]
    sample_path = OUTPUT / "sample.csv"
    if sample_path.exists():
        if [{k: str(r[k]) for k in sample_fields} for r in rows] != list(csv_rows(sample_path)):
            raise ValueError("Frozen sample changed")
    else:
        write_csv(sample_path, sample_fields, rows)
    initialize_editable_csv(OUTPUT / "judgments.csv", LABEL_FIELDS, rows)
    initialize_editable_csv(OUTPUT / "proposals.csv", PROPOSAL_FIELDS, rows)
    retrieval_rows = []
    for case in cases:
        for method, scores in case["rankings"].items():
            for rank, (t, score) in enumerate(zip(scores["top_ids"], scores["top_scores"]), 1):
                retrieval_rows.append({"source_id": case["source_id"], "method": method, "rank": rank,
                                       "canonical_id": t, "name": catalogue[t]["name"], "score": score})
    retrieval_path = OUTPUT / "candidates.csv"
    write_csv(retrieval_path, ["source_id", "method", "rank", "canonical_id", "name", "score"], retrieval_rows)
    diagnostics = []
    for arm in ("random", "challenge"):
        arm_cases = [c for c in cases if c["arm"] == arm]
        pooled = [c["rankings"]["template_alias_max"] for c in arm_cases]
        diagnostics.append({"arm": arm, "tags": len(arm_cases),
            "semantic_top1_agreement": sum(c["rankings"]["template"]["top_ids"][0] == c["rankings"]["template_alias_max"]["top_ids"][0] for c in arm_cases),
            "all_three_top1_agreement": sum(len({s["top_ids"][0] for s in c["rankings"].values()}) == 1 for c in arm_cases),
            "template_top1_without_aliases": sum(c["rankings"]["template"]["top_ids"][0] not in selected for c in arm_cases),
            "non_ascii_names": sum(not c["name"].isascii() for c in arm_cases),
            "with_context": sum(c["profile"]["n"] > 0 for c in arm_cases),
            "with_three_context_works": sum(c["profile"]["n"] >= 3 for c in arm_cases),
            "pooled_top_score_quantiles": np.quantile([p["top_scores"][0] for p in pooled], [0, .25, .5, .75, 1]).tolist(),
            "approximate_usage_quantiles": np.quantile([c["cached_count"] for c in arm_cases], [0, .25, .5, .75, 1]).tolist()})
    all_query_works = {w for c in cases for w in profiles["query"][str(c["source_id"])]["sample_work_ordinals"]}
    candidate_ids = {t["canonical_id"] for c in cases for t in c["candidates"]}
    all_support_works = {w for t in candidate_ids for w in support[str(t)]["sample_work_ordinals"]}
    if all_query_works & all_support_works:
        raise ValueError("Review query evidence overlaps candidate support works")
    membership_checks = 0
    for case in cases:
        for prediction in case["rankings"].values():
            ids, scores = prediction["top_ids"], prediction["top_scores"]
            if (len(ids) != 10 or len(set(ids)) != 10 or len(scores) != 10
                    or not set(ids) <= catalogue.keys() or not np.isfinite(scores).all()
                    or list(zip(scores, ids)) != sorted(zip(scores, ids), key=lambda v: (-v[0], v[1]))):
                raise ValueError("Invalid full-catalogue shortlist")
            membership_checks += len(ids)
    original_queries = {r["source_id"] for r in benchmark.examples}
    if original_queries & {r["source_id"] for r in rows}:
        raise ValueError("Review sample overlaps the original labelled benchmark queries")
    validation = {"sampled_tags": len(rows), "unique_source_ids": len({r["source_id"] for r in rows}),
                  "shortlist_id_score_checks": membership_checks,
                  "query_sample_work_records": len(all_query_works), "candidate_sample_work_records": len(all_support_works),
                  "sample_work_overlap": 0, "original_benchmark_query_overlap": 0,
                  "blank_reference_labels_are_not_no_match": True,
                  "original_calibration_and_test_aliases_indexed": False}
    write_json(OUTPUT / "validation.json", validation)
    summary = {"status": "retrieval_complete_reference_review_pending", "population": len(population),
               "sample": len(rows), "arms": dict(Counter(r["arm"] for r in rows)),
               "partitions": dict(Counter(r["partition"] for r in rows)),
               "selection_reasons": dict(Counter(r["selection_reason"] for r in rows)),
               "diagnostics_are_not_accuracy": diagnostics,
               "context": {k: v for k, v in profiles.items() if k != "query"},
               "reference_judgments_generated": 0, "synonym_precision": None, "new_canonical_proposal_precision": None,
               "embeddings": json.loads((OUTPUT / "embeddings.json").read_text()),
               "artifacts": {str(p): sha256(p) for p in (OUTPUT / "protocol.json", OUTPUT / "review-amendment-2026-10-04.json", sample_path, retrieval_path, ROOT / "cases.json", ROOT / "profiles.json", ROOT / "semantic.json", OUTPUT / "validation.json")},
               "original_calibration_and_test_evaluated": False,
               "sources_sha256": {name: sha256(Path("scripts") / name) for name in ("ao3_unmerged_review.py", "analyze_ao3_review.py", "ao3_review_html.py")}}
    write_json(OUTPUT / "summary.json", summary)
    from ao3_review_html import write_review_html
    write_review_html(ROOT, cases, benchmark.catalogue, selected, population, LABEL_FIELDS)
    print(f"Prepared {len(cases)} review cases; reference labels remain independent and unfilled", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("prepare", "score", "packet", "all"), default="all")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    benchmark = load_benchmark()
    population, random_rows, screen = prepare(benchmark)
    if args.phase == "prepare":
        return
    semantic_rows = semantic(random_rows + screen, benchmark, args.offline)
    challenge = select_challenge(screen, semantic_rows, set(selected_aliases(benchmark)))
    rows = sorted(random_rows + challenge, key=lambda r: hash_order(SEED + ":review_order", r["source_id"]))
    freeze(ROOT / "selection.json", {"protocol_sha256": fingerprint(PROTOCOL), "rows": rows})
    if args.phase == "score":
        return
    lexical_rows = lexical(rows, benchmark)
    profiles = work_profiles(rows, benchmark)
    build_packet(rows, population, semantic_rows, lexical_rows, profiles, benchmark)


if __name__ == "__main__":
    main()
