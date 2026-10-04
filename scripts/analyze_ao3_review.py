#!/usr/bin/env python3
"""Validate independent AO3 judgments and report review-partition metrics.

Blank references remain missing. Unresolved judgments are excluded from accuracy
denominators and their proposed merges are counted separately, never as correct.
"""

import argparse
from collections import Counter
import json
import math
from pathlib import Path

from ao3_benchmark import csv_rows, fingerprint, load_benchmark, sha256, write_json
from ao3_unmerged_review import LABEL_FIELDS, OUTPUT, PROPOSAL_FIELDS, ROOT


def proportion(successes, total):
    if not total:
        return {"successes": successes, "denominator": total, "estimate": None, "wilson95": None}
    z = 1.959963984540054
    p = successes / total
    divisor = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / divisor
    width = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / divisor
    return {"successes": successes, "denominator": total, "estimate": p,
            "wilson95": [max(0., centre - width), min(1., centre + width)]}


def validate_references(rows, cases, catalogue):
    expected = {c["source_id"] for c in cases}
    seen, references = set(), {}
    for raw in rows:
        if set(raw) != set(LABEL_FIELDS) or any(v is None for v in raw.values()):
            raise ValueError("Incorrect judgment CSV schema")
        r = {k: v.strip() for k, v in raw.items()}
        source_id = int(r["source_id"])
        if source_id not in expected or source_id in seen:
            raise ValueError("Unknown or duplicate review source ID")
        seen.add(source_id)
        if not r["judgment"]:
            continue
        if r["judgment"] not in ("synonym", "new_canonical", "unresolved"):
            raise ValueError(f"Unknown judgment for {source_id}")
        if not r["reviewer"] or not r["rationale"] or r["confidence"] not in ("high", "medium", "low"):
            raise ValueError(f"Incomplete reference judgment for {source_id}: reviewer, rationale, confidence required")
        if r["catalogue_checked"] not in ("", "yes", "no"):
            raise ValueError(f"Invalid catalogue search field for {source_id}")
        if r["judgment"] == "synonym":
            if not r["canonical_id"].isdigit() or int(r["canonical_id"]) not in catalogue or r["proposed_name"]:
                raise ValueError(f"Synonym {source_id} requires an existing Freeform canonical ID and no proposed name")
            r["canonical_id"] = int(r["canonical_id"])
        elif r["judgment"] == "new_canonical":
            if r["canonical_id"] or r["catalogue_checked"] != "yes":
                raise ValueError(f"New canonical {source_id} requires catalogue search and no target ID; proposed name is optional")
        elif r["canonical_id"] or r["proposed_name"]:
            raise ValueError(f"Unresolved {source_id}: put tentative targets in the rationale, not target fields")
        references[source_id] = r
    if seen != expected:
        raise ValueError("Judgment CSV must retain every sampled source ID, with blank rows for pending cases")
    return references


def validate_proposals(rows, cases, catalogue):
    expected = {c["source_id"] for c in cases}
    seen, proposals = set(), {}
    for raw in rows:
        if set(raw) != set(PROPOSAL_FIELDS) or any(v is None for v in raw.values()):
            raise ValueError("Incorrect proposal CSV schema")
        r = {k: v.strip() for k, v in raw.items()}
        source_id = int(r["source_id"])
        if source_id not in expected or source_id in seen:
            raise ValueError("Unknown or duplicate proposal source ID")
        seen.add(source_id)
        if not r["action"]:
            continue
        if r["action"] not in ("synonym", "new_canonical", "abstain"):
            raise ValueError("Unknown proposed action")
        if r["action"] == "synonym":
            if not r["canonical_id"].isdigit() or int(r["canonical_id"]) not in catalogue or r["proposed_name"]:
                raise ValueError("Proposed synonym must have an existing Freeform canonical ID only")
            r["canonical_id"] = int(r["canonical_id"])
        elif r["action"] == "new_canonical":
            if r["canonical_id"]:
                raise ValueError("Proposed new canonical cannot select an existing ID; proposed name is optional")
        elif r["canonical_id"] or r["proposed_name"]:
            raise ValueError("Abstention cannot select a canonical")
        proposals[source_id] = r
    if seen != expected:
        raise ValueError("Proposal CSV must retain all sampled source IDs")
    return proposals


def baseline_proposals(cases, method):
    result = {}
    for c in cases:
        template = c["rankings"]["template"]["top_ids"][0]
        pooled = c["rankings"]["template_alias_max"]["top_ids"][0]
        abstain = method == "semantic_agreement_else_abstain" and template != pooled
        result[c["source_id"]] = {"action": "abstain" if abstain else "synonym",
                                  "canonical_id": template if method == "always_template" else pooled}
    return result


def measure_actions(cases, references, proposals):
    ids = {c["source_id"] for c in cases}
    submitted = ids & proposals.keys()
    reviewed = ids & references.keys()
    resolved = {t for t in reviewed if references[t]["judgment"] != "unresolved"}
    unresolved = reviewed - resolved
    merges = {t for t in submitted if proposals[t]["action"] == "synonym"}
    new = {t for t in submitted if proposals[t]["action"] == "new_canonical"}
    resolved_merges = merges & resolved
    correct = {t for t in resolved_merges if references[t]["judgment"] == "synonym"
               and proposals[t]["canonical_id"] == references[t]["canonical_id"]}
    reference_new = {t for t in resolved if references[t]["judgment"] == "new_canonical"}
    resolved_new = new & resolved
    return {
        "submitted": len(submitted), "missing_proposals": len(ids - submitted),
        "synonym_proposals": len(merges), "new_canonical_proposals": len(new),
        "abstentions": sum(proposals[t]["action"] == "abstain" for t in submitted),
        "proposal_coverage": proportion(len(merges | new), len(ids)),
        "resolved_synonym_precision": proportion(len(correct), len(resolved_merges)),
        "resolved_false_merge_fraction": proportion(len(resolved_merges - correct), len(resolved_merges)),
        "false_merge_rate_on_reference_new_concepts": proportion(len(merges & reference_new), len(reference_new & submitted)),
        "merges_on_unresolved_references": len(merges & unresolved),
        "merges_without_reference": len(merges - reviewed),
        "new_canonical_decision_precision": proportion(len(resolved_new & reference_new), len(resolved_new)),
        "new_canonical_proposals_on_unresolved": len(new & unresolved),
        "new_canonical_proposals_without_reference": len(new - reviewed),
        "new_canonical_label_quality": "Unscored: agreement on novelty does not validate proposed wording or usefulness",
    }


def report_partition(cases, references, proposals=None):
    ids = {c["source_id"] for c in cases}
    reviewed = {t: r for t, r in references.items() if t in ids}
    counts = Counter(r["judgment"] for r in reviewed.values())
    synonyms = [c for c in cases if reviewed.get(c["source_id"], {}).get("judgment") == "synonym"]
    retrieval = {}
    for method in ("template", "template_alias_max", "lexical_alias_max", "union"):
        for k in ((10,) if method == "union" else (1, 3, 5, 10)):
            correct = 0
            for c in synonyms:
                candidates = ({t for r in c["rankings"].values() for t in r["top_ids"]}
                              if method == "union" else c["rankings"][method]["top_ids"][:k])
                correct += reviewed[c["source_id"]]["canonical_id"] in candidates
            retrieval[f"{method}/{'shortlist' if method == 'union' else 'recall@' + str(k)}"] = proportion(correct, len(synonyms))
    methods = {name: measure_actions(cases, reviewed, baseline_proposals(cases, name))
               for name in ("always_template", "always_alias_max", "semantic_agreement_else_abstain")}
    if proposals is not None:
        methods["supplied_proposals"] = measure_actions(cases, reviewed, proposals)
    return {"sampled": len(cases), "reviewed": len(reviewed), "pending": len(cases) - len(reviewed),
            "status": "reference_review_pending" if not reviewed else "complete_review" if len(reviewed) == len(cases) else "partial_review_not_population_estimate",
            "reference_counts": dict(counts),
            "reference_proportions_among_reviewed": {label: proportion(counts[label], len(reviewed)) for label in ("synonym", "new_canonical", "unresolved")},
            "shortlist_recall_on_reviewed_synonyms": retrieval, "decision_baselines": methods,
            "accuracy_scope": "Only resolved reviewed cases; unresolved and missing references do not enter accuracy denominators"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--judgments", type=Path, default=OUTPUT / "judgments.csv")
    parser.add_argument("--proposals", type=Path)
    parser.add_argument("--partition", choices=("calibration", "validation", "challenge", "all"), default="calibration")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    summary = json.loads((OUTPUT / "summary.json").read_text())
    for path, expected in summary["artifacts"].items():
        if sha256(Path(path)) != expected:
            raise ValueError(f"Frozen review evidence changed: {path}")
    cases = json.loads((ROOT / "cases.json").read_text())
    catalogue = {r["canonical_id"] for r in load_benchmark().catalogue}
    references = validate_references(list(csv_rows(args.judgments)), cases, catalogue)
    proposals = validate_proposals(list(csv_rows(args.proposals)), cases, catalogue) if args.proposals else None
    partitions = ("calibration", "validation", "challenge") if args.partition == "all" else (args.partition,)
    reports = {part: report_partition([c for c in cases if c["partition"] == part], references, proposals) for part in partitions}
    if args.partition == "all":
        reports["random_combined"] = report_partition([c for c in cases if c["arm"] == "random"], references, proposals)
    payload = {"status": "reference_review_pending" if not references else "review_results",
               "cases_sha256": fingerprint(cases), "judgments_sha256": sha256(args.judgments),
               "proposal_sha256": sha256(args.proposals) if args.proposals else None,
               "partitions": reports,
               "notes": ["Challenge cases are not pooled into population estimates.",
                         "Partial reviews may be selected by ease or order; their proportions are descriptive only.",
                         "Calibration and validation here are new review splits, not the original benchmark group holdouts.",
                         "Wilson intervals describe tag-level proportions, not independent concept clusters.",
                         "Use calibration only for threshold selection; freeze any fitted policy before validation.",
                         "The three decision baselines were fixed before review; agreement is not a calibrated confidence score."],
               "source_sha256": sha256(Path(__file__))}
    if args.partition == "all":
        targets = {part: {references[c["source_id"]]["canonical_id"] for c in cases if c["partition"] == part
                         and references.get(c["source_id"], {}).get("judgment") == "synonym"}
                   for part in ("calibration", "validation")}
        payload["shared_reviewed_canonical_targets_between_review_splits"] = len(targets["calibration"] & targets["validation"])
    destination = args.output or OUTPUT / f"evaluation-{args.partition}.json"
    write_json(destination, payload)
    print(json.dumps({"status": payload["status"], "partitions": {p: {k: r[k] for k in ("sampled", "reviewed", "pending")} for p, r in reports.items()}, "output": str(destination)}, indent=2))


if __name__ == "__main__":
    main()
