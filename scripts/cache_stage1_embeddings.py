#!/usr/bin/env python3
"""Cache raw-name embeddings for the canonical catalogue and development queries.

Only public tag strings are sent to the endpoint in example_api/openai_embedding.py.
The key is read from OPENROUTER_API_KEY and sent to curl on stdin, never in argv.
Successful batches are reused; --offline requires a complete, validated cache.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import numpy as np

from stage1_data import digest, fingerprint, load_benchmark, write_json


CONFIG = {
    "endpoint": "https://openrouter.ai/api/v1/embeddings",
    "model": "openai/text-embedding-3-small", "dimensions": 1536,
    "encoding_format": "float", "representation": "raw_tag_name_v1",
    "batch_size": 128, "storage_dtype": "float64",
}


def parse_vectors(payload, count):
    if "error" in payload:
        raise ValueError("Embedding provider returned an error; no vectors cached")
    data = payload.get("data", [])
    indices = [item.get("index") for item in data]
    if len(data) != count or set(indices) != set(range(count)):
        raise ValueError("Missing or duplicate embedding response indices")
    ordered = sorted(data, key=lambda item: item["index"])
    vectors = np.asarray([item["embedding"] for item in ordered], dtype=np.float64)
    if vectors.shape != (count, CONFIG["dimensions"]) or not np.isfinite(vectors).all():
        raise ValueError("Invalid embedding dimensions or non-finite values")
    if np.any(np.linalg.norm(vectors, axis=1) == 0):
        raise ValueError("Zero-length embedding")
    return vectors


def request_batch(texts):
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key or any(c in key for c in "\r\n\0"):
        raise ValueError("Set OPENROUTER_API_KEY to a valid single-line key")
    body = {k: CONFIG[k] for k in ("model", "dimensions", "encoding_format")}
    body["input"] = texts
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as handle:
        json.dump(body, handle)
        handle.flush()
        command = ["curl", "--silent", "--show-error", "--fail-with-body",
                   "--connect-timeout", "10", "--max-time", "60",
                   "--header", "@-", "--data-binary", "@" + handle.name, CONFIG["endpoint"]]
        started = time.perf_counter()
        result = subprocess.run(command, input=(f"Authorization: Bearer {key}\n"
                                               "Content-Type: application/json\n").encode(),
                                capture_output=True, check=False)
        elapsed = time.perf_counter() - started
    if result.returncode:
        # Do not print the response body or request headers on authentication errors.
        raise RuntimeError(f"Embedding request failed (curl {result.returncode}): "
                           + result.stderr.decode(errors="replace").strip())
    return json.loads(result.stdout), elapsed


def cache_embeddings(dataset, cache, offline=False):
    names, examples, _, provenance = load_benchmark(dataset)
    texts = sorted(set(names) | {r["incoming_tag"] for r in examples})
    recipe = {"configuration": CONFIG, "provenance": provenance, "texts": texts}
    cache = Path(cache) / fingerprint(recipe)[:20]
    cache.mkdir(parents=True, exist_ok=True)
    write_json(cache / "inputs.json", recipe)
    matrices, batches = [], []
    for start in range(0, len(texts), CONFIG["batch_size"]):
        batch = texts[start:start + CONFIG["batch_size"]]
        stem = f"batch-{start // CONFIG['batch_size']:04d}"
        array_path, metadata_path = cache / (stem + ".npy"), cache / (stem + ".json")
        if metadata_path.exists() and array_path.exists():
            metadata = json.loads(metadata_path.read_text())
            if metadata["texts_sha256"] != fingerprint(batch) or metadata["vectors_sha256"] != digest(array_path):
                raise ValueError(f"Embedding cache integrity failure: {stem}")
            vectors = np.load(array_path, allow_pickle=False)
        else:
            if offline:
                raise ValueError(f"Missing embedding cache: {stem}; run without --offline first")
            payload, elapsed = request_batch(batch)
            vectors = parse_vectors(payload, len(batch))
            np.save(array_path, vectors, allow_pickle=False)
            metadata = {
                "texts_sha256": fingerprint(batch), "vectors_sha256": digest(array_path),
                "count": len(batch), "dimensions": CONFIG["dimensions"],
                "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
                "response_model": payload.get("model"), "provider": payload.get("provider"),
                "request_id": payload.get("id"), "usage": payload.get("usage"),
                "request_seconds": elapsed,
            }
            write_json(metadata_path, metadata)
            print(f"Embeddings: {start + len(batch)}/{len(texts)} strings cached "
                  f"({elapsed:.2f}s, usage={metadata['usage']})", flush=True)
        if vectors.shape != (len(batch), CONFIG["dimensions"]) or not np.isfinite(vectors).all():
            raise ValueError(f"Invalid cached vector array: {stem}")
        if np.any(np.linalg.norm(vectors, axis=1) == 0):
            raise ValueError(f"Zero-length cached embedding: {stem}")
        matrices.append(vectors)
        batches.append(metadata)
    matrix = np.concatenate(matrices)
    norms = np.linalg.norm(matrix, axis=1)
    summary = {
        "configuration": CONFIG, "cache_directory": str(cache), "inputs_sha256": digest(cache / "inputs.json"),
        "unique_strings": len(texts), "batches": len(batches),
        "response_models": sorted({str(b['response_model']) for b in batches}),
        "providers": sorted({str(b['provider']) for b in batches}),
        "prompt_tokens": sum((b.get("usage") or {}).get("prompt_tokens", 0) for b in batches),
        "reported_cost_usd": (sum(b["usage"]["cost"] for b in batches)
                              if all(isinstance(b.get("usage"), dict) and "cost" in b["usage"] for b in batches)
                              else None),
        "request_seconds_total": sum(b["request_seconds"] for b in batches),
        "raw_norm_min": float(norms.min()), "raw_norm_max": float(norms.max()),
        "captured_from_utc": min(b["fetched_at_utc"] for b in batches),
        "captured_to_utc": max(b["fetched_at_utc"] for b in batches),
        "normalization_for_search": "explicit L2 normalization in float64",
        "similarity": "cosine via exhaustive normalized dot product",
        "model_revision": "Provider returned an unversioned model alias; cached vectors freeze this run.",
    }
    write_json(cache / "manifest.json", summary)
    return texts, matrix / norms[:, None], summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("data/stackoverflow"))
    parser.add_argument("--cache", type=Path, default=Path("data/baselines/embeddings"))
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    try:
        _, _, summary = cache_embeddings(args.dataset, args.cache, args.offline)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Embedding collection failed: {error}\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
