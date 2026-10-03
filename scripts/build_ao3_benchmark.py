#!/usr/bin/env python3
"""Freeze the named AO3 catalogue, positive pool, and Freeform Stage 1 sample."""

import argparse
from collections import Counter
import csv
import json
from pathlib import Path

import numpy as np

from analyze_ao3_dataset import audit_tags, sha256
from ao3_benchmark import (DEFAULT_DATASET, SAMPLE_SEED, SPLIT_SEED, SPLITS,
                           hash_order, load_benchmark, sample_synonyms, split_for, write_csv, write_json)


def build(source, output, report_directory, size=2000, cap=3, identities=250):
    if (output / "manifest.json").exists():
        benchmark = load_benchmark(output)
        if benchmark.manifest["sampling"] != sampling_policy(size, cap, identities):
            raise ValueError("Existing frozen benchmark has a different sampling policy; choose a new --output")
        for name, meta in benchmark.manifest["source_files"].items():
            if sha256(source / name) != meta["sha256"]:
                raise ValueError(f"Source checksum changed: {name}")
        report_directory.mkdir(parents=True, exist_ok=True)
        write_json(report_directory / "manifest.json", benchmark.manifest)
        print("Existing frozen benchmark validated", flush=True)
        return benchmark.manifest
    if identities < 1:
        raise ValueError("Identity sample size must be positive")
    source_metadata = {name: {"sha256": sha256(source / name), "bytes": (source / name).stat().st_size}
                       for name in ("tags-20210226.csv", "works-20210226.csv")}
    snapshot = "ao3-20210226-" + source_metadata["tags-20210226.csv"]["sha256"][:12]
    data, audit = audit_tags(source / "tags-20210226.csv")
    wanted = set(int(x) for x in data["id"][data["catalogue"] | data["eligible"]])
    raw = {}
    with (source / "tags-20210226.csv").open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        next(reader)
        for tag_id, kind, name, canonical, count, merger in reader:
            tag_id = int(tag_id)
            if tag_id in wanted:
                raw[tag_id] = (kind, name, canonical, int(count), merger)
    catalogue, synonyms = [], []
    for position in np.flatnonzero(data["catalogue"]):
        tag_id = int(data["id"][position])
        kind, name, canonical, count, merger = raw[tag_id]
        catalogue.append(dict(canonical_id=tag_id, type=kind, name=name, canonical=canonical,
                              cached_count=count, merger_id=merger, split=split_for(tag_id)))
    for position in np.flatnonzero(data["eligible"]):
        source_id = int(data["id"][position])
        target_id = int(data["id"][data["terminal"][position]])
        kind, name, canonical, count, merger = raw[source_id]
        hops, current = 0, int(position)
        while int(data["id"][current]) != target_id:
            current = int(np.searchsorted(data["id"], data["merger_id"][current]))
            hops += 1
        synonyms.append(dict(source_id=source_id, type=kind, name=name, canonical=canonical,
                             cached_count=count, merger_id=int(merger), canonical_id=target_id,
                             canonical_name=raw[target_id][1], resolution_hops=hops, split=split_for(target_id)))
    del data, raw, wanted
    freeform = [r for r in synonyms if r["type"] == "Freeform" and r["split"] == "development"]
    selected = sample_synonyms(freeform, size, cap)
    controls = sorted((r for r in catalogue if r["type"] == "Freeform" and r["split"] == "development"),
                      key=lambda r: (hash_order(SAMPLE_SEED + ":identity", r["canonical_id"]), r["canonical_id"]))[:identities]
    if len(controls) != identities:
        raise ValueError("Insufficient development identity controls")
    examples = []
    for kind, rows in (("synonym", selected), ("canonical_name", controls)):
        for row in rows:
            source_id = row.get("source_id", row["canonical_id"])
            examples.append(dict(example_id=f"{snapshot}:{kind}:{source_id}", snapshot_id=snapshot,
                                 source_id=source_id, type="Freeform", incoming_tag=row["name"],
                                 canonical_id=row["canonical_id"], canonical_name=row.get("canonical_name", row["name"]),
                                 example_kind=kind, split="development", label="matchable", cached_count=row["cached_count"]))
    output.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "catalogue.csv": write_csv(output / "catalogue.csv", list(catalogue[0]), catalogue),
        "synonyms.csv": write_csv(output / "synonyms.csv", list(synonyms[0]), synonyms),
        "development.csv": write_csv(output / "development.csv", list(examples[0]),
                                     sorted(examples, key=lambda r: (r["example_kind"], r["source_id"]))),
    }
    group_sizes = Counter(r["canonical_id"] for r in selected)
    manifest = {
        "format": "ao3-tag-benchmark-v1", "snapshot_id": snapshot, "source_file_date": "2021-02-26",
        "source_directory": str(source), "source_files": source_metadata,
        "export_sources_sha256": {name: sha256(Path(__file__).parent / name) for name in
                                  ("build_ao3_benchmark.py", "ao3_benchmark.py", "analyze_ao3_dataset.py")},
        "catalogue_policy": "All visible canonical=true rows without outgoing merger; all types exported; no count threshold",
        "positive_policy": "Visible noncanonical source; resolve merger to visible canonical with no conflict; same source/target type",
        "excluded_named_merged": {"missing_target": audit["named_merged_reaching_missing_target"],
                                 "canonical_merger_conflict": audit["named_merged_reaching_canonical_merger_conflict"],
                                 "unmerged_terminal": audit["named_merged_to_noncanonical_terminal"],
                                 "cross_type": audit["cross_type_named_positives"]},
        "suppression": "Exclude literal Redacted and blank names; retain original names and approximate counts",
        "unlabelled_visible_unmerged": audit["visible_unmerged_noncanonical"],
        "split": {"seed": SPLIT_SEED, "unit": "resolved canonical ID",
                  "rule": "SHA256(seed:canonical_id), first 64 bits modulo 10: 0-5 development, 6-7 calibration, 8-9 test"},
        "by_type": audit["by_type"], "sampling": sampling_policy(size, cap, identities),
        "sample_counts": {"synonyms": len(selected), "identities": len(controls),
                          "synonym_target_groups": len(group_sizes),
                          "aliases_per_sampled_target": dict(sorted(Counter(group_sizes.values()).items())),
                          "freeform_candidates": sum(r["type"] == "Freeform" for r in catalogue),
                          "permitted_development_aliases": len(freeform)},
        "leakage_policy": {"query_text": "raw source name only; observed type selects catalogue",
                           "secondary_alias_pool": "all same-type development synonyms, exclude the evaluated source ID per query",
                           "holdout_aliases": "calibration and test aliases never indexed, embedded, or scored",
                           "canonical_names": "all eligible same-type canonicals remain candidates across every split",
                           "works_context": False},
        "artifacts": artifacts,
    }
    write_json(output / "manifest.json", manifest)
    load_benchmark(output)
    report_directory.mkdir(parents=True, exist_ok=True)
    write_json(report_directory / "manifest.json", manifest)
    return manifest


def sampling_policy(size, cap, identities):
    return {"type": "Freeform", "split": "development", "seed": SAMPLE_SEED,
            "synonyms": size, "per_target_cap": cap, "identities": identities,
            "synonym_rule": "Sort by full SHA256(seed:source_id), numeric ID tie break; greedily take aliases until size, skipping targets at cap",
            "identity_rule": "Sort development canonicals by SHA256(seed:identity:canonical_id), numeric ID tie break; take identities count",
            "bias": "Visible-name positives only; unweighted hash sample with cap downweights large alias families; not a usage-weighted or full-population estimate"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/ao3-dump"))
    parser.add_argument("--output", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, default=Path("results/ao3/benchmark"))
    parser.add_argument("--sample-size", type=int, default=2000)
    parser.add_argument("--per-target-cap", type=int, default=3)
    parser.add_argument("--identities", type=int, default=250)
    args = parser.parse_args()
    manifest = build(args.input, args.output, args.report, args.sample_size, args.per_target_cap, args.identities)
    print(json.dumps(manifest["sample_counts"], indent=2), flush=True)


if __name__ == "__main__":
    main()
