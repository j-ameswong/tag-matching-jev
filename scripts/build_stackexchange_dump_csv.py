#!/usr/bin/env python3
"""Verify a downloaded Stack Overflow tag dump and convert Tags.xml to CSV.

The output's raw/ directory must contain stackoverflow.com-Tags.7z and
archive-metadata.json. This command is offline and requires the 7z executable.
"""

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import quote
import xml.etree.ElementTree as ET


ARCHIVE_NAME = "stackoverflow.com-Tags.7z"


def checksum(path, algorithm="sha256"):
    return hashlib.new(algorithm, path.read_bytes()).hexdigest()


def build(output, release):
    raw = output / "raw"
    archive = raw / ARCHIVE_NAME
    metadata_path = raw / "archive-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    entry = next(item for item in metadata["files"] if item["name"] == ARCHIVE_NAME)
    if archive.stat().st_size != int(entry["size"]):
        raise ValueError("Archive size does not match Internet Archive metadata")
    for algorithm in ("md5", "sha1"):
        if checksum(archive, algorithm) != entry[algorithm]:
            raise ValueError(f"Archive {algorithm} does not match Internet Archive metadata")

    xml_path = raw / "Tags.xml"
    temporary_xml = raw / "Tags.xml.tmp"
    with temporary_xml.open("wb") as handle:
        result = subprocess.run(["7z", "e", "-so", str(archive), "Tags.xml"],
                                stdout=handle, stderr=subprocess.PIPE, check=False, timeout=60)
    if result.returncode:
        raise ValueError("Unable to extract Tags.xml: " + result.stderr.decode("utf-8", errors="replace"))
    temporary_xml.replace(xml_path)

    root = ET.parse(xml_path).getroot()
    if root.tag != "tags":
        raise ValueError("Expected a tags XML document")
    elements = root.findall("row")
    if not elements:
        raise ValueError("Tag dump is empty")
    archive_hash = checksum(archive)
    snapshot_id = f"stackoverflow-tags-dump-{release}-{archive_hash[:16]}"
    rows, names, identifiers = [], set(), set()
    fields = Counter()
    for element in elements:
        source = element.attrib
        name, identifier, count = source["TagName"], int(source["Id"]), int(source["Count"])
        if not name or identifier <= 0 or count < 0:
            raise ValueError("Invalid tag name, ID, or question count")
        if name in names or identifier in identifiers:
            raise ValueError("Duplicate tag name or ID in dump")
        names.add(name)
        identifiers.add(identifier)
        fields.update(source.keys())
        optional = {key: int(source[key]) if key in source else None for key in ("ExcerptPostId", "WikiPostId")}
        if any(value is not None and value <= 0 for value in optional.values()):
            raise ValueError("Invalid excerpt or wiki post ID")
        rows.append({"snapshot_id": snapshot_id, "site": "stackoverflow", "name": name,
                     "question_count": count, "source_tag_id": identifier,
                     "excerpt_post_id": optional["ExcerptPostId"], "wiki_post_id": optional["WikiPostId"],
                     "dump_release": release})

    csv_path = output / "tags.csv"
    temporary_csv = output / "tags.csv.tmp"
    with temporary_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: row["name"]))
    temporary_csv.replace(csv_path)

    identifier = metadata["metadata"]["identifier"]
    manifest = {
        "snapshot_id": snapshot_id, "site": "stackoverflow", "source_type": "historical_data_dump",
        "dump_release": release, "creator": metadata["metadata"].get("creator"),
        "source_url": f"https://archive.org/download/{quote(identifier)}/{ARCHIVE_NAME}",
        "source_metadata_url": f"https://archive.org/metadata/{quote(identifier)}",
        "archive_modified_at_utc": datetime.fromtimestamp(int(entry["mtime"]), timezone.utc).isoformat(),
        "downloaded_at_utc": datetime.fromtimestamp(archive.stat().st_mtime, timezone.utc).isoformat(),
        "release_date_precision": "month; archive modification time is not a per-tag observation date",
        "scope": "Every row in the historical Tags.xml; not a current API snapshot",
        "synonyms_included": False,
        "unavailable_fields": ["has_synonyms", "is_moderator_only", "is_required", "tag_wiki_text", "question_context"],
        "counts": {"tags": len(rows), "unique_names": len(names), "unique_source_ids": len(identifiers),
                   "missing_excerpt_post_ids": len(rows) - fields["ExcerptPostId"],
                   "missing_wiki_post_ids": len(rows) - fields["WikiPostId"]},
        "source_field_counts": dict(sorted(fields.items())),
        "validation": {"archive_size_matches": True, "archive_md5_matches": True,
                       "archive_sha1_matches": True, "unique_names_and_ids": True,
                       "non_negative_question_counts": True},
        "artifacts": {str(path.relative_to(output)): {"bytes": path.stat().st_size, "sha256": checksum(path)}
                      for path in (csv_path, archive, xml_path, metadata_path)},
    }
    temporary_manifest = output / "manifest.json.tmp"
    temporary_manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary_manifest.replace(output / "manifest.json")
    print(json.dumps({"snapshot_id": snapshot_id, "counts": manifest["counts"], "csv": str(csv_path)}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/stackoverflow-dump-2024-04"))
    parser.add_argument("--release", default="2024-04", help="Historical release month, YYYY-MM")
    args = parser.parse_args()
    try:
        datetime.strptime(args.release, "%Y-%m")
        build(args.output, args.release)
    except (ValueError, OSError, KeyError, StopIteration, ET.ParseError, subprocess.TimeoutExpired) as error:
        print(f"Dump conversion failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
