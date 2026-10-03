# AO3 Tag Canonicalisation Experiment

This is the live writeup for an experiment combining lexical retrieval, semantic embeddings, candidate fusion, and optional Jev adjudication to recover AO3 canonical tags. [plan.md](plan.md) describes the experimental sequence.

**Status, 2026-10-03:** the AO3 audit, frozen benchmark export, ID/type adapter, and **Stage 1 Freeform development pilot are complete**. On **2,000 aliases** against all **181,067 Freeform canonicals**, name-only embeddings achieve **80.95% Recall@10**, versus **70.35%** for the best name-only lexical baseline. Trigram retrieval with other development aliases reaches **85.00%**. The 250 identity controls are reported separately; calibration and test aliases remain unevaluated.

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

## Frozen benchmark and scope

**Adopt AO3 and start with Freeform.** It offers a large positive pool, many natural-language variants, explicit IDs and types, and enough canonical groups for meaningful holdouts. Character, Relationship, and Fandom should follow as separate evaluations, with their own candidate catalogues and error analysis.

Use the input's observed tag type to restrict candidates. That is a declared task assumption; an application receiving only an untyped string would need a separate type-inference or untyped-retrieval evaluation. Do not use the expected answer to choose a type or catalogue.

Merger IDs, resolved target names/IDs, and canonical-status labels are ground truth and import metadata, not query features. Stage 1 uses the raw incoming name and its observed type. The frozen snapshot ID is `ao3-20210226-ac07b915acab`; its [benchmark manifest](results/ao3/benchmark/manifest.json) records filtering rules, source and export hashes, splits, and sampling.

The frozen split hashes `tag-matching-jev-ao3-v1:canonical_id`, takes its first 64 bits modulo ten, and assigns buckets 0–5/6–7/8–9 to development/calibration/test. All aliases and identity controls follow their resolved target's group. The Freeform population is:

| Split | Synonym examples | Canonical groups with synonyms |
|---|---:|---:|
| Development | 70,294 | 28,205 |
| Calibration | 24,943 | 9,394 |
| Test | 21,406 | 9,302 |

Across all four large types, the same rule gives 135,265 development, 46,014 calibration, and 43,465 test synonyms. These populations are exported in `data/ao3-benchmark/synonyms.csv`; the initial evaluation uses only the sampled Freeform development rows. Holdout canonical names remain ordinary candidates; holdout aliases are neither indexed nor embedded nor scored.

The initial sample sorts eligible development aliases by the full SHA-256 of `tag-matching-jev-ao3-freeform-stage1-v1:source_id`, with numeric ID as a tie break, then takes 2,000 while skipping targets already represented three times. This yields **1,761 target groups**: 1,583 contribute one alias, 117 contribute two, and 61 contribute three. Separately, the first 250 development canonicals ordered by SHA-256 of `tag-matching-jev-ao3-freeform-stage1-v1:identity:canonical_id` supply identity controls.

This is an unweighted sample of visible-name positives, with large alias families capped; it is not usage-weighted or an estimate for all incoming AO3 tags. The sample was frozen before scoring. Every retrieval condition retains all **181,067 Freeform candidates**, including distractors with no sampled synonym. Identity controls are reported separately. Full-pool evaluation remains later work.

All Freeform development queries against all canonical candidates would form roughly 12.73 billion score cells, or 101.8 GB in float64 before sorting. The AO3 runner instead computes bounded batches and checkpoints full target ranks and top-ten IDs/scores. It uses exhaustive scoring without similarity thresholds, candidate pruning, or approximate indexes.

### Exported files and adapter

| Local export | Rows | Contents |
|---|---:|---|
| `data/ao3-benchmark/catalogue.csv` | 1,175,987 | Canonical IDs, raw names, types, flags, counts, empty direct merger IDs, group splits |
| `data/ao3-benchmark/synonyms.csv` | 224,744 | Source IDs/names/types/counts, direct merger IDs, resolved canonical IDs/names, path lengths, group splits |
| `data/ao3-benchmark/development.csv` | 2,250 | Frozen 2,000 Freeform synonym queries and 250 identity controls |
| `data/ao3-benchmark/manifest.json` | — | Source/export checksums, exclusions, group split, and sampling policy; reviewable copy under `results/ao3/benchmark/` |

The [AO3 loader](scripts/ao3_benchmark.py) verifies checksums, ID/type/name agreement, canonical-group splits, the deterministic sample, and identity controls before evaluation. It exposes the full Freeform canonical catalogue, development queries, and development-only aliases. No original CSV is changed; raw exports, vectors, and detailed predictions remain under gitignored `data/`.

## Stage 1 methods

The primary comparison gives every method the same raw canonical names. The secondary lexical condition adds **70,294 development aliases**, masking each query's own source ID before lookup or scoring. A target receives the maximum score across its permitted representations and occupies only one shortlist position. Other development aliases, including other sampled queries, remain available: this is a leave-one-alias-out condition, not an evaluation of unseen canonical families. It supplies more lexical information than the primary name-only condition.

Exact lookup is cumulative: raw strings, case folding, whitespace collapse, then conservative punctuation normalization. The last step maps internal U+2010/U+2011 to `-` and U+2018/U+2019 to `'` only between alphanumeric characters. It preserves `/`, `&`, `|`, parentheses, diacritics, participant order, and other punctuation. A normalized key reaching multiple target IDs abstains. Coverage, incorrect unique matches, and ambiguous keys are counted separately.

Lexical ranking uses actual PostgreSQL `pg_trgm` functions `similarity(query, candidate)`, `word_similarity(query, candidate)`, and `strict_word_similarity(query, candidate)`, plus RapidFuzz normalized Levenshtein similarity and `WRatio` with no text processor. All score ties break by ascending numeric canonical ID. The PostgreSQL functions ignore non-alphanumeric characters, so exact string distinctions can disappear in trigram scores. [PostgreSQL documentation](https://www.postgresql.org/docs/18/pgtrgm.html).

The semantic baseline uses **`openai/text-embedding-3-small`, 1,536 dimensions**, through the endpoint in [the original API example](example_api/openai_embedding.py). Both sides contain raw tag names only. Returned float32 vectors are cached losslessly from base64, then explicitly L2-normalized and scored by exhaustive dot products in float64. Cosine similarity follows the [OpenAI embedding guidance](https://developers.openai.com/api/docs/guides/embeddings); the request format follows the [OpenRouter embedding API](https://openrouter.ai/docs/api/api-reference/embeddings/submit-an-embedding-request).

Recall@1/3/5/10 is reported per alias and as a macro average of per-target recall. Percentile 95% intervals use 2,000 bootstrap resamples of whole canonical groups, seed `20261003`. Paired differences resample the same groups. These describe uncertainty within this development pilot and do not remove sampling bias or establish final-test performance. No Jev adjudication, fitted threshold, fusion, or rejection decision is part of Stage 1.

## Stage 1 results

All recall values below are percentages over the same 2,000 synonym queries. Complete per-method and identity results are in [metrics.csv](results/ao3/stage1/metrics.csv); confidence intervals at every k, paired comparisons, runtime metadata, and source hashes are in [summary.json](results/ao3/stage1/summary.json).

### Primary comparison: canonical names only

| Method | R@1 | R@3 | R@5 | R@10 | R@10 95% CI | Macro R@10 |
|---|---:|---:|---:|---:|---:|---:|
| pg_trgm `similarity` | 50.25 | 60.85 | 65.10 | 70.35 | 68.26–72.48 | 71.53 |
| pg_trgm `word_similarity` | 42.80 | 52.10 | 56.20 | 61.55 | 59.30–63.80 | 63.83 |
| pg_trgm `strict_word_similarity` | 46.05 | 55.25 | 58.80 | 63.85 | 61.58–66.07 | 65.79 |
| Normalized Levenshtein | 19.45 | 25.90 | 29.15 | 34.85 | 32.82–37.00 | 35.68 |
| RapidFuzz WRatio | 24.00 | 36.30 | 39.20 | 43.75 | 41.56–46.00 | 45.28 |
| **text-embedding-3-small, cosine** | **58.40** | **71.35** | **75.85** | **80.95** | **79.07–82.82** | **81.44** |

Embeddings retrieve the correct canonical in the top ten for **1,619/2,000** aliases, compared with **1,407/2,000** for ordinary trigram similarity. The paired improvement is **10.60 percentage points**, with a group-bootstrap 95% interval of **8.57–12.65 points**. At rank one the improvement is 8.15 points, interval 5.91–10.38. The macro results support the same ordering.

### Secondary comparison: permitted development aliases

Each lexical target may also use its development aliases, with the evaluated alias removed. The number of canonical candidates stays 181,067; the representation pool grows to 251,361 strings before query masking.

| Method | R@1 | R@3 | R@5 | R@10 | R@10 95% CI | Macro R@10 |
|---|---:|---:|---:|---:|---:|---:|
| **pg_trgm `similarity`** | **65.80** | **78.65** | **81.55** | **85.00** | **83.46–86.53** | **84.29** |
| pg_trgm `word_similarity` | 60.90 | 72.15 | 75.10 | 79.50 | 77.70–81.29 | 79.14 |
| pg_trgm `strict_word_similarity` | 62.55 | 72.80 | 77.05 | 80.25 | 78.50–81.96 | 79.97 |
| Normalized Levenshtein | 42.00 | 53.15 | 57.55 | 62.65 | 60.51–64.82 | 59.56 |
| RapidFuzz WRatio | 39.10 | 55.40 | 58.05 | 61.75 | 59.58–63.95 | 60.07 |

Other known aliases add **14.65 points** to trigram Recall@10. This is strong evidence that representation coverage matters. The secondary condition has additional labelled lexical information, so its 85.00% versus the name-only embedding result is not a controlled comparison of lexical and semantic scoring alone. An alias-enriched semantic representation remains to be tested in Stage 2.

### Deterministic matching and identity controls

Raw, case-folded, and whitespace-normalized exact lookup solve **0/2,000 synonym queries**, including when other development aliases are permitted. Conservative punctuation normalization with permitted aliases solves **3/2,000 (0.15%)**; canonical-name-only lookup still solves none. There are no incorrect unique exact matches or ambiguous query lookups in this sample. Across the whole lookup vocabulary, punctuation normalization creates two cross-target collision keys using canonical names and four using names plus aliases; the matcher retains these collisions and would abstain on them.

All exact conditions, normalized Levenshtein, WRatio, ordinary trigram similarity, and embeddings rank or return the correct answer first for **250/250 identity controls**. Word and strict-word similarity each rank **248/250 (99.2%)** identities first using names, or **246/250 (98.4%)** with aliases; both reach 250/250 at Recall@3. Their extent-based matching can give another name an equal score, leaving the numeric-ID tie break to decide first place. Identity controls are excluded from synonym metrics.

The PostgreSQL punctuation probes give similarity 1.0 to each of `A/B` versus `A&B`, `A|B` versus `A B`, and `A (B)` versus `A B`. A trigram score of 1 therefore does not establish AO3 equivalence. This also supports keeping exact punctuation-preserving checks and type-specific evaluation in the later pipeline.

### Complementarity and remaining misses

For name-only top-ten retrieval, embeddings and ordinary trigram similarity both succeed on 1,313 aliases; embeddings alone succeed on 306, trigrams alone on 94, and neither succeeds on 287. The union of their lists therefore contains the answer for **1,713/2,000 (85.65%)**, with up to **20 candidates**. This is a candidate-pool ceiling for later fusion of those lists; no fused top-ten ranking has been evaluated.

The [descriptive difficulty analysis](results/ao3/stage1/lexical_difficulty.csv) bins labelled pairs by raw-name normalized Levenshtein similarity. These bands were added for follow-up analysis and never used as retrieval features or sampling criteria.

| Raw pair similarity | Queries | Trigram R@10 | Embedding R@10 |
|---|---:|---:|---:|
| Below 0.25 | 459 | 29.63 | 56.43 |
| 0.25 to below 0.50 | 753 | 69.46 | 81.14 |
| 0.50 to below 0.75 | 631 | 94.14 | 94.61 |
| 0.75 and above | 157 | 98.09 | 96.82 |

The largest semantic advantage occurs when query and canonical names share little character-level similarity. The 34 queries containing non-ASCII characters remain difficult: trigram Recall@10 is 23.53% and embedding Recall@10 is 58.82%. This small character-based slice does not establish performance for a particular language. Usage, length, and punctuation strata are also included in the summary; only three sampled names exceed 50 characters, so that slice supports little inference.

The [error review](results/ao3/stage1/semantic_errors.csv) records ten of the 381 semantic top-ten misses, selected by a fixed diagnostic hash order rather than by model confidence. Illustrative rows from that selection are:

| Incoming name | Recorded canonical | Trigram rank | Embedding rank |
|---|---|---:|---:|
| `Dark-fic` | `Dark` | 2 | 45 |
| `Missing moments from canon` | `Missing Scene` | 10 | 71 |
| `OnS Gift Exchange` | `Owari no Seraph Gift Exchange` | 68 | 34 |

These examples motivate testing lexical/semantic complementarity and better representation of fandom-specific shorthand. They do not establish the frequency of particular error causes. A selector restricted to the semantic top ten cannot recover the 381 missing targets, so retrieval improvements remain necessary before measuring Jev's selection benefit.

For Stage 2, retain ordinary `pg_trgm similarity` and this embedding model as references. Test representations using permitted aliases and definitions where provenance is available, with the same sample, candidate catalogue, and masking rules. Context from works needs the separate leakage controls described above.

### Runtime, cost, and validation

The run used Python 3.11.16, NumPy 2.4.6, RapidFuzz 3.14.6, and PostgreSQL 18.6 with `pg_trgm` 1.6 and locale `C`. All three PostgreSQL functions over 2,250 queries and 251,361 representations took **1,283 seconds (21.4 minutes)** with four query workers. Batch scoring took **12.62 seconds** for Levenshtein, **232.86 seconds** for WRatio, and **14.12 seconds** for exact vector dot products. The latter timings exclude loading, API collection and ranking/checkpoint overhead; they are not comparable end-to-end serving latencies.

Embedding collection cached **183,067 unique strings** in **179 batches**, covering the full Freeform canonical catalogue and the 2,000 sampled aliases. Identity controls reuse canonical-name vectors. The provider reported **1,434,207 input tokens** and **US$0.02868414** in embedding cost. Collection took about two minutes with four concurrent requests, all served by OpenAI. No calibration/test alias, definition, work context, or Jev request was sent.

The pipeline saves **42,750 predictions across 19 retrieval conditions**. All **24 repository tests passed**, including 14 AO3 audit/baseline tests. A complete offline replay validated the embedding and ranking caches and produced the final report. [validation.json](results/ao3/stage1/validation.json) records the integrity checks; [analysis.json](results/ao3/stage1/analysis.json) records **152 independent metric checks** that recompute micro and macro recall from candidate membership rather than trusting saved rank fields. Original source hashes and export/source-code checksums also match.

## Limitations and remaining work

- **No-match and ambiguous ground truth.** The 221,190 visible unmerged noncanonical tags, including 166,244 Freeform tags, are unlabelled. Absence of a merger can reflect an unprocessed tag or an unclear concept; it does not prove that no equivalent canonical exists.
- **Definitions and a canonical hierarchy.** The dump contains names and merger links, not curated definitions or metatag/subtag edges.
- **Current labels and rare spelling coverage.** This is a historical, partially suppressed snapshot. Results describe the selected named subset and its recorded wrangling policy.
- **Final-test and downstream measurements.** The current development baseline does not establish Jev benefit, fusion performance, no-match rejection quality, or automation precision.
- **Model provenance.** Canonical-group holdouts control local evaluation leakage; they cannot rule out the pretrained model having encountered public AO3 vocabulary. The API exposes an unversioned model alias, so captured vectors identify this specific run.

For a later rejection study, curate no-match/ambiguous cases separately or create a clearly labelled experiment that deliberately removes known canonical targets from the catalogue. Keep that constructed stress test distinct from naturally occurring no-match data.

## Artifacts and reproduction

| Artifact | Purpose |
|---|---|
| [analysis.json](results/ao3/audit/analysis.json) | Full-file counts, source hashes, merger integrity, per-type/split diagnostics, works coverage, and analysis-script hash |
| [tag_types.csv](results/ao3/audit/tag_types.csv) | Compact type-by-type catalogue and synonym counts |
| [analyze_ao3_dataset.py](scripts/analyze_ao3_dataset.py) | Reproducible local audit; no API calls or model inference |
| [test_ao3_analysis.py](tests/test_ao3_analysis.py) | Tests for graph resolution, conflicts/cycles, name suppression, type consistency, missing references, and nullable works fields |
| [benchmark manifest](results/ao3/benchmark/manifest.json) | Frozen source/export hashes, inclusion rules, splits, sample policy and counts |
| [Stage 1 summary](results/ao3/stage1/summary.json) and [metrics](results/ao3/stage1/metrics.csv) | AO3-only recall, bootstrap intervals, comparisons, strata, timings and provenance |
| [validation](results/ao3/stage1/validation.json) and [follow-up analysis](results/ao3/stage1/analysis.json) | Integrity checks, independent metric verification and descriptive artifact hashes |
| [build_ao3_benchmark.py](scripts/build_ao3_benchmark.py) | Export the catalogue, positive pool, and deterministic development sample |
| [ao3_benchmark.py](scripts/ao3_benchmark.py) | AO3 ID/type adapter with checksum and leakage checks |
| [run_ao3_baselines.py](scripts/run_ao3_baselines.py) | Resumable exact lexical/vector scoring and development metrics |
| [cache_ao3_embeddings.py](scripts/cache_ao3_embeddings.py) | Batched, checksummed raw-name embeddings; supports offline reuse |
| [analyze_ao3_baselines.py](scripts/analyze_ao3_baselines.py) | Verify candidate-based recall; reproduce difficulty bands and the diagnostic error selection |
| [test_ao3_baselines.py](tests/test_ao3_baselines.py) | Export/adapter, masking, collision, tie, batching, embedding and checkpoint regression tests |
| [plan.md](plan.md) | AO3 benchmark, schema, leakage, scaling, and staged evaluation plan |

Run from the repository root with NumPy installed, or use the project's existing virtual environment:

```bash
.venv/bin/python scripts/analyze_ao3_dataset.py
.venv/bin/python -m unittest discover -s tests -p 'test_ao3_analysis.py' -v
```

The audit took about 2–3 minutes on this workspace. It streams the CSVs and retains compact tag metadata for ID joins; memory use is still substantial for 14.5 million tags. `--input` and `--output` select alternative directories; `--skip-works` limits the audit to tags and records that works were not analysed. The four AO3 audit tests passed.

To reproduce the benchmark and Stage 1, install [requirements.txt](requirements.txt) in the virtual environment and ensure `postgres`, `initdb`, `pg_ctl`, and the `pg_trgm` extension are installed. Embedding collection reads `OPENROUTER_API_KEY` from the environment. It sends raw public names only, with no label fields or work context.

```bash
.venv/bin/python scripts/build_ao3_benchmark.py
.venv/bin/python scripts/cache_ao3_embeddings.py
OPENBLAS_NUM_THREADS=4 .venv/bin/python scripts/run_ao3_baselines.py --offline
.venv/bin/python scripts/analyze_ao3_baselines.py
.venv/bin/python -m unittest discover -s tests -p 'test_ao3*.py' -v
```

The exporter validates an existing frozen export and its original source hashes rather than silently resampling it. Use a new `--output` and `--report` directory for a different sample. Embedding batches and ranking checkpoints are checksummed and resumable. `--offline` forbids API requests; the default runner uses a disposable PostgreSQL cluster only when lexical checkpoints are missing. That cluster listens on a private local Unix socket, has no TCP listener, and is stopped after scoring. The persistent application schema and pgvector indexes are not required for this baseline.

Individual phases are available through `--phase lexical`, `--phase semantic`, and `--phase report`. The report phase rebuilds metrics from completed checkpoints without starting a database or contacting the API. Source hashes, the frozen manifest, and the scoring protocol identify the score cache; a code or recipe change creates a separate cache. Provider model aliases are unversioned, so rerunning the API later is not guaranteed to reproduce the same vectors; offline replay uses this run's captured vectors.

For future PostgreSQL serving, [Stage 11 of the plan](plan.md#110-postgresql-index-experiment) specifies a GiST trigram index, a B-tree for normalized lookup, and an HNSW cosine index, with type/configuration scoping and recall checks against this exhaustive reference. These indexes have not been created as part of Stage 1.

The [earlier pilot writeup and plan](docs/archive/stackoverflow/README.md) are archived, and its original data, code, caches, and `results/stage1/` artifacts are preserved for provenance. The AO3 runner has its own adapter, cache, and results directory; it reuses only the disposable PostgreSQL helper from the earlier runner. Historical pilot scores are excluded from the active experiment's findings.
