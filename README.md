# AO3 Tag Canonicalisation Experiment

This is the live writeup for an experiment combining lexical retrieval, semantic embeddings, candidate fusion, and optional Jev adjudication to recover AO3 canonical tags. [plan.md](plan.md) describes the experimental sequence.

**Status, 2026-10-03:** AO3 is the active dataset. The full local tags and works files have been audited. They supply **224,744 usable named synonym mappings to 105,340 canonical targets**, with explicit tag types and optional observed co-tag context. This is sufficient to begin a substantial retrieval study. **AO3 baselines have not yet been run**; benchmark export and the AO3 evaluation adapter are the next implementation work.

## Research questions

1. Can retrieval place the recorded canonical tag in a small candidate list?
2. Which lexical, embedding, and fusion configurations improve candidate recall?
3. Does Jev improve selection or abstention on identical candidate lists?
4. How much can be automated at a validated incorrect-mapping rate?

The ground truth is AO3's recorded canonicalisation policy in this historical snapshot. A merger label expresses a platform decision; it need not be a universal claim of literal dictionary synonymy. Co-occurrence and broader/narrower tag relationships do not provide additional synonym labels.

## Source and provenance

The files are under **`data/ao3-dump/`**, using a hyphen. Their filenames identify the **2021-02-26** snapshot. AO3 announced its selective data release on **2021-03-21**, describing the two CSVs, name suppression, and approximate usage counts. [AO3 release announcement](https://archiveofourown.org/admin_posts/18804).

| Local input | Bytes | Parsed records |
|---|---:|---:|
| [tags-20210226.csv](data/ao3-dump/tags-20210226.csv) | 581,517,630 | 14,467,138 |
| [works-20210226.csv](data/ao3-dump/works-20210226.csv) | 968,060,352 | 7,269,693 |

Both original files are preserved unchanged. Their full SHA-256 hashes are recorded in [analysis.json](results/ao3/audit/analysis.json). These identify the local copies; the analysis date is separate from the historical file date.

The tags schema is `id,type,name,canonical,cached_count,merger_id`. The works schema supplies creation date, language, restricted/complete flags, word count, and `+`-separated tag IDs. It has an extra empty header field, while all actual records have six fields. This is accounted for by the parser.

## Tag audit

| Measure | Count |
|---|---:|
| Tag records and distinct tag IDs | 14,467,138 |
| Visible tag names | 1,621,975 |
| Names replaced by the literal `Redacted` | 12,845,163 (88.79%) |
| Tags marked canonical | 1,176,038 |
| Named canonical candidates with no outgoing merger | **1,175,987** |
| Tags with a merger, including suppressed names and conflicts | 3,466,917 |
| Visible noncanonical tags with a merger | 224,747 |
| Usable named synonyms after integrity checks | **224,744** |
| Distinct canonical targets reached by usable synonyms | **105,340** |
| Visible noncanonical tags without a merger | 221,190 |

There are no duplicate tag IDs, duplicate visible names, blank tag names, or negative cached counts. The primary catalogue uses source IDs rather than names as identity, even though visible names happen to be unique in this snapshot.

A usable synonym must satisfy all of the following:

1. Its source is noncanonical and has a visible name.
2. Its merger path terminates at a visible canonical tag with no outgoing merger.
3. Source and target have the same tag type.
4. The path has no missing reference, cycle, or conflicting canonical/merger state.

The audit excludes 51 canonical records that also have a merger. In total, 176 rows are those conflicts or reach them. There are 20 dangling merger references and one merged path terminating at a noncanonical tag; no cycles or self-mergers were found. Seven valid merger paths require two hops. Among the 224,747 named noncanonical sources with a merger, one reaches a missing target and two reach conflicting canonical records, leaving 224,744 usable pairs. None of these usable named pairs crosses types.

### Name suppression and approximate counts

`Redacted` is a missing-name marker and must never be embedded or treated as one repeated alias. The audit found 96,873 suppressed names with `cached_count >= 5`, and 849,932 visible canonical names with counts below five. Consequently, the actual name value determines visibility; applying a fresh count threshold would both admit placeholders and remove valid canonical names.

No visible noncanonical name has a cached count below five. The usable synonym pool therefore excludes the rarest source spellings and should not be treated as representative of all incoming tags. Usage counts can support exploratory strata, but they are approximate and are not independent repeated label observations.

### Coverage by type

| Tag type | Named canonical candidates | Usable synonyms | Canonical targets with synonyms |
|---|---:|---:|---:|
| **Freeform** | **181,067** | **116,643** | **46,901** |
| Character | 327,424 | 47,646 | 28,888 |
| Relationship | 625,122 | 45,219 | 24,592 |
| Fandom | 42,344 | 15,236 | 4,959 |
| ArchiveWarning | 6 | 0 | 0 |
| Category | 6 | 0 | 0 |
| Media | 12 | 0 | 0 |
| Rating | 5 | 0 | 0 |
| UnsortedTag | 1 | 0 | 0 |

The large types support different tasks and should have separate results. The small controlled types can provide identity checks but supply no merged-name positives here. `UnsortedTag` is not a useful synonym benchmark in this snapshot.

Across all usable pairs, the median target has one visible synonym, the 95th percentile has five, and the largest group has 1,334. The ten largest groups account for 2.90% of pairs. Report both per-synonym recall and a macro average giving each target equal weight; split and bootstrap by canonical target so related aliases remain together.

The vocabulary also provides useful normalization tests: 1,498,919 visible names contain whitespace, 61,672 contain non-ASCII characters, 506,759 contain `/`, and 183,173 contain `&`. These counts overlap. Preserve punctuation: AO3 distinguishes romantic/sexual relationship tags using `/` from platonic relationship tags using `&`. Pipes and parenthetical qualifiers can also carry identity information. [AO3 Tags FAQ](https://archive.transformativeworks.org/faq/tags?language_id=en).

## What the works table contributes

| Measure | Count |
|---|---:|
| Work records | 7,269,693 |
| Associated tag references | 119,146,249 |
| References whose tag ID is absent from the tags file | 8,213 across 8,139 distinct IDs |
| References to a tag with a suppressed name | 11,780,661 |
| Work records containing at least one usable named synonym | 3,954,844 |
| Distinct usable synonyms observed on a supplied work | 218,336 |
| Usable synonyms absent from the supplied works table | 6,408 |
| Records with missing word count | 2,268 |

No work repeats a tag ID within its own tag list. Three work records have no associated tags. Missing references must remain unresolved, and missing word counts must remain null rather than becoming zero. A positive tag pair can remain valid for the tag-only benchmark even when the source alias has no occurrence in this works table.

The works table can supply **observed co-tag context**, including a work's fandom/character tags where visible. It contains **no titles, summaries, body text, or original work IDs**. If contexts are materialized, identify their source by the file hash and logical CSV record ordinal. Work language is metadata about the work, not a verified language label for each tag.

Context introduces an answer-leakage risk: the same work may list the query alias, its canonical target, or another equivalent alias. Before comparing tag-only retrieval against retrieval with context, remove the query and every tag resolving to its expected target. Exclude held-out aliases from retrieval/enrichment, and prevent the same work record from supplying contexts across splits. Compare the two representations on the same subset of queries with eligible observed context.

## Suitability and initial benchmark recommendation

**Adopt AO3 and start with Freeform.** It offers a large positive pool, many natural-language variants, explicit IDs and types, and enough canonical groups for meaningful holdouts. Character, Relationship, and Fandom should follow as separate evaluations, with their own candidate catalogues and error analysis.

Use the input's observed tag type to restrict candidates. That is a declared task assumption; an application receiving only an untyped string would need a separate type-inference or untyped-retrieval evaluation. Do not use the expected answer to choose a type or catalogue.

Merger IDs, resolved target names/IDs, and canonical-status labels are ground truth and import metadata, not query features. The first retrieval run will use the raw incoming name and its observed type.

A proposed deterministic split hashes `tag-matching-jev-ao3-v1:canonical_id`, takes its first 64 bits modulo ten, and assigns buckets 0–5/6–7/8–9 to development/calibration/test. Its Freeform population would be:

| Split | Synonym examples | Canonical groups with synonyms |
|---|---:|---:|
| Development | 70,294 | 28,205 |
| Calibration | 24,943 | 9,394 |
| Test | 21,406 | 9,302 |

Across all four large types, the same rule gives 135,265 development, 46,014 calibration, and 43,465 test synonyms. **These are split diagnostics, not an exported benchmark or evaluation result.** The sampling policy and resulting example manifests must be frozen before running retrieval.

For an initial cost-controlled run, use a deterministic development sample, such as 2,000 Freeform aliases with a per-target cap, while retaining all **181,067 Freeform canonical candidates**, including distractors without sampled synonyms. Record the sample policy and its bias. Keep identity examples separate from the primary synonym metrics. Full-pool evaluation can follow once runtime is measured.

The larger catalogue requires implementation changes. All Freeform development queries against all canonical candidates form roughly 12.73 billion score cells, or 101.8 GB in float64 before sorting. Use batched exhaustive scoring and retain only the required rankings rather than allocating the full matrix. Introduce approximate search only after exact recall is established.

### What remains missing

- **No-match and ambiguous ground truth.** The 221,190 visible unmerged noncanonical tags, including 166,244 Freeform tags, are unlabelled. Absence of a merger can reflect an unprocessed tag or an unclear concept; it does not prove that no equivalent canonical exists.
- **Definitions and a canonical hierarchy.** The dump contains names and merger links, not curated definitions or metatag/subtag edges.
- **Current labels and rare spelling coverage.** This is a historical, partially suppressed snapshot. Results will describe the selected named subset and its recorded wrangling policy.
- **AO3 baseline measurements.** The dataset audit establishes usable labels and data requirements, not achieved retrieval accuracy, Jev benefit, or automation precision.

For a later rejection study, curate no-match/ambiguous cases separately or create a clearly labelled experiment that deliberately removes known canonical targets from the catalogue. Keep that constructed stress test distinct from naturally occurring no-match data.

## Artifacts and reproduction

| Artifact | Purpose |
|---|---|
| [analysis.json](results/ao3/audit/analysis.json) | Full-file counts, source hashes, merger integrity, per-type/split diagnostics, works coverage, and analysis-script hash |
| [tag_types.csv](results/ao3/audit/tag_types.csv) | Compact type-by-type catalogue and synonym counts |
| [analyze_ao3_dataset.py](scripts/analyze_ao3_dataset.py) | Reproducible local audit; no API calls or model inference |
| [test_ao3_analysis.py](tests/test_ao3_analysis.py) | Tests for graph resolution, conflicts/cycles, name suppression, type consistency, missing references, and nullable works fields |
| [plan.md](plan.md) | AO3 benchmark, schema, leakage, scaling, and staged evaluation plan |

Run from the repository root with NumPy installed, or use the project's existing virtual environment:

```bash
.venv/bin/python scripts/analyze_ao3_dataset.py
.venv/bin/python -m unittest discover -s tests -p 'test_ao3_analysis.py' -v
```

The audit took about 2–3 minutes on this workspace. It streams the CSVs and retains compact tag metadata for ID joins; memory use is still substantial for 14.5 million tags. `--input` and `--output` select alternative directories; `--skip-works` limits the audit to tags and records that works were not analysed. The four AO3 audit tests passed.

The active experiment starts with the AO3 data audit. The [earlier pilot writeup and plan](docs/archive/stackoverflow/README.md) are archived, and its original data, code, caches, and `results/stage1/` artifacts are preserved for provenance. Those runners assume the earlier CSV schema and are not an AO3 adapter. Their scores are excluded from the active experiment's findings.
