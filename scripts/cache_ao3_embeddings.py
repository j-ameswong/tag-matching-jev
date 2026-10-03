#!/usr/bin/env python3
"""Resumable AO3 embeddings: full Freeform catalogue plus sampled dev strings.

Only raw public tag names go to the API. Base64 preserves native float32 values;
normalization and exhaustive cosine ranking use float64. Holdout aliases are not
embedded. Credentials are supplied to curl over stdin and never logged.
"""

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import numpy as np

from ao3_benchmark import DEFAULT_CACHE, DEFAULT_DATASET, fingerprint, load_benchmark, sha256, write_json


CONFIG = {
    "endpoint": "https://openrouter.ai/api/v1/embeddings",
    "model": "openai/text-embedding-3-small", "dimensions": 1536,
    "encoding_format": "base64", "representation": "ao3_raw_tag_name_v1",
    "batch_size": 1024, "storage_dtype": "float32", "search_dtype": "float64",
}


def parse_vectors(payload, count, dimensions=1536):
    data = payload.get("data", [])
    if ("error" in payload or len(data) != count
            or {item.get("index") for item in data} != set(range(count))):
        raise ValueError("Embedding response has missing/duplicate indices or a provider error")
    vectors = np.empty((count, dimensions), dtype=np.float32)
    for item in data:
        if not isinstance(item.get("embedding"), str):
            raise ValueError("Expected the requested base64 embedding representation")
        vector = np.frombuffer(base64.b64decode(item["embedding"], validate=True), dtype="<f4")
        if vector.shape != (dimensions,):
            raise ValueError("Wrong embedding dimensions")
        vectors[item["index"]] = vector
    validate_vectors(vectors, count, dimensions)
    return vectors


def validate_vectors(vectors, count, dimensions=1536):
    if vectors.dtype != np.float32 or vectors.shape != (count, dimensions) or not np.isfinite(vectors).all():
        raise ValueError("Invalid cached embedding shape, dtype or values")
    if np.any(np.linalg.norm(vectors, axis=1) == 0):
        raise ValueError("Zero-length embedding")


def request_batch(texts):
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key or any(c in key for c in "\r\n\0"):
        raise ValueError("Set OPENROUTER_API_KEY to a valid single-line key")
    # Byte counts upper-bound tokens conservatively for these short plain strings.
    lengths = [len(text.encode("utf-8")) for text in texts]
    if not texts or len(texts) > 2048 or min(lengths) < 1 or max(lengths) > 8192 or sum(lengths) > 300000:
        raise ValueError("Batch exceeds conservative embedding input limits")
    body = {key: CONFIG[key] for key in ("model", "dimensions", "encoding_format")}
    body["input"] = texts
    started = time.perf_counter()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as handle:
        json.dump(body, handle)
        handle.flush()
        command = ["curl", "--silent", "--show-error", "--connect-timeout", "10", "--max-time", "120",
                   "--header", "@-", "--data-binary", "@" + handle.name,
                   "--write-out", "\n%{http_code}", CONFIG["endpoint"]]
        for attempt in range(4):
            response = subprocess.run(command, input=(f"Authorization: Bearer {key}\n"
                                                      "Content-Type: application/json\n").encode(),
                                      capture_output=True, check=False)
            raw, _, status = response.stdout.rpartition(b"\n")
            status = int(status) if status.isdigit() else 0
            if response.returncode == 0 and status == 200:
                return json.loads(raw), time.perf_counter() - started, attempt + 1
            transient = response.returncode in (7, 18, 28, 52, 56) or status in (408, 429) or status >= 500
            if not transient or attempt == 3:
                # Do not expose headers, credentials, request text or provider error bodies.
                raise RuntimeError(f"Embedding request failed: curl={response.returncode}, HTTP={status}, attempts={attempt + 1}")
            time.sleep(2 ** (attempt + 1))
    raise AssertionError("Unreachable")


def collect(benchmark, cache_root=DEFAULT_CACHE / "embeddings", offline=False, workers=4):
    texts = sorted({r["name"] for r in benchmark.catalogue} | {r["incoming_tag"] for r in benchmark.examples})
    recipe = {"configuration": CONFIG, "benchmark": benchmark.provenance, "texts": texts}
    cache = Path(cache_root) / fingerprint(recipe)[:20]
    cache.mkdir(parents=True, exist_ok=True)
    inputs = cache / "inputs.json"
    if inputs.exists() and json.loads(inputs.read_text()) != recipe:
        raise ValueError("Embedding recipe mismatch")
    if not inputs.exists():
        write_json(inputs, recipe)
    starts = list(range(0, len(texts), CONFIG["batch_size"]))

    def one(start):
        batch = texts[start:start + CONFIG["batch_size"]]
        stem = f"batch-{start // CONFIG['batch_size']:04d}"
        array_path, meta_path = cache / (stem + ".npy"), cache / (stem + ".json")
        if array_path.exists() and meta_path.exists():
            metadata = json.loads(meta_path.read_text())
            if (metadata["texts_sha256"] != fingerprint(batch)
                    or metadata["vectors_sha256"] != sha256(array_path)):
                raise ValueError(f"Embedding cache checksum failure: {stem}")
            vectors = np.load(array_path, allow_pickle=False)
            validate_vectors(vectors, len(batch))
        else:
            if offline:
                raise ValueError(f"Missing {stem}; collect embeddings before an offline run")
            payload, seconds, attempts = request_batch(batch)
            vectors = parse_vectors(payload, len(batch), CONFIG["dimensions"])
            temporary = array_path.with_suffix(".npy.tmp")
            with temporary.open("wb") as handle:
                np.save(handle, vectors, allow_pickle=False)
            temporary.replace(array_path)
            metadata = {
                "texts_sha256": fingerprint(batch), "vectors_sha256": sha256(array_path),
                "count": len(batch), "dimensions": CONFIG["dimensions"],
                "fetched_at_utc": datetime.now(timezone.utc).isoformat(), "request_seconds": seconds,
                "attempts": attempts, "response_model": payload.get("model"),
                "provider": payload.get("provider"), "request_id": payload.get("id"), "usage": payload.get("usage"),
            }
            write_json(meta_path, metadata)
        return start, metadata

    started = time.perf_counter()
    # Validate the first response before scheduling the full collection.
    first, metadata = one(starts[0])
    batches = {first: metadata}
    print(f"Embeddings: {metadata['count']:,}/{len(texts):,} strings ready", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(one, start) for start in starts[1:]]
        try:
            for future in as_completed(futures):
                start, metadata = future.result()
                batches[start] = metadata
                if len(batches) % 5 == 0 or len(batches) == len(starts):
                    done = sum(b["count"] for b in batches.values())
                    print(f"Embeddings: {done:,}/{len(texts):,} strings ready ({time.perf_counter() - started:.1f}s)", flush=True)
        except BaseException:
            for future in futures:
                future.cancel()
            raise
    ordered = [batches[start] for start in starts]
    summary = {
        "configuration": CONFIG, "cache_directory": str(cache), "inputs_sha256": sha256(inputs),
        "unique_strings": len(texts), "batches": len(ordered), "request_workers": workers,
        "response_models": sorted({str(b["response_model"]) for b in ordered}),
        "providers": sorted({str(b["provider"]) for b in ordered}),
        "prompt_tokens": sum((b.get("usage") or {}).get("prompt_tokens", 0) for b in ordered),
        "reported_cost_usd": (sum(b["usage"]["cost"] for b in ordered)
                              if all(isinstance(b.get("usage"), dict) and "cost" in b["usage"] for b in ordered) else None),
        "request_seconds_total": sum(b["request_seconds"] for b in ordered),
        "request_attempts": sum(b["attempts"] for b in ordered),
        "captured_from_utc": min(b["fetched_at_utc"] for b in ordered),
        "captured_to_utc": max(b["fetched_at_utc"] for b in ordered),
        "batch_metadata_sha256": fingerprint(ordered),
        "normalization": "L2 normalization and exhaustive dot products in float64",
        "model_revision": "Provider returned an unversioned model alias; checksummed vectors freeze this run",
        "calibration_and_test_aliases_embedded": False,
    }
    manifest_path = cache / "manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text()) != summary:
        # Worker count affects scheduling only; preserve acquisition metadata on offline replay.
        previous = json.loads(manifest_path.read_text())
        summary["request_workers"] = previous["request_workers"]
        if previous != summary:
            raise ValueError("Embedding collection manifest mismatch")
    else:
        write_json(manifest_path, summary)
    return texts, cache, summary


def load_normalized(texts, cache):
    vectors = np.empty((len(texts), CONFIG["dimensions"]), dtype=np.float64)
    for start in range(0, len(texts), CONFIG["batch_size"]):
        raw = np.load(cache / f"batch-{start // CONFIG['batch_size']:04d}.npy", allow_pickle=False).astype(np.float64)
        vectors[start:start + len(raw)] = raw / np.linalg.norm(raw, axis=1)[:, None]
    return vectors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE / "embeddings")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    _, _, summary = collect(load_benchmark(args.dataset), args.cache, args.offline, args.workers)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
