#!/usr/bin/env python3
"""Fetch a Stack Exchange sample or all tags and build experiment CSVs.

Uses Python's standard library and curl. Cached pages are reused, so rerunning
also resumes an interrupted download without repeating successful requests.
Fetching beyond page 25 requires STACKEXCHANGE_API_KEY or --key-file.
"""

import argparse
import collections
import csv
import hashlib
import json
import itertools
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlencode


API = "https://api.stackexchange.com/2.3"
SITE = "stackoverflow"
PAGE_SIZE = 100
SPLIT_SEED = "tag-matching-jev-v1"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def validate_response(payload):
    if not isinstance(payload, dict):
        raise ValueError("API response must be an object")
    if "error_id" in payload:
        raise ValueError(f"API error {payload['error_id']}: {payload.get('error_name', 'unknown')}")
    if not isinstance(payload.get("items"), list) or not isinstance(payload.get("has_more"), bool):
        raise ValueError("API response is missing items or has_more")
    for key in ("quota_remaining", "backoff"):
        if key in payload and (type(payload[key]) is not int or payload[key] < 0):
            raise ValueError(f"Invalid {key}")


def fetch_pages(output, kind, limit, offline, api_key=None, request_state=None):
    endpoint, sort = ("tags", "popular") if kind == "tags" else ("tags/synonyms", "applied")
    records, pages = [], []
    backoff_until = 0.0
    if request_state is None:
        request_state = {"quota_remaining": None}
    for page in itertools.count(1):
        if limit is not None and len(records) >= limit:
            break
        source_url = f"{API}/{endpoint}?" + urlencode({
            "site": SITE, "pagesize": PAGE_SIZE, "page": page, "sort": sort, "order": "desc",
        })
        path = output / "raw" / f"{kind}-page-{page:03d}.json"
        if path.exists():
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("source_url") != source_url:
                raise ValueError(f"Cache URL mismatch: {path}")
        else:
            if offline:
                raise ValueError(f"Missing cached page: {path}")
            if request_state["quota_remaining"] == 0:
                raise ValueError("API quota exhausted; cached pages are preserved")
            if page > 25 and not api_key:
                raise ValueError(f"Page {page} requires a Stack Exchange app key; cached pages are preserved")
            wait = max(0.0, backoff_until - time.time())
            if wait:
                print(f"API backoff: waiting {wait:.1f}s", flush=True)
                time.sleep(wait)
            command = [
                "curl", "--fail-with-body", "--silent", "--show-error", "--compressed",
                "--connect-timeout", "10", "--max-time", "45", "--user-agent",
                "tag-matching-jev/0.1 (public tag experiment)", source_url,
            ]
            # Read the header from stdin: the key never appears in argv or cached URLs.
            header_input = None
            if api_key:
                command.extend(["--header", "@-"])
                header_input = f"Authorization: Bearer {api_key}\n".encode("utf-8")
            result = subprocess.run(command, input=header_input, capture_output=True, check=False)
            if result.returncode:
                try:
                    validate_response(json.loads(result.stdout))
                except json.JSONDecodeError:
                    pass
                raise ValueError(f"Download failed for {kind} page {page}: "
                                 + result.stderr.decode("utf-8", errors="replace").strip())
            payload = json.loads(result.stdout)
            validate_response(payload)
            cached = {"source_url": source_url,
                      "fetched_at_utc": datetime.now(timezone.utc).isoformat(), "response": payload}
            if api_key:
                cached["authentication"] = "app_key"
            write_json(path, cached)
            request_state["quota_remaining"] = payload.get("quota_remaining")
            # Requests are sequential and deliberately paced below the IP throttle.
            time.sleep(0.4)
        payload = cached["response"]
        validate_response(payload)
        fetched_at = datetime.fromisoformat(cached["fetched_at_utc"]).timestamp()
        backoff_until = max(backoff_until, fetched_at + payload.get("backoff", 0))
        records.extend((item, cached) for item in payload["items"])
        pages.append({"path": str(path.relative_to(output)), "source_url": source_url,
                      "fetched_at_utc": cached["fetched_at_utc"], "items": len(payload["items"]),
                      "has_more": payload["has_more"], "quota_remaining": payload.get("quota_remaining"),
                      "backoff": payload.get("backoff", 0), "sha256": sha256(path.read_bytes())})
        if "authentication" in cached:
            pages[-1]["authentication"] = cached["authentication"]
        print(f"{kind}: page {page}, {len(records)} records collected", flush=True)
        if not payload["has_more"]:
            break
        if not payload["items"]:
            raise ValueError("Empty page with has_more=true")
    return (records if limit is None else records[:limit]), pages


def unique_records(records, field, required):
    result = {}
    for item, source in records:
        if not isinstance(item, dict) or any(key not in item for key in required):
            raise ValueError(f"Missing required fields: {required}")
        key = item[field]
        if not isinstance(key, str) or not key:
            raise ValueError(f"Invalid {field}")
        if key in result:
            if field == "from_tag" and result[key][0]["to_tag"] != item["to_tag"]:
                raise ValueError(f"Conflicting synonym targets for {key}")
        else:
            result[key] = (item, source)
    return result


def group_split(canonical):
    bucket = int(sha256(f"{SPLIT_SEED}:{SITE}:{canonical}".encode())[:16], 16) % 10
    return "development" if bucket < 6 else "calibration" if bucket < 8 else "test"


def write_csv(path, rows):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: str(value).lower() if isinstance(value, bool) else value
                             for key, value in row.items()})
    temporary.replace(path)


def build(output, tag_records, synonym_records, tag_pages, synonym_pages, limits):
    tags = unique_records(tag_records, "name", ("name", "count", "has_synonyms", "is_required", "is_moderator_only"))
    synonyms = unique_records(synonym_records, "from_tag", ("from_tag", "to_tag", "applied_count", "creation_date"))
    for item, _ in tags.values():
        if type(item["count"]) is not int or item["count"] < 0:
            raise ValueError("Invalid tag count")
        if any(type(item[key]) is not bool for key in ("has_synonyms", "is_required", "is_moderator_only")):
            raise ValueError("Invalid tag flags")
    for item, _ in synonyms.values():
        if not isinstance(item["to_tag"], str) or not item["to_tag"]:
            raise ValueError("Invalid synonym target")
        for key in ("applied_count", "creation_date", "last_applied_date"):
            if key in item and (type(item[key]) is not int or item[key] < 0):
                raise ValueError(f"Invalid synonym {key}")

    def resolve(name):
        seen = set()
        while name in synonyms:
            if name in seen:
                raise ValueError(f"Synonym cycle involving {name}")
            seen.add(name)
            name = synonyms[name][0]["to_tag"]
        return name

    candidates = {name for name in tags if resolve(name) == name}
    pages = tag_pages + synonym_pages
    snapshot_id = "stackoverflow-" + sha256(json.dumps({
        "limits": limits, "pages": [page["sha256"] for page in pages],
    }, sort_keys=True).encode())[:16]
    tag_rows = []
    for name, (item, source) in sorted(tags.items()):
        tag_rows.append({"snapshot_id": snapshot_id, "site": SITE, "name": name,
                         "question_count": item["count"], "has_synonyms": item["has_synonyms"],
                         "is_moderator_only": item["is_moderator_only"], "is_required": item["is_required"],
                         "canonical_tag": resolve(name), "is_canonical_candidate": name in candidates,
                         "source_url": source["source_url"], "fetched_at_utc": source["fetched_at_utc"]})
    synonym_rows = []
    for alias, (item, source) in sorted(synonyms.items()):
        canonical = resolve(alias)
        synonym_rows.append({"snapshot_id": snapshot_id, "site": SITE, "from_tag": alias,
                             "to_tag": item["to_tag"], "canonical_tag": canonical,
                             "target_in_catalogue": canonical in candidates,
                             "applied_count": item["applied_count"], "creation_date": item["creation_date"],
                             "last_applied_date": item.get("last_applied_date"),
                             "source_url": source["source_url"], "fetched_at_utc": source["fetched_at_utc"]})

    examples = []

    def example(incoming, canonical, kind, item, source):
        examples.append({
            "example_id": sha256(f"{snapshot_id}:{kind}:{incoming}".encode())[:24],
            "snapshot_id": snapshot_id, "site": SITE, "example_kind": kind,
            "incoming_tag": incoming, "canonical_tag": canonical, "label": "matchable",
            "split": group_split(canonical), "source_group": f"{SITE}:{canonical}",
            "canonical_question_count": tags[canonical][0]["count"],
            "synonym_applied_count": item.get("applied_count"),
            "synonym_creation_date": item.get("creation_date"),
            "synonym_last_applied_date": item.get("last_applied_date"),
            "source_url": source["source_url"], "fetched_at_utc": source["fetched_at_utc"],
        })

    for canonical in sorted(candidates):
        item, source = tags[canonical]
        example(canonical, canonical, "canonical_name", item, source)
    for alias, (item, source) in sorted(synonyms.items()):
        canonical = resolve(alias)
        if canonical in candidates:
            example(alias, canonical, "synonym", item, source)

    if not candidates or not any(row["example_kind"] == "synonym" for row in examples):
        raise ValueError("Snapshot must contain canonical tags and usable synonym examples")
    if len({row["example_id"] for row in examples}) != len(examples):
        raise ValueError("Duplicate example IDs")
    groups = collections.defaultdict(set)
    for row in examples:
        groups[row["source_group"]].add(row["split"])
    if any(len(splits) != 1 for splits in groups.values()):
        raise ValueError("Canonical group crosses splits")

    for filename, rows in (("tags.csv", tag_rows), ("synonyms.csv", synonym_rows), ("experiment.csv", examples)):
        write_csv(output / filename, rows)
    split_counts = {split: dict(collections.Counter(row["example_kind"] for row in examples if row["split"] == split))
                    for split in ("development", "calibration", "test")}
    authentication_modes = {page.get("authentication", "anonymous") for page in pages}
    manifest = {
        "snapshot_id": snapshot_id, "api_version": "2.3", "site": SITE,
        "authentication": next(iter(authentication_modes)) if len(authentication_modes) == 1 else "mixed",
        "snapshot_started_at_utc": min(page["fetched_at_utc"] for page in pages),
        "snapshot_finished_at_utc": max(page["fetched_at_utc"] for page in pages),
        "sampling": {"requested_records": limits, "pagesize": PAGE_SIZE, "tags_sort": "popular",
                     "synonyms_sort": "applied", "order": "desc", "complete_site_dump": False,
                     "tags_has_more": tag_pages[-1]["has_more"], "synonyms_has_more": synonym_pages[-1]["has_more"]},
        "counts": {"tags_fetched": len(tag_records), "tags_unique": len(tags),
                   "canonical_candidates": len(candidates), "known_alias_tags_excluded": len(tags) - len(candidates),
                   "synonyms_fetched": len(synonym_records), "synonyms_unique": len(synonyms),
                   "synonyms_in_catalogue": sum(row["target_in_catalogue"] for row in synonym_rows),
                   "synonyms_outside_catalogue": sum(not row["target_in_catalogue"] for row in synonym_rows),
                   "experiment_rows": len(examples), "canonical_groups": len(groups)},
        "split": {"seed": SPLIT_SEED, "algorithm": "SHA-256(seed:site:canonical) first 64 bits mod 10",
                  "group_proportions": {"development": 0.6, "calibration": 0.2, "test": 0.2},
                  "row_counts": split_counts,
                  "group_counts": dict(collections.Counter(next(iter(splits)) for splits in groups.values()))},
        "validation": {"unique_example_ids": True, "all_positive_targets_in_catalogue": True,
                       "no_canonical_group_crosses_splits": True, "no_synonym_cycles": True,
                       "no_conflicting_synonym_targets": True},
        "artifacts": {name: {"sha256": sha256((output / name).read_bytes())}
                      for name in ("tags.csv", "synonyms.csv", "experiment.csv")},
        "source_pages": pages,
    }
    if limits["tags"] is None:
        manifest["sampling"]["all_tags_requested"] = True
        manifest["sampling"]["complete_tag_collection"] = not tag_pages[-1]["has_more"]
    write_json(output / "manifest.json", manifest)
    print(json.dumps({"snapshot_id": snapshot_id, "counts": manifest["counts"], "splits": split_counts}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/stackoverflow"))
    parser.add_argument("--tags", type=int, default=2500)
    parser.add_argument("--synonyms", type=int, default=2500)
    parser.add_argument("--all-tags", action="store_true", help="Collect tags until has_more=false, reusing cached pages")
    parser.add_argument("--key-file", type=Path, help="Read a Stack Exchange app key from a local file instead of the environment")
    parser.add_argument("--offline", action="store_true", help="Rebuild from cached pages without network requests")
    args = parser.parse_args()
    if any(count < 1 for count in (args.tags, args.synonyms)):
        parser.error("Sample sizes must be positive")
    try:
        api_key = (args.key_file.read_text(encoding="utf-8") if args.key_file
                   else os.environ.get("STACKEXCHANGE_API_KEY", "")).strip() or None
        if api_key and any(character in api_key for character in ("\r", "\n", "\0")):
            raise ValueError("The app key must be a single line")
        if not args.offline and not api_key and (args.all_tags or args.tags > 2500 or args.synonyms > 2500):
            raise ValueError("Fetching beyond page 25 requires STACKEXCHANGE_API_KEY or --key-file")
        (args.output / "raw").mkdir(parents=True, exist_ok=True)
        request_state = {"quota_remaining": None}
        tag_limit = None if args.all_tags else args.tags
        tags, tag_pages = fetch_pages(args.output, "tags", tag_limit, args.offline, api_key, request_state)
        synonyms, synonym_pages = fetch_pages(args.output, "synonyms", args.synonyms, args.offline, api_key, request_state)
        build(args.output, tags, synonyms, tag_pages, synonym_pages,
              {"tags": tag_limit, "synonyms": args.synonyms})
    except (ValueError, OSError) as error:
        print(f"Dataset build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
