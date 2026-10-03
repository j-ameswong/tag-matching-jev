"""Frozen inputs shared by the Stage 1 runners. Evaluation is development-only."""

import csv
import hashlib
import json
from pathlib import Path


SPLIT = "development"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def read_csv(path):
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_benchmark(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    for filename, metadata in manifest["artifacts"].items():
        if digest(directory / filename) != metadata["sha256"]:
            raise ValueError(f"Snapshot checksum mismatch: {filename}")
    tags = read_csv(directory / "tags.csv")
    synonyms = read_csv(directory / "synonyms.csv")
    examples = read_csv(directory / "experiment.csv")
    names = sorted(row["name"] for row in tags if row["is_canonical_candidate"] == "true")
    if len(set(names)) != len(names):
        raise ValueError("Duplicate canonical names")
    name_set = set(names)
    mapping = {row["from_tag"]: row["canonical_tag"] for row in synonyms}
    if len(mapping) != len(synonyms) or name_set.intersection(mapping):
        raise ValueError("Duplicate aliases or aliases in the canonical catalogue")
    groups = {}
    ids = set()
    for row in examples:
        if row["snapshot_id"] != manifest["snapshot_id"] or row["example_id"] in ids:
            raise ValueError("Snapshot or example ID mismatch")
        ids.add(row["example_id"])
        target = row["canonical_tag"]
        if target not in name_set or row["label"] != "matchable":
            raise ValueError("Stage 1 requires positive labels with targets in the catalogue")
        if groups.setdefault(target, row["split"]) != row["split"]:
            raise ValueError("Canonical group crosses splits")
        if row["example_kind"] == "synonym":
            if mapping.get(row["incoming_tag"]) != target:
                raise ValueError("Synonym label disagrees with ground truth")
        elif row["example_kind"] != "canonical_name" or row["incoming_tag"] != target:
            raise ValueError("Invalid identity example")
    development = sorted((r for r in examples if r["split"] == SPLIT),
                         key=lambda r: (r["example_kind"], r["incoming_tag"]))
    if not development:
        raise ValueError("No development examples")
    aliases = sorted((r["incoming_tag"], r["canonical_tag"])
                     for r in development if r["example_kind"] == "synonym")
    provenance = {
        "snapshot_id": manifest["snapshot_id"], "manifest_sha256": digest(directory / "manifest.json"),
        "artifacts": manifest["artifacts"], "evaluation_split": SPLIT,
        "canonical_candidates": len(names), "development_examples": len(development),
        "development_synonyms": len(aliases),
        "development_synonym_groups": len({target for _, target in aliases}),
        "development_identities": len(development) - len(aliases),
        "calibration_and_test_evaluated": False,
    }
    return names, development, aliases, provenance
