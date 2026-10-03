#!/usr/bin/env python3
"""Audit the local AO3 selective dump without network access or model calls.

Redacted is a missing-name marker, not a tag to embed. Merger resolution uses
numeric IDs; unmerged noncanonical tags remain unlabelled. Only aggregate
findings and numeric integrity examples are written to the report directory.
"""

import argparse
from array import array
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

import numpy as np


REDACTED = "Redacted"
UNMERGED, MISSING, CYCLE, CONFLICT, UNKNOWN = -1, -2, -3, -4, -9


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def save_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def resolve_mergers(ids, merger_ids, canonical):
    """Return terminal row indexes/status codes and path lengths for a sorted graph.

    Canonical nodes with outgoing merger edges are contradictory and quarantined.
    Remaining unresolved nodes after propagation reach a cycle (including feeders).
    """
    if np.any(ids[1:] <= ids[:-1]):
        raise ValueError("Tag IDs must be distinct and sorted before resolution")
    linked = np.flatnonzero(merger_ids != 0)
    next_rows = np.searchsorted(ids, merger_ids[linked])
    exists = next_rows < len(ids)
    exists[exists] &= ids[next_rows[exists]] == merger_ids[linked[exists]]
    terminal = np.full(len(ids), UNKNOWN, dtype=np.int32)
    depth = np.zeros(len(ids), dtype=np.int32)
    no_edge = merger_ids == 0
    terminal[no_edge & ~canonical] = UNMERGED
    terminals = np.flatnonzero(no_edge & canonical)
    terminal[terminals] = terminals
    terminal[canonical & ~no_edge] = CONFLICT
    terminal[linked[~exists & ~canonical[linked]]] = MISSING
    depth[linked[~exists]] = 1
    pending = linked[exists & ~canonical[linked]]
    targets = next_rows[exists & ~canonical[linked]]
    while len(pending):
        known = terminal[targets] != UNKNOWN
        if not known.any():
            terminal[pending] = CYCLE
            break
        terminal[pending[known]] = terminal[targets[known]]
        depth[pending[known]] = depth[targets[known]] + 1
        pending, targets = pending[~known], targets[~known]
    return terminal, depth, linked[~exists]


def audit_tags(path):
    columns = {k: array("q" if k in ("id", "merger_id", "count") else "B")
               for k in ("id", "merger_id", "count", "canonical", "visible", "type")}
    type_codes, names, name_hist = {}, {}, Counter()
    checks, name_features = Counter(), Counter()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        if header != ["id", "type", "name", "canonical", "cached_count", "merger_id"]:
            raise ValueError(f"Unexpected tag header: {header}")
        for row_number, row in enumerate(reader, 1):
            if len(row) != 6 or row[3] not in ("true", "false"):
                raise ValueError(f"Invalid tag row {row_number}")
            tag_id, kind, name, canonical_text, uses, merger = row
            tag_id, uses, merger = int(tag_id), int(uses), int(merger or 0)
            if tag_id <= 0 or merger < 0:
                raise ValueError(f"Invalid ID in tag row {row_number}")
            canonical = canonical_text == "true"
            visible = bool(name.strip()) and name != REDACTED
            checks["redacted_names"] += name == REDACTED
            checks["blank_names"] += not name.strip()
            checks["negative_cached_counts"] += uses < 0
            checks["visible_noncanonical_below_five_uses"] += visible and not canonical and uses < 5
            checks["visible_canonical_below_five_uses"] += visible and canonical and uses < 5
            checks["redacted_at_least_five_uses"] += name == REDACTED and uses >= 5
            checks["canonical_with_merger"] += canonical and merger != 0
            if visible:
                names[tag_id] = name
                name_hist[name] += 1
                name_features["non_ascii"] += not name.isascii()
                name_features["contains_slash"] += "/" in name
                name_features["contains_ampersand"] += "&" in name
                name_features["contains_whitespace"] += any(c.isspace() for c in name)
            code = type_codes.setdefault(kind, len(type_codes))
            for key, value in (("id", tag_id), ("count", uses), ("merger_id", merger),
                               ("canonical", canonical), ("visible", visible), ("type", code)):
                columns[key].append(value)
            if row_number % 1000000 == 0:
                print(f"Tags: {row_number:,} rows read", flush=True)
    data = {k: np.frombuffer(v, dtype=np.int64 if v.typecode == "q" else np.uint8)
            for k, v in columns.items()}
    data["canonical"] = data["canonical"].astype(bool)
    data["visible"] = data["visible"].astype(bool)
    if not np.all(data["id"][1:] > data["id"][:-1]):
        order = np.argsort(data["id"], kind="stable")
        data = {k: v[order] for k, v in data.items()}
    if np.any(data["id"][1:] == data["id"][:-1]):
        raise ValueError("Duplicate tag IDs: cannot use merger ground truth")
    ids, merger = data["id"], data["merger_id"]
    canonical, visible = data["canonical"], data["visible"]
    terminal, depth, missing = resolve_mergers(ids, merger, canonical)
    resolved = terminal >= 0
    target_visible = np.zeros(len(ids), dtype=bool)
    same_type = np.zeros(len(ids), dtype=bool)
    target_visible[resolved] = visible[terminal[resolved]]
    same_type[resolved] = data["type"][resolved] == data["type"][terminal[resolved]]
    named_merged = ~canonical & visible & (merger != 0)
    named_positive = named_merged & resolved & target_visible
    eligible = named_positive & same_type
    catalogue = canonical & visible & (merger == 0)
    data.update(terminal=terminal, eligible=eligible, catalogue=catalogue)
    target_counts = Counter(int(x) for x in terminal[eligible])
    # Proposed split diagnostics, not a frozen or sampled benchmark.
    buckets = np.full(len(ids), -1, dtype=np.int8)
    for row in np.flatnonzero(catalogue):
        tag_id = int(ids[row])
        bucket = int(hashlib.sha256(f"tag-matching-jev-ao3-v1:{tag_id}".encode()).hexdigest()[:16], 16) % 10
        buckets[row] = 0 if bucket < 6 else 1 if bucket < 8 else 2
    type_rows = []
    for kind, code in sorted(type_codes.items()):
        mask = data["type"] == code
        positives = mask & eligible
        represented = np.unique(terminal[positives])
        type_rows.append({
            "type": kind, "total_tags": int(mask.sum()), "visible_names": int((mask & visible).sum()),
            "canonical_tags": int((mask & canonical).sum()), "catalogue_candidates": int((mask & catalogue).sum()),
            "merged_tags_including_redacted": int((mask & (merger != 0)).sum()),
            "usable_synonyms": int(positives.sum()), "canonical_targets_with_synonyms": len(represented),
            "visible_unmerged_noncanonical": int((mask & visible & ~canonical & (merger == 0)).sum()),
            "cross_type_named_positive": int((mask & named_positive & ~same_type).sum()),
            "proposed_split": {
                split: {"synonyms": int((buckets[terminal[positives]] == i).sum()),
                        "synonym_target_groups": int((buckets[represented] == i).sum()),
                        "canonical_candidates": int((mask & catalogue & (buckets == i)).sum())}
                for i, split in enumerate(("development", "calibration", "test"))},
        })
    group_sizes = np.array(list(target_counts.values()), dtype=np.int64)
    cross_pairs = Counter((next(k for k, v in type_codes.items() if v == data["type"][i]),
                           next(k for k, v in type_codes.items() if v == data["type"][terminal[i]]))
                          for i in np.flatnonzero(named_positive & ~same_type))
    report = {
        "total_rows": len(ids), "unique_ids": len(ids), "min_id": int(ids.min()), "max_id": int(ids.max()),
        "visible_names": int(visible.sum()), "canonical_tags": int(canonical.sum()),
        "named_canonical_catalogue": int(catalogue.sum()), "merged_tags": int((merger != 0).sum()),
        "visible_noncanonical_with_merger": int(named_merged.sum()),
        "visible_aliases_to_visible_canonical": int(named_positive.sum()),
        "usable_same_type_synonyms": int(eligible.sum()), "distinct_usable_targets": len(target_counts),
        "visible_unmerged_noncanonical": int((visible & ~canonical & (merger == 0)).sum()),
        "redacted_noncanonical_with_merger": int((~visible & ~canonical & (merger != 0)).sum()),
        "merged_to_canonical_without_name": int((~canonical & (merger != 0) & resolved & ~target_visible).sum()),
        "named_merged_to_noncanonical_terminal": int((named_merged & (terminal == UNMERGED)).sum()),
        "all_merged_to_noncanonical_terminal": int(((merger != 0) & (terminal == UNMERGED)).sum()),
        "missing_direct_target_references": len(missing),
        "distinct_missing_merger_targets": len(np.unique(merger[missing])),
        "missing_direct_target_examples": [{"source_id": int(ids[i]), "target_id": int(merger[i])} for i in missing[:10]],
        "rows_reaching_missing_target": int((terminal == MISSING).sum()),
        "named_merged_reaching_missing_target": int((named_merged & (terminal == MISSING)).sum()),
        "rows_in_or_reaching_cycles": int((terminal == CYCLE).sum()),
        "rows_reaching_canonical_merger_conflict": int((terminal == CONFLICT).sum()),
        "named_merged_reaching_canonical_merger_conflict": int((named_merged & (terminal == CONFLICT)).sum()),
        "self_mergers": int((ids == merger).sum()),
        "resolved_merged_chains_over_one_hop": int((resolved & (depth > 1)).sum()),
        "max_resolved_merger_hops": int(depth[resolved].max()),
        "cross_type_named_positives": int((named_positive & ~same_type).sum()),
        "cross_type_pairs": [{"source_type": a, "target_type": b, "count": n} for (a, b), n in cross_pairs.items()],
        "checks": dict(checks), "visible_name_features": dict(name_features),
        "duplicate_visible_name_strings": sum(n > 1 for n in name_hist.values()),
        "extra_rows_with_duplicate_visible_name": sum(n - 1 for n in name_hist.values()),
        "synonyms_per_target": {"median": float(np.median(group_sizes)), "p95": float(np.quantile(group_sizes, 0.95)),
                                "max": int(group_sizes.max()), "singleton_targets": int((group_sizes == 1).sum()),
                                "top10_share": sum(n for _, n in target_counts.most_common(10)) / int(eligible.sum())},
        "proposed_split_seed": "tag-matching-jev-ao3-v1",
        "proposed_split_rule": "SHA-256(seed:canonical_id), first 64 bits modulo 10; 0-5 development, 6-7 calibration, 8-9 test",
        "by_type": type_rows,
    }
    # Compute diagnostics over visible labelled pairs without retaining redacted strings.
    equal = Counter()
    for i in np.flatnonzero(eligible):
        source, target = names[int(ids[i])], names[int(ids[terminal[i]])]
        equal["identical_raw_name"] += source == target
        equal["equal_after_casefold"] += source.casefold() == target.casefold()
        equal["equal_after_casefold_and_whitespace"] += " ".join(source.casefold().split()) == " ".join(target.casefold().split())
    report["positive_pair_name_diagnostics"] = dict(equal)
    return data, report


def audit_works(path, data):
    ids = data["id"]
    references = np.zeros(len(ids), dtype=np.int64)
    totals, languages, tag_counts, row_widths = Counter(), Counter(), Counter(), Counter()
    date_min, date_max = None, None
    refs, work_rows = array("q"), array("q")
    missing_ids = set()
    batch_rows = 0

    def flush():
        nonlocal refs, work_rows, batch_rows
        values = np.frombuffer(refs, dtype=np.int64)
        owners = np.frombuffer(work_rows, dtype=np.int64)
        indexes = np.searchsorted(ids, values)
        valid = indexes < len(ids)
        valid[valid] &= ids[indexes[valid]] == values[valid]
        totals["tag_references"] += len(values)
        totals["missing_tag_references"] += int((~valid).sum())
        missing_ids.update(int(x) for x in values[~valid])
        positions = indexes[valid]
        np.add.at(references, positions, 1)
        totals["redacted_or_blank_tag_references"] += int((~data["visible"][positions]).sum())
        totals["usable_synonym_tag_references"] += int(data["eligible"][positions].sum())
        totals["works_with_usable_synonym"] += len(np.unique(owners[valid][data["eligible"][positions]]))
        refs, work_rows, batch_rows = array("q"), array("q"), 0

    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        if header[:6] != ["creation date", "language", "restricted", "complete", "word_count", "tags"]:
            raise ValueError(f"Unexpected works header: {header}")
        for row_number, row in enumerate(reader, 1):
            row_widths[len(row)] += 1
            if len(row) < 6 or any(row[6:]) or row[2] not in ("true", "false") or row[3] not in ("true", "false"):
                raise ValueError(f"Invalid work row {row_number}")
            date, language, restricted, complete, words, tag_text = row[:6]
            if date:
                date_min = min(date_min, date) if date_min is not None else date
                date_max = max(date_max, date) if date_max is not None else date
            else:
                totals["missing_creation_dates"] += 1
            languages[language] += 1
            totals["restricted"] += restricted == "true"
            totals["complete"] += complete == "true"
            if words.strip():
                totals["negative_word_counts"] += int(words) < 0
            else:
                totals["missing_word_counts"] += 1
            tag_ids = [int(tag) for tag in tag_text.split("+")] if tag_text else []
            tag_counts[len(tag_ids)] += 1
            totals["works_with_duplicate_tag_ids"] += len(tag_ids) != len(set(tag_ids))
            refs.extend(tag_ids)
            work_rows.extend([batch_rows] * len(tag_ids))
            batch_rows += 1
            totals["rows"] += 1
            if batch_rows == 10000:
                flush()
            if row_number % 500000 == 0:
                print(f"Works: {row_number:,} rows audited", flush=True)
    if batch_rows:
        flush()
    return {
        **dict(totals), "header": header, "row_width_counts": dict(sorted(row_widths.items())),
        "creation_date_min": date_min, "creation_date_max": date_max,
        "languages": dict(languages.most_common()), "tag_count_distribution": dict(sorted(tag_counts.items())),
        "distinct_referenced_tag_ids": int((references > 0).sum()), "distinct_missing_tag_ids": len(missing_ids),
        "missing_tag_id_examples": sorted(missing_ids)[:10],
        "distinct_usable_synonyms_observed_in_works": int((references[data["eligible"]] > 0).sum()),
        "distinct_usable_synonyms_absent_from_works": int((references[data["eligible"]] == 0).sum()),
        "titles_summaries_body_text_or_work_ids_supplied": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/ao3-dump"))
    parser.add_argument("--output", type=Path, default=Path("results/ao3/audit"))
    parser.add_argument("--skip-works", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    tag_path, work_path = args.input / "tags-20210226.csv", args.input / "works-20210226.csv"
    started = time.perf_counter()
    data, tags = audit_tags(tag_path)
    report = {"dataset": "AO3 selective data dump", "snapshot_date_from_filenames": "2021-02-26",
              "audited_at_utc": datetime.now(timezone.utc).isoformat(),
              "source_files": {str(p): {"bytes": p.stat().st_size, "sha256": sha256(p)}
                               for p in (tag_path, work_path) if p.exists()},
              "tags": tags, "works": None,
              "policy": {"missing_name_markers": [REDACTED, "blank or whitespace-only"],
                         "positive_rule": "visible noncanonical source -> resolved visible canonical target of the same type",
                         "unmerged_noncanonical": "unlabelled; not automatic no-match ground truth",
                         "split_status": "diagnostic only; no benchmark has been sampled or evaluated"}}
    save_json(args.output / "analysis.json", report)
    print(json.dumps({k: v for k, v in tags.items() if k not in ("by_type",)}, indent=2), flush=True)
    for row in tags["by_type"]:
        print(json.dumps(row), flush=True)
    if not args.skip_works:
        report["works"] = audit_works(work_path, data)
    report["elapsed_seconds"] = time.perf_counter() - started
    report["analysis_script_sha256"] = sha256(Path(__file__))
    save_json(args.output / "analysis.json", report)
    with (args.output / "tag_types.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = [key for key in tags["by_type"][0] if key != "proposed_split"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: row[key] for key in fields} for row in tags["by_type"])
    print(f"AO3 audit complete: {args.output / 'analysis.json'} ({report['elapsed_seconds']:.1f}s)", flush=True)


if __name__ == "__main__":
    main()
