#!/usr/bin/env python3
"""Collect, rank and report the preregistered AO3 representation comparisons."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import threading
import time

import numpy as np
import psycopg

from ao3_benchmark import fingerprint, load_benchmark, sha256, write_json
from ao3_representations import (CONTEXT_VIEWS, OUTPUT, PROTOCOL, ROOT, VIEWS, freeze,
                                 render, selected_aliases)
from ao3_scoring import rank_row, read_checkpoint, save_checkpoint
from cache_ao3_embeddings import CONFIG, parse_vectors, request_batch, validate_vectors
from run_stage1_baselines import temporary_postgres


def prepare(benchmark):
    manifest_path = ROOT / "inputs-manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest["protocol_sha256"] != fingerprint(PROTOCOL):
            raise ValueError("Representation protocol mismatch")
        for filename in ("texts.json", "positions.npz", "profiles.json"):
            if sha256(ROOT / filename) != manifest[filename + "_sha256"]:
                raise ValueError(f"Representation checksum failure: {filename}")
        return json.loads((ROOT / "texts.json").read_text()), manifest
    coverage = json.loads((ROOT / "profiles-manifest.json").read_text())
    if sha256(ROOT / "profiles.json") != coverage["profiles_sha256"]:
        raise ValueError("Profile checksum failure")
    profiles = json.loads((ROOT / "profiles.json").read_text())
    original = json.loads(Path("results/ao3/stage1/summary.json").read_text())["embedding"]
    base = Path(original["cache_directory"])
    if sha256(base / "inputs.json") != original["inputs_sha256"]:
        raise ValueError("Stage 1 embedding inputs changed")
    texts = json.loads((base / "inputs.json").read_text())["texts"]
    base_count = len(texts)
    lookup = {text: i for i, text in enumerate(texts)}
    selections = selected_aliases(benchmark)
    positions, availability = {}, {}

    def register(text):
        if not text or len(text.encode("utf-8")) > 8192:
            raise ValueError("Representation exceeds frozen embedding limits")
        if text not in lookup:
            lookup[text] = len(texts)
            texts.append(text)
        return lookup[text]

    for view in VIEWS:
        positions["candidate_" + view] = np.asarray([
            register(render(r["name"], view, profiles["candidate"][str(r["canonical_id"])],
                            [a["name"] for a in selections.get(r["canonical_id"], [])],
                            profiles["counts"]["support_works"])) for r in benchmark.catalogue], dtype=np.int32)
        positions["query_" + view] = np.asarray([
            register(render(r["incoming_tag"], view, profiles["query"][str(r["source_id"])],
                            denominator=profiles["counts"]["development_context_works"])) for r in benchmark.examples], dtype=np.int32)
        print(f"Prepared {view}: {len(texts):,} unique strings", flush=True)
    positions["query_one_work"] = np.asarray([
        register(render(r["incoming_tag"], "cotags", profiles["query"][str(r["source_id"])],
                        denominator=profiles["counts"]["development_context_works"], one_work=True))
        for r in benchmark.examples], dtype=np.int32)
    for view in CONTEXT_VIEWS:
        availability[view] = {
            "candidates_with_additional_text": int(np.count_nonzero(positions["candidate_" + view] != positions["candidate_aliases"])),
            "synonym_queries_with_additional_text": int(sum(
                r["example_kind"] == "synonym" and positions["query_" + view][i] != positions["query_template"][i]
                for i, r in enumerate(benchmark.examples))),
        }
    write_json(ROOT / "texts.json", texts)
    np.savez_compressed(ROOT / "positions.npz", **positions)
    manifest = {"protocol_sha256": fingerprint(PROTOCOL), "benchmark": benchmark.provenance,
                "base_embedding_cache": str(base), "base_strings": base_count,
                "unique_strings": len(texts), "novel_strings": len(texts) - base_count,
                "selected_alias_examples": sum(map(len, selections.values())),
                "availability": availability,
                **{filename + "_sha256": sha256(ROOT / filename) for filename in ("texts.json", "positions.npz", "profiles.json")}}
    write_json(manifest_path, manifest)
    write_json(OUTPUT / "representations.json", manifest)
    return texts, manifest


def embedding_batches(texts, start):
    batches, count, size, begin = [], 0, 0, start
    for i in range(start, len(texts)):
        length = len(texts[i].encode("utf-8"))
        if count and (count == 512 or size + length > 280000):
            batches.append((begin, i))
            begin, count, size = i, 0, 0
        count += 1
        size += length
    if count:
        batches.append((begin, len(texts)))
    return batches


def collect(texts, inputs, offline=False):
    if CONFIG["model"] != PROTOCOL["embedding_model"] or CONFIG["dimensions"] != PROTOCOL["dimensions"]:
        raise ValueError("Embedding request adapter differs from the frozen model")
    directory = ROOT / "embeddings"
    directory.mkdir(exist_ok=True)
    manifest_path = directory / "manifest.json"
    vector_path = directory / "vectors.npy"
    recipe = {"inputs_sha256": fingerprint(inputs), "model": PROTOCOL["embedding_model"],
              "dimensions": 1536, "batch_maximum": 512, "batch_byte_maximum": 280000}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest["recipe"] != recipe or sha256(vector_path) != manifest["vectors_sha256"]:
            raise ValueError("Embedding matrix integrity failure")
        return manifest
    freeze(directory / "recipe.json", recipe)
    vectors = np.lib.format.open_memmap(vector_path, mode="w+", dtype=np.float32, shape=(len(texts), 1536))
    base = Path(inputs["base_embedding_cache"])
    for start in range(0, inputs["base_strings"], 1024):
        stem = f"batch-{start // 1024:04d}"
        metadata = json.loads((base / (stem + ".json")).read_text())
        path = base / (stem + ".npy")
        batch = np.load(path, allow_pickle=False)
        if sha256(path) != metadata["vectors_sha256"] or fingerprint(texts[start:start + len(batch)]) != metadata["texts_sha256"]:
            raise ValueError("Stage 1 vector cache changed")
        validate_vectors(batch, len(batch))
        vectors[start:start + len(batch)] = batch
    batches = embedding_batches(texts, inputs["base_strings"])
    print(f"Reused {inputs['base_strings']:,} Stage 1 embeddings; {len(batches):,} new batches", flush=True)

    def one(item):
        index, (start, end) = item
        path = directory / f"batch-{index:05d}.npy"
        meta_path = path.with_suffix(".json")
        batch_texts = texts[start:end]
        if path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text())
            if meta["texts_sha256"] != fingerprint(batch_texts) or meta["vectors_sha256"] != sha256(path):
                raise ValueError("Representation embedding batch checksum failure")
            batch = np.load(path, allow_pickle=False)
            validate_vectors(batch, end - start)
        else:
            if offline:
                raise ValueError(f"Missing representation embedding batch {index}")
            payload, seconds, attempts = request_batch(batch_texts)
            if payload.get("model") not in ("text-embedding-3-small", "openai/text-embedding-3-small"):
                raise ValueError("Provider returned a different model")
            batch = parse_vectors(payload, end - start)
            temporary = path.with_suffix(".npy.tmp")
            with temporary.open("wb") as handle:
                np.save(handle, batch, allow_pickle=False)
            temporary.replace(path)
            meta = {"start": start, "end": end, "texts_sha256": fingerprint(batch_texts),
                    "vectors_sha256": sha256(path), "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
                    "request_seconds": seconds, "attempts": attempts, "response_model": payload.get("model"),
                    "provider": payload.get("provider"), "usage": payload.get("usage"), "request_id": payload.get("id")}
            write_json(meta_path, meta)
        vectors[start:end] = batch
        return index, meta

    started = time.perf_counter()
    ordered = {}
    if batches:
        index, meta = one((0, batches[0]))
        ordered[index] = meta
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(one, item) for item in list(enumerate(batches))[1:]]
        try:
            for future in as_completed(futures):
                index, meta = future.result()
                ordered[index] = meta
                if len(ordered) % 10 == 0 or len(ordered) == len(batches):
                    print(f"Representation embeddings: {len(ordered)}/{len(batches)} batches ({time.perf_counter() - started:.1f}s)", flush=True)
        except BaseException:
            for future in futures:
                future.cancel()
            raise
    vectors.flush()
    del vectors
    metadata = [ordered[i] for i in range(len(batches))]
    manifest = {"recipe": recipe, "vectors_sha256": sha256(vector_path),
                "new_batches": len(batches), "new_strings": inputs["novel_strings"],
                "reused_strings": inputs["base_strings"], "prompt_tokens": sum((m.get("usage") or {}).get("prompt_tokens", 0) for m in metadata),
                "reported_cost_usd": sum(m["usage"]["cost"] for m in metadata) if all("cost" in (m.get("usage") or {}) for m in metadata) else None,
                "providers": sorted({str(m["provider"]) for m in metadata}),
                "response_models": sorted({m["response_model"] for m in metadata}),
                "attempts": sum(m["attempts"] for m in metadata),
                "batch_metadata_sha256": fingerprint(metadata), "last_session_seconds": time.perf_counter() - started,
                "captured_from_utc": min((m["fetched_at_utc"] for m in metadata), default=None),
                "captured_to_utc": max((m["fetched_at_utc"] for m in metadata), default=None)}
    write_json(manifest_path, manifest)
    write_json(OUTPUT / "embeddings.json", manifest)
    return manifest


def prediction_path(method, condition):
    path = ROOT / "rankings" / f"{method}-{condition}.json.gz"
    path.parent.mkdir(exist_ok=True)
    return path


def save_predictions(method, condition, recipe, rows, seconds):
    path = prediction_path(method, condition)
    with gzip.GzipFile(filename=str(path), mode="wb", mtime=0) as handle:
        handle.write(json.dumps(rows, separators=(",", ":")).encode())
    write_json(path.with_suffix(".meta.json"), {"recipe": recipe, "sha256": sha256(path), "rows": len(rows), "seconds": seconds})


def read_predictions(method, condition, recipe=None):
    path = prediction_path(method, condition)
    if not path.exists():
        return None
    metadata = json.loads(path.with_suffix(".meta.json").read_text())
    if (recipe is not None and metadata["recipe"] != recipe) or sha256(path) != metadata["sha256"]:
        raise ValueError(f"Prediction provenance failure: {method}/{condition}")
    with gzip.open(path, "rt") as handle:
        rows = json.load(handle)
    if len(rows) != metadata["rows"]:
        raise ValueError("Prediction count changed")
    return rows, metadata


def normalize(vectors):
    vectors = np.asarray(vectors, dtype=np.float64)
    for start in range(0, len(vectors), 4096):
        part = vectors[start:start + 4096]
        norms = np.linalg.norm(part, axis=1)
        if not np.isfinite(part).all() or np.any(norms == 0):
            raise ValueError("Non-finite or zero representation vector")
        part /= norms[:, None]
    return vectors


def semantic(benchmark, inputs, embeddings):
    positions = np.load(ROOT / "positions.npz", allow_pickle=False)
    matrix = np.load(ROOT / "embeddings/vectors.npy", mmap_mode="r", allow_pickle=False)
    ids = np.asarray([r["canonical_id"] for r in benchmark.catalogue])
    index = {int(t): i for i, t in enumerate(ids)}
    expected = [index[r["canonical_id"]] for r in benchmark.examples]
    conditions = [("raw", "raw", "raw"), ("template", "template", "template"), ("aliases", "aliases", "template")]
    for view in CONTEXT_VIEWS:
        conditions.extend([(view + "_candidate", view, "template"), (view + "_both", view, view)])
    conditions.extend([("cotags_one_work", "cotags", "one_work"), ("all_name_anchor", "all", "all")])
    cached_view, canonical = None, None
    for condition, cv, qv in conditions:
        recipe = {"protocol_sha256": fingerprint(PROTOCOL), "inputs_sha256": fingerprint(inputs),
                  "vectors_sha256": embeddings["vectors_sha256"], "condition": condition}
        if read_predictions("semantic", condition, recipe) is not None:
            continue
        if cv != cached_view or condition == "all_name_anchor":
            canonical = None
            canonical = normalize(matrix[positions["candidate_" + cv]])
            cached_view = cv
        queries = normalize(matrix[positions["query_" + qv]])
        if condition == "all_name_anchor":
            for start in range(0, len(canonical), 4096):
                raw = normalize(matrix[positions["candidate_raw"][start:start + 4096]])
                canonical[start:start + len(raw)] = normalize(0.75 * raw + 0.25 * canonical[start:start + len(raw)])
            queries = normalize(0.75 * normalize(matrix[positions["query_raw"]]) + 0.25 * queries)
        started = time.perf_counter()
        rows = []
        for start in range(0, len(queries), 32):
            scores = queries[start:start + 32] @ canonical.T
            rows.extend(rank_row(row, ids, expected[i]) for i, row in enumerate(scores, start))
        elapsed = time.perf_counter() - started
        save_predictions("semantic", condition, recipe, rows, elapsed)
        synonyms = [row for row, q in zip(rows, benchmark.examples) if q["example_kind"] == "synonym"]
        print(f"Semantic {condition}: R@1={sum(r['expected_rank'] <= 1 for r in synonyms) / len(synonyms):.4f} "
              f"R@10={sum(r['expected_rank'] <= 10 for r in synonyms) / len(synonyms):.4f} ({elapsed:.1f}s)", flush=True)


def lexical(benchmark):
    blocked = {r["source_id"] for r in benchmark.examples if r["example_kind"] == "synonym"}
    aliases = [r for r in benchmark.aliases if r["source_id"] not in blocked]
    selected_ids = {r["source_id"] for values in selected_aliases(benchmark).values() for r in values}
    ids = np.asarray([r["canonical_id"] for r in benchmark.catalogue])
    index = {int(t): i for i, t in enumerate(ids)}
    alias_targets = np.asarray([index[r["canonical_id"]] for r in aliases])
    selected = np.asarray([r["source_id"] in selected_ids for r in aliases])
    terms = [r["name"] for r in benchmark.catalogue] + [r["name"] for r in aliases]
    recipe = {"protocol_sha256": fingerprint(PROTOCOL), "benchmark": benchmark.provenance,
              "terms_sha256": fingerprint(terms), "selected_alias_ids_sha256": fingerprint(sorted(selected_ids))}
    conditions = PROTOCOL["lexical_conditions"]
    if all(read_predictions("lexical", c, recipe) is not None for c in conditions):
        return
    cache = ROOT / "lexical"
    cache.mkdir(exist_ok=True)
    paths = [cache / f"query-{i:05d}.json" for i in range(len(benchmark.examples))]
    missing = [i for i, path in enumerate(paths) if read_checkpoint(path, recipe) is None]
    if missing:
        with temporary_postgres() as connection:
            connection.execute("CREATE EXTENSION pg_trgm")
            connection.execute("CREATE TABLE term (id integer PRIMARY KEY, name text NOT NULL)")
            with connection.cursor().copy("COPY term (id, name) FROM STDIN") as copy:
                for i, name in enumerate(terms):
                    copy.write_row((i, name))
            connection.execute("ANALYZE term")
            write_json(OUTPUT / "postgres.json", {"server_version": connection.execute("SELECT version()").fetchone()[0],
                       "extension_version": connection.execute("SELECT extversion FROM pg_extension WHERE extname='pg_trgm'").fetchone()[0],
                       "locale": "C", "function": "similarity(query, candidate)", "exhaustive": True,
                       "representations": len(terms), "selected_aliases": len(selected_ids), "workers": 4})
            parameters = connection.info.get_parameters()
            completed, lock, started = len(paths) - len(missing), threading.Lock(), time.perf_counter()

            def partition(indexes):
                nonlocal completed
                with psycopg.connect(**parameters, autocommit=True) as worker:
                    for i in indexes:
                        query = benchmark.examples[i]
                        before = time.perf_counter()
                        with worker.cursor(binary=True) as cursor:
                            cursor.execute("SELECT similarity(%s, name) FROM term ORDER BY id", (query["incoming_tag"],))
                            scores = np.asarray(cursor.fetchall(), dtype=np.float32).reshape(-1)
                        if scores.shape != (len(terms),) or not np.isfinite(scores).all():
                            raise ValueError("Invalid lexical scores")
                        raw = scores[:len(ids)]
                        global_aliases, selected_aliases_scores = raw.copy(), raw.copy()
                        np.maximum.at(global_aliases, alias_targets, scores[len(ids):])
                        np.maximum.at(selected_aliases_scores, alias_targets[selected], scores[len(ids):][selected])
                        rows = {c: rank_row(s, ids, index[query["canonical_id"]]) for c, s in zip(conditions, (raw, global_aliases, selected_aliases_scores))}
                        save_checkpoint(paths[i], recipe, {"predictions": rows, "seconds": time.perf_counter() - before})
                        with lock:
                            completed += 1
                            if completed % 50 == 0 or completed == len(paths):
                                print(f"Frozen pg_trgm: {completed}/{len(paths)} ({time.perf_counter() - started:.1f}s)", flush=True)

            with ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(partition, [missing[i::4] for i in range(4)]))
    checkpoints = [read_checkpoint(path, recipe) for path in paths]
    for condition in conditions:
        save_predictions("lexical", condition, recipe, [r["predictions"][condition] for r in checkpoints],
                         sum(r["seconds"] for r in checkpoints))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("prepare", "embeddings", "semantic", "lexical", "report", "all"), default="all")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    benchmark = load_benchmark()
    freeze(OUTPUT / "protocol.json", {"protocol": PROTOCOL, "benchmark": benchmark.provenance})
    if args.phase in ("prepare", "embeddings", "semantic", "all"):
        texts, inputs = prepare(benchmark)
        if args.phase in ("embeddings", "semantic", "all"):
            embeddings = collect(texts, inputs, offline=args.offline)
            if args.phase in ("semantic", "all"):
                del texts
                semantic(benchmark, inputs, embeddings)
    if args.phase in ("lexical", "all"):
        lexical(benchmark)
    if args.phase in ("report", "all"):
        from analyze_ao3_representations import report
        report(benchmark)


if __name__ == "__main__":
    main()
