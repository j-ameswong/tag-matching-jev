"""AO3 snapshot adapter. Labels stay separate from raw retrieval strings."""

from collections import Counter
import csv
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from analyze_ao3_dataset import sha256
from stage1_data import fingerprint, write_json


SPLIT_SEED = "tag-matching-jev-ao3-v1"
SAMPLE_SEED = "tag-matching-jev-ao3-freeform-stage1-v1"
SPLITS = ("development", "calibration", "test")
DEFAULT_DATASET = Path("data/ao3-benchmark")
DEFAULT_CACHE = Path("data/ao3-baselines")
DEFAULT_OUTPUT = Path("results/ao3/stage1")


def hash_order(seed, tag_id):
    return hashlib.sha256(f"{seed}:{tag_id}".encode()).hexdigest()


def split_for(canonical_id):
    bucket = int(hash_order(SPLIT_SEED, canonical_id)[:16], 16) % 10
    return SPLITS[0 if bucket < 6 else 1 if bucket < 8 else 2]


def sample_synonyms(rows, size=2000, cap=3):
    """Hash-priority sampling, skipping groups that already reached the cap."""
    if size < 1 or cap < 1:
        raise ValueError("Sample size and per-target cap must be positive")
    counts, selected = Counter(), []
    for row in sorted(rows, key=lambda r: (hash_order(SAMPLE_SEED, r["source_id"]), r["source_id"])):
        if counts[row["canonical_id"]] < cap:
            selected.append(row)
            counts[row["canonical_id"]] += 1
            if len(selected) == size:
                return selected
    raise ValueError(f"Only {len(selected)} eligible rows under the sampling cap, requested {size}")


def csv_rows(path):
    with Path(path).open(encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle)


def write_csv(path, fields, rows):
    temporary = Path(path).with_suffix(".csv.tmp")
    count = 0
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            count += 1
    temporary.replace(path)
    return {"rows": count, "sha256": sha256(Path(path)), "bytes": Path(path).stat().st_size}


@dataclass
class Benchmark:
    catalogue: list
    examples: list
    aliases: list
    manifest: dict
    provenance: dict


def load_benchmark(directory=DEFAULT_DATASET):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("format") != "ao3-tag-benchmark-v1":
        raise ValueError("Expected an AO3 benchmark manifest")
    for filename, metadata in manifest["artifacts"].items():
        if sha256(directory / filename) != metadata["sha256"]:
            raise ValueError(f"AO3 snapshot checksum mismatch: {filename}")
    kind = manifest["sampling"]["type"]
    catalogue = []
    for row in csv_rows(directory / "catalogue.csv"):
        if row["type"] != kind:
            continue
        row["canonical_id"], row["cached_count"] = int(row["canonical_id"]), int(row["cached_count"])
        if (row["canonical"] != "true" or row["merger_id"] or not row["name"].strip()
                or row["name"] == "Redacted" or row["split"] != split_for(row["canonical_id"])):
            raise ValueError("Invalid canonical candidate")
        catalogue.append(row)
    catalogue.sort(key=lambda r: r["canonical_id"])
    candidates = {r["canonical_id"]: r for r in catalogue}
    if len(candidates) != len(catalogue):
        raise ValueError("Duplicate canonical IDs")
    aliases, all_alias_ids, splits = [], set(), Counter()
    for row in csv_rows(directory / "synonyms.csv"):
        if row["type"] != kind:
            continue
        for key in ("source_id", "canonical_id", "merger_id", "resolution_hops", "cached_count"):
            row[key] = int(row[key])
        target = candidates.get(row["canonical_id"])
        if (row["source_id"] in all_alias_ids or row["source_id"] in candidates or not target
                or row["canonical"] != "false" or row["name"] == "Redacted" or not row["name"].strip()
                or row["canonical_name"] != target["name"] or row["split"] != target["split"]
                or row["merger_id"] <= 0 or row["resolution_hops"] < 1):
            raise ValueError("Invalid synonym or canonical group crosses splits")
        all_alias_ids.add(row["source_id"])
        splits[row["split"]] += 1
        if row["split"] == "development":
            aliases.append(row)
    aliases.sort(key=lambda r: r["source_id"])
    alias_lookup = {r["source_id"]: r for r in aliases}
    examples, seen = [], set()
    for row in csv_rows(directory / "development.csv"):
        for key in ("source_id", "canonical_id", "cached_count"):
            row[key] = int(row[key])
        target = candidates.get(row["canonical_id"])
        if (row["snapshot_id"] != manifest["snapshot_id"] or row["example_id"] in seen
                or row["type"] != kind or row["split"] != "development" or row["label"] != "matchable"
                or not target or target["split"] != "development" or row["canonical_name"] != target["name"]):
            raise ValueError("Invalid development example")
        if row["example_kind"] == "synonym":
            alias = alias_lookup.get(row["source_id"])
            if (not alias or alias["canonical_id"] != row["canonical_id"]
                    or alias["name"] != row["incoming_tag"] or alias["cached_count"] != row["cached_count"]):
                raise ValueError("Query disagrees with the source synonym")
        elif (row["example_kind"] != "canonical_name" or row["source_id"] != row["canonical_id"]
              or row["incoming_tag"] != target["name"] or row["cached_count"] != target["cached_count"]):
            raise ValueError("Invalid identity control")
        seen.add(row["example_id"])
        examples.append(row)
    chosen = sample_synonyms(aliases, manifest["sampling"]["synonyms"], manifest["sampling"]["per_target_cap"])
    actual = [r["source_id"] for r in examples if r["example_kind"] == "synonym"]
    if sorted(actual) != sorted(r["source_id"] for r in chosen):
        raise ValueError("Development examples do not match the frozen sampling rule")
    identity_ids = sorted((r for r in catalogue if r["split"] == "development"),
                          key=lambda r: (hash_order(SAMPLE_SEED + ":identity", r["canonical_id"]), r["canonical_id"]))
    expected_ids = {r["canonical_id"] for r in identity_ids[:manifest["sampling"]["identities"]]}
    if {r["canonical_id"] for r in examples if r["example_kind"] == "canonical_name"} != expected_ids:
        raise ValueError("Identity controls do not match the frozen sampling rule")
    if len(examples) != manifest["sampling"]["synonyms"] + manifest["sampling"]["identities"]:
        raise ValueError("Wrong number of development examples")
    examples.sort(key=lambda r: (r["example_kind"], r["source_id"]))
    provenance = {
        "snapshot_id": manifest["snapshot_id"], "manifest_sha256": sha256(directory / "manifest.json"),
        "artifacts": manifest["artifacts"], "type": kind, "evaluation_split": "development",
        "canonical_candidates": len(catalogue), "development_synonyms": len(actual),
        "development_synonym_groups": len({r["canonical_id"] for r in chosen}),
        "development_identities": len(expected_ids), "permitted_development_aliases": len(aliases),
        "population_synonyms_by_split": dict(splits), "calibration_and_test_evaluated": False,
    }
    return Benchmark(catalogue, examples, aliases, manifest, provenance)
