# AO3 Tag Canonicalisation Experiment

This is the live writeup for an experiment combining lexical retrieval, semantic embeddings, candidate fusion, and optional Jev adjudication to recover AO3 canonical tags. [plan.md](plan.md) describes the experimental sequence.

**Status, 2026-10-03:** the AO3 audit, benchmark export, Stage 1, and **Stage 2 experiments using the supplied dump are complete**. Across **2,000 development aliases** and all **181,067 Freeform candidates**, the strongest configuration reaches **92.05% Recall@10** and **73.15% Recall@1**: embed each templated canonical/alias name separately and take the maximum cosine per canonical ID. This was an explicitly recorded exploratory follow-up; original raw-name embeddings score 80.95% Recall@10. The 250 identity controls remain separate, and calibration/test aliases remain unevaluated. Summary and reader-engagement effectiveness require a larger HTML corpus.

The **500-tag unmerged review experiment is prepared and deferred** at the user's request on 2026-10-04. Its sample, evidence, local review page, and evaluation tooling are preserved. Independent reference labels, natural no-match performance, and new-canonical decision accuracy remain unknown. [Open the local review page](data/ao3-unmerged-review/review.html) or read the [review instructions](results/ao3/unmerged-review/README.md).

**Jev evaluation complete, 2026-10-04:** seven approaches were evaluated on those exact ten-candidate lists using existing AO3 merger labels. **AO3 instructions plus permitted aliases reach 81.65% overall matching accuracy using Jev's returned Choice, or 83.60% with the predeclared highest-non-none-probability rule**, versus 73.15% for retrieval's first choice. The latter selects the correct tag in **1,672/1,841 (90.82%)** answer-containing lists and **1,672/2,000 (83.60%)** overall. [Detailed results below](#jev-selection-on-the-best-ten-candidates); human review remains deferred.

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

Other known aliases add **14.65 points** to trigram Recall@10. This is strong evidence that representation coverage matters. The secondary condition has additional labelled lexical information, so its 85.00% versus the name-only embedding result is not a controlled comparison of lexical and semantic scoring alone. Stage 2 below tests semantic alias representations under a stricter global exclusion policy.

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

These findings motivated the Stage 2 comparisons below, retaining ordinary `pg_trgm similarity` and this embedding model with the same sample and candidate catalogue. Definitions remain unavailable; observed-work context requires separate leakage controls.

### Runtime, cost, and validation

The run used Python 3.11.16, NumPy 2.4.6, RapidFuzz 3.14.6, and PostgreSQL 18.6 with `pg_trgm` 1.6 and locale `C`. All three PostgreSQL functions over 2,250 queries and 251,361 representations took **1,283 seconds (21.4 minutes)** with four query workers. Batch scoring took **12.62 seconds** for Levenshtein, **232.86 seconds** for WRatio, and **14.12 seconds** for exact vector dot products. The latter timings exclude loading, API collection and ranking/checkpoint overhead; they are not comparable end-to-end serving latencies.

Embedding collection cached **183,067 unique strings** in **179 batches**, covering the full Freeform canonical catalogue and the 2,000 sampled aliases. Identity controls reuse canonical-name vectors. The provider reported **1,434,207 input tokens** and **US$0.02868414** in embedding cost. Collection took about two minutes with four concurrent requests, all served by OpenAI. No calibration/test alias, definition, work context, or Jev request was sent.

The pipeline saves **42,750 predictions across 19 retrieval conditions**. All **24 repository tests passed**, including 14 AO3 audit/baseline tests. A complete offline replay validated the embedding and ranking caches and produced the final report. [validation.json](results/ao3/stage1/validation.json) records the integrity checks; [analysis.json](results/ao3/stage1/analysis.json) records **152 independent metric checks** that recompute micro and macro recall from candidate membership rather than trusting saved rank fields. Original source hashes and export/source-code checksums also match.

## Stage 2: text and observed-work representations

The [frozen protocol](results/ao3/stage2/protocol.json) retains PostgreSQL `pg_trgm similarity`, `openai/text-embedding-3-small` at 1,536 dimensions, exhaustive cosine search, the original 2,000 development aliases, 250 separate identity controls, and all 181,067 Freeform candidates. Representation choices were fixed before collecting their embeddings or measuring recall. No model comparison, Jev decision, ANN index, learned fusion, or generated definition is involved.

### Information and leakage controls

All 2,000 evaluation aliases are removed globally before constructing candidate descriptions. This leaves **68,294** permitted development aliases; at most five per target are selected by a frozen hash order, excluding names longer than 160 characters. The resulting **48,080 selected examples** also supply a matching lexical reference. A separate lexical reference uses the complete permitted pool. Stage 1's per-query leave-one-out 85.00% score has a different information policy and is not reused as the Stage 2 alias baseline.

Work identity combines the immutable source-file hash with the logical CSV record ordinal. Independent hashing assigns 60% of works to candidate support, 20% to development query context, and 10% each to future calibration/test context. Support works containing any sampled evaluation alias are discarded: **34,990** records. The resulting pools contain **4,328,616 support works**, **1,454,164 development-context works**, and **1,451,923 reserved works**.

A candidate's support membership uses its direct canonical ID and permitted development aliases, deduplicated within each work. A query's context membership uses only its raw source ID; its expected canonical ID is used solely to remove equivalent context. Co-tags are visible canonical names only, so noncanonical and held-out aliases cannot enter descriptions. The represented target is removed from co-tag context. Work language describes the work, not the tag's language.

Canonical names from all group splits remain candidates. Their direct canonical occurrences may supply support profiles, but calibration/test alias memberships are unavailable. This explicitly evaluates metadata available for a known candidate catalogue; it does not establish performance for entirely undescribed tag families.

Work counts, length bands, language counts and completion/restriction rates use the full eligible partition. Co-tag descriptions use up to 32 works per tag, selected by independent hash priority. Selection favors observed frequency, then smoothed association for ties. Per-description quotas are three fandoms, three characters, two relationships and four Freeform co-tags. Each work contributes at most 24 canonical co-tags of each type, selected by ascending ID. Co-tags require at least two sampled observations; numeric metadata requires at least three works. Missing word counts remain unknown.

**127,193 candidates** have support works. Among the 2,000 synonym queries, **1,643** have observed context and **798** have at least three context works. Fandom descriptions add query text for **778** aliases; broader co-tags for **949**; combined descriptions for **1,042**. Absent features fall back to the name/template, and every comparison retains the full query sample and candidate catalogue. Coverage strata also compare configurations on identical subsets. These profiles simulate recurring tags with observed history; a separate one-work condition tests context from a single work.

### Comparisons

| View | Text added to the tag name and `Type: Freeform` template |
|---|---|
| Raw / template controls | Raw name alone, then a consistent tagged template |
| Aliases | Up to five permitted alternative names on candidates |
| Fandom | Alias base plus observed fandom context |
| Co-tags | Alias base plus fandom, character, relationship and Freeform context |
| Length | Alias base plus percentages in short (<5k), medium (5k–<20k), long (20k–<100k) and epic (100k+) bands |
| Usage | Alias base plus a coarse tier of eligible-work prevalence; this is not reader engagement |
| Metadata | Alias base plus work-language codes, completion/restriction rates and observed rating/warning/category |
| Co-tags + length | Both additions together |
| All | Co-tags, length, usage and work metadata together |

Each of the seven context views is tested on candidates alone with a templated-name query, and with matching context on both sides. Additional conditions use a single observed query work with candidate co-tag profiles, and a name anchor: normalize `0.75 × normalized raw-name vector + 0.25 × normalized all-features vector` on both sides. This produces **19 semantic conditions** and **three lexical references**. The lexical method always scores individual names; extended prose is an embedding experiment.

The supplied [HTML fixture](tests/fixtures/ao3_work.html) demonstrates extraction of a summary, typed tags, work ID, dates, chapter counts, and engagement statistics. [Extracted metadata](results/ao3/stage2/html-fixture.json) records 65,871 whole-work words, 25/25 chapters, 534 kudos, 244 bookmarks, 103 comments and 21,909 hits, while the saved body contains only chapter 1. Its capture date is unknown. One work does not support a representative summary/engagement benchmark, and the CSV has no original work IDs for a direct join. **Summary and reader-engagement effectiveness remain unmeasured.** The parser is validated locally; no broader HTML corpus was collected.

### Results of the frozen comparisons

All values are percentages over the same 2,000 synonym queries. [metrics.csv](results/ao3/stage2/metrics.csv) includes Recall@1/3/5/10 and macro recall; [summary.json](results/ao3/stage2/summary.json) includes target-group bootstrap intervals and paired comparisons.

| Reference | Recall@1 | Recall@10 |
|---|---:|---:|
| Trigrams: canonical names | 50.25 | 70.35 |
| Trigrams: all globally permitted aliases | 65.85 | 84.80 |
| Trigrams: selected maximum-five aliases | 61.70 | 81.65 |
| Embeddings: raw names | 58.40 | 80.95 |
| Embeddings: `Tag: name` / `Type: Freeform` | **64.70** | **84.55** |
| Embeddings: concatenate selected aliases into the template | 48.40 | 78.35 |

The simple template adds **3.60 percentage points** to Recall@10 over raw names (paired 95% interval **2.19–5.02**). Concatenating alias examples then loses **6.20 points** against that template (**−7.89 to −4.51**). More correct alternative names do not automatically make a better single embedding.

All following text additions build on the concatenated-alias base. “Candidate only” keeps the templated-name query; “both” also gives the query its independently observed work profile.

| Addition | Candidate only R@1 | Candidate only R@10 | Both R@1 | Both R@10 |
|---|---:|---:|---:|---:|
| Fandom | 30.75 | 61.95 | 36.75 | 64.80 |
| Broader co-tags | 23.05 | 51.90 | 33.65 | 60.75 |
| Length distribution | 50.70 | 80.10 | 46.10 | 74.95 |
| Usage frequency | 36.00 | 67.35 | 37.60 | 67.65 |
| Work metadata | 38.00 | 70.55 | 42.00 | 72.60 |
| Co-tags + length | 25.30 | 55.85 | 34.90 | 62.90 |
| All additions | 20.15 | 47.00 | 33.90 | 59.45 |

Length on candidates recovers **1.75 points** relative to the concatenated-alias base (paired interval **0.44–3.04**), but still trails the simple name/type template. Co-tags and popularity prose substantially weaken this model's retrieval. Supplying context to both sides often recovers some of the loss from enriching candidates alone; it does not recover the name-template baseline.

This is not explained solely by missing query context. On the **866 identical queries with co-tag text available on both sides**, name templates achieve **84.30% Recall@10**, concatenated aliases **78.06%**, and both-sided co-tag descriptions **75.17%**. Among **743 queries with at least three works on each side**, templates score **86.54%** and all-feature descriptions **72.81%**. [Coverage strata](results/ao3/stage2/strata.csv) report every condition on the same subsets.

Using one observed query work with candidate co-tag profiles gives **33.60% R@1 / 54.40% R@10**. The fixed 75% raw-name / 25% all-features vector blend gives **63.30% / 85.85%**; its Recall@10 improvement over raw names is **4.90 points** (**3.87–5.94**). Keeping a strong name component mitigates the loss from long descriptions. This combined blend does not isolate the contributions of aliases and work context.

Our interpretation is that concatenated examples and shared fandom/context terms can move vectors away from the specific tag meaning. This is a hypothesis consistent with these ablations, not a demonstration that work metadata is intrinsically useless. Structured reranking or adjudication could use the same information differently.

### Exploratory follow-up: separate alias vectors

After observing the first three semantic results, a [separate protocol](results/ao3/stage2/alias-pooling-protocol.json) was frozen before acquiring any follow-up embeddings. It keeps the same model, queries, full catalogue and **48,080 selected permitted alias examples**, but embeds each name individually. A canonical receives the maximum cosine over its own name and its alias vectors, then occupies one shortlist position. This tests representation aggregation, with no work context or fitted weights.

| Separate-vector representation | R@1 | R@3 | R@5 | R@10 | R@10 95% CI | Macro R@10 |
|---|---:|---:|---:|---:|---:|---:|
| Raw canonical/alias names | 64.65 | 79.15 | 83.40 | 87.65 | 86.24–89.10 | 87.68 |
| **Templated canonical/alias names** | **73.15** | **86.05** | **88.85** | **92.05** | **90.84–93.26** | **91.95** |

The templated separate-vector configuration improves Recall@10 by **7.50 points** over the matching name-only template (**6.06–8.90**) and **10.40 points** over trigrams using exactly the same selected aliases (**8.71–12.02**). Its point improvement over Stage 1 raw-name embeddings is **11.10 points**. It retrieves the recorded target in the top ten for **1,841/2,000 queries**, leaving **159 misses**. [Follow-up results](results/ao3/stage2/alias-pooling.json) include paired intervals at every k.

The gain depends on alias coverage:

| Target support | Queries | Name template R@10 | Template + separate alias vectors R@10 |
|---|---:|---:|---:|
| At least one selected alias | 1,508 | 83.16 | **94.30** |
| No selected alias | 492 | **88.82** | 85.16 |

Additional alias vectors strengthen competing candidates and can hurt targets without aliases. This tradeoff is important for the frozen canonical-group holdout: its target families have no exposed alias examples. Keep that holdout intact; a separately frozen within-family alias holdout is needed to validate the use case of new variants of already described tags. These development results do not establish a final-test winner or an automation error rate.

**Next-stage recommendation:** carry forward the name/type template and separate alias vectors as the leading development configuration, retain the template-only control, and keep alias coverage explicit in evaluation. Do not append all work statistics to the primary retrieval embedding. Preserve those profiles for later structured-context experiments. Summaries and reader engagement remain pending acquisition of a suitable dated corpus; the single HTML fixture provides extraction evidence only.

### Cost and validation

The original experiment reused **183,067** Stage 1 vectors and acquired **842,596** new description vectors in **1,670** batches. Collection took about **13.2 minutes** and the provider reported **51,648,664 input tokens / US$1.03297328**. The follow-up acquired **96,160** vectors in **188** batches, reporting **740,833 tokens / US$0.01481666**. Total additional embedding cost was **US$1.04778994**. All requests used OpenAI through the same OpenRouter model alias, with no retries. This excludes local computation and any provider billing adjustments.

There are **54,000 predictions across 24 conditions**, including the two explicitly exploratory follow-up conditions. All **34 repository tests passed**. Independent validation checked **216,000** top-k membership decisions, verified input/vector/profile hashes, and found **zero** rank or top-ten mismatches when replaying both Stage 1 raw-name controls across all 2,250 queries. Among sampled profile works, **766,025 candidate-support records** and **9,407 query-context records** had zero overlap. [Main validation](results/ao3/stage2/validation.json) and the [follow-up report](results/ao3/stage2/alias-pooling.json) retain these checks.

Identity controls remain separate: raw names, templates, separate-alias-vector conditions and the name anchor retrieve all **250/250** identities at rank one. Concatenated aliases yield **92.40% R@1 / 98.80% R@10**; all-feature descriptions on both sides yield **79.20% / 91.60%**, another indication that descriptions can overwhelm name identity. Confidence intervals describe this development sample; the many comparisons have no multiplicity correction, and shared query works can introduce dependence beyond canonical groups.

## Jev selection on the best ten candidates

Human review and new labelling are deferred. This experiment uses the existing recorded merger targets for the frozen 2,000 Freeform development aliases. Every approach sees the same ten unique canonical candidates from `semantic/template_alias_max`, the strongest development Recall@10 configuration. Correct targets are never inserted into missed shortlists. The 250 identity controls are evaluated separately.

One incoming tag and its ten candidates form **one trial**. Its canonical matching success is 1 if the selected ID is the recorded target, otherwise 0. [Per-query predictions](results/ao3/jev-top10/predictions.csv) retain that outcome for each approach, alongside expected/selected names and IDs. The two main rates have different denominators:

```text
Selection accuracy when retrieved = correct selections / 1,841
Overall matching accuracy         = correct selections / 2,000
Overall matching accuracy         = 92.05% × selection accuracy when retrieved
```

The 159 retrieval misses therefore impose a **92.05% ceiling** on overall matching accuracy. A correct `none_of_these` decision on one of those misses is a useful shortlist rejection, but it does not recover the canonical tag. Rejection accuracy is reported separately and does not establish performance on naturally unmatchable tags.

The [protocol](results/ao3/jev-top10/protocol.json) freezes seven variants: generic name-only Choice; strict-equivalence name-only Choice; AO3-policy name-only Choice; AO3 policy with the permitted alias examples; the same alias condition with shuffled options; aliases plus observed co-tag context on both sides; and ten candidate-specific Noul questions followed by maximum-probability selection. The generic and pairwise variants force a candidate. The other five also allow `none_of_these`. A highest-non-none-probability diagnostic is recorded separately from each API's actual Choice.

Alias examples are exactly the selected maximum-five strings available to the winning retriever. All evaluated aliases and calibration/test aliases remain excluded from enrichment. Co-tags reuse the checked, masked Stage 2 work profiles; they describe association, not synonym labels. Definitions and summaries are absent and are not invented. Retrieval scores and explicit ranks are hidden, although retrieval-ranked presentation still supplies implicit ordering evidence. The shuffle uses a frozen per-query hash while preserving candidate keys and descriptions.

The main control is retrieval's first choice; additional controls rerank the same ten candidates using WRatio or normalized Levenshtein over names and permitted aliases. The [summary](results/ao3/jev-top10/summary.json) and [metrics](results/ao3/jev-top10/metrics.csv) keep incomplete Jev conditions null until all their requests are valid. Group-bootstrap intervals and paired differences use the same target grouping as the earlier stages. These are exploratory development results on a retrieval configuration selected using this same sample; calibration/test evaluation and automation thresholds remain future work.

### Results

All conditions are complete. The following table uses each API's returned Choice, or the maximum Noul for the pairwise condition. Abstention counts as failure to recover the canonical tag. Percentages describe the same 2,000 synonym queries, with 1,841 retrieved targets as the conditional denominator.

| Method | Correct | Selection when retrieved | Overall matching | Abstentions |
|---|---:|---:|---:|---:|
| Retrieval first choice | 1,463 | 79.47% | 73.15% | 0 |
| WRatio reranking, permitted aliases | 964 | 52.36% | 48.20% | 0 |
| Levenshtein reranking, permitted aliases | 924 | 50.19% | 46.20% | 0 |
| Jev generic names, forced Choice | 1,606 | 87.24% | 80.30% | 0 |
| Jev strict equivalence, names | 1,442 | 78.33% | 72.10% | 380 |
| Jev AO3 policy, names | 1,521 | 82.62% | 76.05% | 235 |
| **Jev AO3 policy + aliases** | **1,633** | **88.70%** | **81.65%** | **166** |
| Jev AO3 policy + aliases, shuffled | 1,613 | 87.62% | 80.65% | 191 |
| Jev AO3 policy + aliases + co-tags | 1,624 | 88.21% | 81.20% | 162 |
| Jev pairwise Noul + aliases, forced maximum | 1,488 | 80.83% | 74.40% | 0 |

The strongest returned-Choice condition improves overall matching by **8.50 percentage points** over retrieval, with a paired target-group 95% interval of **6.89–10.12 points**. It rescues **231** retrieval errors and introduces **61** regressions: `1,463 + 231 − 61 = 1,633`. Its overall 95% interval is **80.00–83.38%**, conditional interval **87.22–90.19%**, and macro overall accuracy **82.17%**.

Its 367 failures decompose into **159 retrieval misses**, **133 wrong selections with the target present**, and **75 unnecessary abstentions**. Among the 159 misses, it correctly chooses `none_of_these` in **91 (57.23%)** and accepts a wrong candidate in **68**. Those 91 rejections are useful but remain failed canonical matches. Counting them as successes would yield a different metric, 86.20% shortlist decision accuracy, not 86.20% matching accuracy.

The predeclared forced-selection diagnostic takes the highest reported probability among the ten real candidates, ignoring `none_of_these`. This is a decision rule applied to the existing eleven-option responses, **not a separately queried ten-option model condition**:

| Choice response used | Forced correct | Selection when retrieved | Overall matching |
|---|---:|---:|---:|
| Strict equivalence, names | 1,603 | 87.07% | 80.15% |
| AO3 policy, names | 1,596 | 86.69% | 79.80% |
| **AO3 policy + aliases** | **1,672** | **90.82%** | **83.60%** |
| AO3 policy + aliases, shuffled | 1,672 | 90.82% | 83.60% |
| AO3 policy + aliases + co-tags | 1,662 | 90.28% | 83.10% |

The ranked alias rule adds **209 correct mappings**, or **10.45 points** over retrieval (paired 95% interval **8.91–12.00 points**), from **247 rescues and 38 regressions**. Its overall 95% interval is **82.04–85.27%**. It recovers 39 of the alias Choice's 75 unnecessary abstentions, while also forcing incorrect matches on all 159 retrieval misses. Forced selection suits the present objective of maximizing successful canonical matches; it is not a validated rejection policy for incoming tags that may have no match.

### What changes the result

Permitted alias examples help: returned-Choice accuracy rises **5.60 points** over AO3 names alone (paired interval **4.28–6.99**). Strict equivalence abstains unnecessarily on **259** answer-containing lists. Its forced diagnostic recovers to 80.15%, so the low 72.10% headline should not be interpreted as a clean measure of prompt wording independent of rejection behavior.

Observed co-tags add **no clear improvement** over aliases: **−0.45 points**, interval **−1.44 to +0.59**. On the same **949 queries with rendered query co-tags**, aliases alone score **80.40%** and aliases plus co-tags **80.08%**. This supports retaining the simpler alias description for this experiment; it does not show that context is intrinsically useless.

The shuffled alias run changes **147/2,000 selections (7.35%)** and lowers returned-Choice accuracy by **1.00 point** (interval **−1.90 to −0.05**). The two forced rules tie at 83.60%. This is one deterministic shuffle with one response per condition, without repeated identical requests to estimate API variation; it is a limited order check. All comparisons are exploratory, with no multiplicity correction.

Alias support remains a material limit. For **1,508 queries whose target has selected aliases**, the alias Choice scores **84.81%**, versus **76.92%** for retrieval. For **492 targets without selected aliases**, it scores **71.95%**, versus **61.59%** for retrieval and **78.46%** for generic name-only Jev. These are diagnostic strata based on the known target, not an inference-time routing rule. The original canonical-group holdout has no exposed aliases for its target families, so this development winner must not be assumed to win that holdout. A separately frozen within-family alias holdout would test new variants of already described tags.

For example, the alias Choice rescues `five times fic` → `5 Times` and `washing` → `Bathing/Washing`. It also introduces errors: `Chicago - Freeform` should map to `Chicago (City)` but selects `One Chicago (Chicago Franchise)`, and `Stages of Love` should map to `Community: stagesoflove` but is rejected. Recorded AO3 wrangling conventions remain the reference, including community labels that are not obvious dictionary synonyms.

**Identity controls:** retrieval, both lexical controls, generic Choice, and strict Choice score **250/250**. AO3 names score 247/250; aliases and shuffled aliases 245/250; co-tags 244/250; pairwise Noul 215/250. Preserve an unambiguous exact-canonical-name fast path in any later application instead of unnecessarily sending these identities through adjudication. These controls are excluded from every synonym rate above.

### Execution and validation

The run captured **15,750 Jev requests** (seven variants × 2,250 queries) through OpenRouter, returning **`typesafe/jev-1.13-20260917`**, provider **TypeSafe**. The provider reported **17,581,617 input tokens**, **3,010,486 output tokens**, and **US$0.738427914**. Observed request latency was **0.284 seconds p50 / 0.380 seconds p95**, with up to 12 concurrent requests and two additional HTTP attempts. Costs are provider-reported; unsuccessful-attempt billing is not independently known.

In **11 of 13,500 Choice responses**, the returned choice differs from the highest reported probability. The [parser amendment](results/ao3/jev-top10/parser-amendment.json) records removal of an unsupported argmax assumption during acquisition. The primary analysis preserves Jev's returned choice; forced diagnostics use the reported probabilities. No prompt, candidate list, or raw response was changed. Returned probabilities have not been calibrated on this task.

All **60 repository tests pass**. [Independent validation](results/ao3/jev-top10/validation.json) checks **22,500 per-query outcomes**, **15,750 raw API responses**, all **2,250 original shortlists**, conditional/overall denominators, the rescue/regression identities, and disjoint error decomposition. Raw responses and order-sensitive request hashes support offline replay; the original calibration/test aliases remain unevaluated.

## Unmerged tags: review experiment (deferred)

The retrieval results support proposing matches, but do not establish whether an unmerged tag has any equivalent canonical. All previous synonym queries had a recorded target; even the best configuration missed 159 of them in its top ten. Low similarity, lack of a merger, and failure to retrieve a convincing candidate therefore cannot serve as novelty labels.

The [frozen review protocol](results/ao3/unmerged-review/protocol.json) samples the **166,244 visible unmerged Freeform tags** without using merger labels as negatives. It selects **400 tags by deterministic uniform hash sampling**, then screens **2,000 different tags** to select **100 challenge cases**. The random sample is split by a separate hash into 200 review-calibration cases and 200 review-validation cases. These are new tag-level review splits, separate from the original canonical-group holdouts; concept groups may cross the new splits and target overlap is reported after review.

The challenge arm contains 20 cases each with low pooled similarity, small pooled margin, disagreement between semantic first choices, and a template-only first choice without selected aliases, plus 10 high-usage and 10 non-ASCII cases. Selection is sequential and disjoint, with frozen ordering and fallback rules; all quotas were filled without fallback. Challenge results must remain separate from population estimates. Non-ASCII is a string property, not a language label, and this arm is a diagnostic sample rather than an exhaustive catalogue of difficult cases.

Both semantic methods retain the Stage 2 name/type template and embedding model. One uses canonical names alone; the other takes the maximum over canonical and individual permitted alias vectors. The lexical reference retains PostgreSQL `pg_trgm similarity` over raw canonical names and the same **48,080 selected aliases**. All three search the full **181,067-canonical Freeform catalogue** without approximate search or thresholds. Each returns ten unique IDs; reviewers see their union, initially in canonical-ID order with scores hidden. The local review page also searches the complete Freeform catalogue and the visible unmerged pool, so reviewers can find matches outside the shortlist.

Review evidence includes approximate usage, length distributions, work-language counts, and canonical co-tags. Query profiles use raw source-ID occurrences in the existing development-context work partition only; candidate profiles reuse the separate Stage 2 support partition. Co-occurrence is evidence about usage, not equivalence. These profiles are not embedded, and work counts cannot establish independent-author support. The HTML fixture is not used to infer missing summaries or reader engagement.

### Observations before reference review

| Observation | Random sample (400) | Challenge sample (100) |
|---|---:|---:|
| Semantic methods agree on first canonical | 262 (65.50%) | 42 (42.00%) |
| All three methods agree on first canonical | 93 (23.25%) | 9 (9.00%) |
| Template-only first canonical has no selected aliases | 235 (58.75%) | 76 (76.00%) |
| Query has observed work context | 308 (77.00%) | 62 (62.00%) |
| Query has at least three context works | 126 (31.50%) | 31 (31.00%) |
| Median approximate dump usage | 10 | 16 |

These are **retrieval and coverage observations, not accuracy estimates**. In particular, agreement is not proof of equivalence, and the alias-coverage statistic concerns retrieved first choices, not known correct targets. The 500 cases have evidence from 5,030 distinct context works. Sparse query context remains a practical constraint even though the overall works dump is large. [Preparation results](results/ao3/unmerged-review/summary.json).

### Reference decisions and evaluation

Each reviewer records one of `synonym`, `new_canonical`, or `unresolved`, with an identity, confidence, and rationale. A synonym needs an existing Freeform canonical ID that preserves the whole meaning. A new canonical needs a documented search beyond the shortlist; its proposed name is optional and can be chosen later. It is a proposal for this historical catalogue, not an AO3 policy decision. The [2026-10-04 review amendment](results/ao3/unmerged-review/review-amendment-2026-10-04.json) makes names optional for both reference judgments and system proposals, preserving the original acquisition protocol and sample. Ambiguous, composite, expressive, wrong-type, or insufficiently supported tags may remain unresolved. Candidate names that are broader or narrower than the source are not automatically synonyms.

The editable [judgments CSV](results/ao3/unmerged-review/judgments.csv) starts blank; system proposals have a [separate file](results/ao3/unmerged-review/proposals.csv). The generator preserves existing judgments on reruns. The local page saves drafts in browser storage and exports CSV plus a portable JSON backup. Independently review new-canonical decisions and disagreements before using them as final reference labels. Model-generated judgments can be a separate experimental condition, but cannot establish their own accuracy.

The evaluator reports shortlist recall for reviewed synonyms, precision and false merges among resolved reference decisions, proposal coverage, and proposals made on unresolved or unreviewed cases. Three decision baselines are fixed before review: always take the template first choice, always take the pooled first choice, and propose the pooled first choice only when the two semantic methods agree. They are deliberately simple baselines, not validated automation policies. A supplied proposal file additionally supports evaluating new-canonical **decisions**; proposed wording and usefulness need their own adjudication.

Tune future thresholds only on review calibration, then freeze the policy before evaluating review validation. Challenge cases are development diagnostics. The combined random sample can estimate the proportion of reference labels once review is complete; incomplete reviews may be biased by case difficulty or order. Unresolved cases are excluded from accuracy denominators and reported separately, never counted as correct or as known negatives. Wilson intervals are descriptive tag-level intervals; the small pilot cannot certify very rare false-merge rates. The [initial evaluation](results/ao3/unmerged-review/evaluation-all.json) correctly reports **zero reviewed cases and null accuracy**, rather than converting missing labels into failures or successes.

### Acquisition and checks

The experiment reused all candidate vectors and acquired **2,400 query vectors in five batches**, with **25,959 input tokens** and a reported cost of **US$0.00051918** through the same provider/model alias. No API retries were needed. All **48 repository tests passed**, including preservation of existing reviews, sampling disjointness, tie handling, required review evidence, and missing/unresolved metric denominators. The [validation report](results/ao3/unmerged-review/validation.json) checks 15,000 shortlist entries and confirms zero overlap between sampled query-context and candidate-support work records. Original calibration/test aliases remain unindexed and unevaluated.

## Limitations and remaining work

- **No-match and ambiguous ground truth.** The 221,190 visible unmerged noncanonical tags, including 166,244 Freeform tags, are unlabelled. A 500-tag Freeform review packet is prepared, but reference review is pending. Absence of a merger can reflect an unprocessed tag or an unclear concept; it does not prove that no equivalent canonical exists.
- **Definitions and a canonical hierarchy.** The dump contains names and merger links, not curated definitions or metatag/subtag edges.
- **Current labels and rare spelling coverage.** This is a historical, partially suppressed snapshot. Results describe the selected named subset and its recorded wrangling policy.
- **Final-test and downstream measurements.** Jev improves known-positive development matching on fixed shortlists, but final-test performance, fusion performance, natural no-match rejection quality, and automation precision remain unestablished.
- **Model provenance.** Canonical-group holdouts control local evaluation leakage; they cannot rule out the pretrained model having encountered public AO3 vocabulary. The API exposes an unversioned model alias, so captured vectors identify this specific run.

Complete the independent unmerged review before making claims about natural no-match rejection. A separate constructed stress test could deliberately remove complete known canonical families from the catalogue; keep that experiment distinct from naturally occurring no-match data.

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
| [Stage 2 protocol](results/ao3/stage2/protocol.json), [coverage](results/ao3/stage2/coverage.json), and [representations](results/ao3/stage2/representations.json) | Frozen conditions, work partitions, permitted support and feature availability |
| [ao3_representations.py](scripts/ao3_representations.py) | Build deterministic, work-disjoint profiles and render the frozen text views |
| [run_ao3_representations.py](scripts/run_ao3_representations.py) | Reuse raw-name vectors; cache new descriptions; score exact lexical/vector conditions |
| [analyze_ao3_representations.py](scripts/analyze_ao3_representations.py) | Independent profile/ranking checks, Stage 1 replay, paired metrics, coverage strata and diagnostics |
| [ao3_work_html.py](scripts/ao3_work_html.py) and [fixture extraction](results/ao3/stage2/html-fixture.json) | Local HTML extraction; whole-work statistics kept distinct from saved chapter content |
| [test_ao3_representations.py](tests/test_ao3_representations.py) | Leakage, work sampling, source integrity, length boundaries, vector normalization, batching and HTML extraction tests |
| [Stage 2 results](results/ao3/stage2/summary.json), [metrics](results/ao3/stage2/metrics.csv), and [validation](results/ao3/stage2/validation.json) | Full comparisons, paired estimates, coverage strata and independent checks |
| [run_ao3_alias_pooling.py](scripts/run_ao3_alias_pooling.py) and [follow-up results](results/ao3/stage2/alias-pooling.json) | Separately declared development follow-up comparing individual alias vectors |
| [Unmerged review instructions](results/ao3/unmerged-review/README.md), [sample](results/ao3/unmerged-review/sample.csv), and [preparation results](results/ao3/unmerged-review/summary.json) | Frozen 400 random / 100 challenge cases, three retrieval methods, context, and review workflow |
| [ao3_unmerged_review.py](scripts/ao3_unmerged_review.py), [ao3_review_html.py](scripts/ao3_review_html.py), and [analyze_ao3_review.py](scripts/analyze_ao3_review.py) | Reproducible acquisition, local catalogue search and labeling, and separate calibration/validation/challenge evaluation |
| [Jev protocol](results/ao3/jev-top10/protocol.json), [metrics](results/ao3/jev-top10/metrics.csv), and [summary](results/ao3/jev-top10/summary.json) | Seven Jev approaches on the best frozen top ten; conditional and overall accuracy, paired intervals, cost and latency |
| [Jev per-query predictions](results/ao3/jev-top10/predictions.csv) and [strata](results/ao3/jev-top10/strata.csv) | Success for each ten-candidate trial; separate identities, retrieval misses, selection errors, abstentions, rescues and regressions |
| [ao3_jev.py](scripts/ao3_jev.py), [run_ao3_jev.py](scripts/run_ao3_jev.py), and [validate_ao3_jev.py](scripts/validate_ao3_jev.py) | Label-isolated prompts, resumable Jev requests and metrics, and independent reconstruction from raw API evidence |
| [test_ao3_jev.py](tests/test_ao3_jev.py) | Label-boundary invariance, order-sensitive caching, response validation, and missing/retrieved/overall denominators |
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

To reproduce Stage 2 after Stage 1:

```bash
.venv/bin/python scripts/ao3_representations.py
.venv/bin/python scripts/ao3_work_html.py
OPENBLAS_NUM_THREADS=4 .venv/bin/python scripts/run_ao3_representations.py --phase all
OPENBLAS_NUM_THREADS=4 .venv/bin/python scripts/run_ao3_alias_pooling.py
.venv/bin/python -m unittest discover -s tests
```

New embeddings send the frozen tag descriptions and aggregate observed-work context through the same provider/model. They do not send HTML story text, expected query labels, work IDs, or held-out alias strings. Inputs are deduplicated and raw-name vectors are reused. `--offline` forbids new embedding requests. Phases `prepare`, `embeddings`, `semantic`, `lexical`, and `report` allow independent resumption; `--phase report` verifies caches and rebuilds the writeup's numbers without API or database access. Large profiles, input text, vectors and individual rankings are under gitignored `data/ao3-representations/`; reviewable methods, aggregate metrics and provenance are under `results/ao3/stage2/`.

The [earlier pilot writeup and plan](docs/archive/stackoverflow/README.md) are archived, and its original data, code, caches, and `results/stage1/` artifacts are preserved for provenance. The AO3 runner has its own adapter, cache, and results directory; it reuses only the disposable PostgreSQL helper from the earlier runner. Historical pilot scores are excluded from the active experiment's findings.

To reproduce the Jev experiment from completed Stage 2 caches:

```bash
.venv/bin/python scripts/run_ao3_jev.py prepare
source ~/.config/secrets/openrouter  # exports OPENROUTER_API_KEY on this workspace
.venv/bin/python scripts/run_ao3_jev.py run --workers 12
.venv/bin/python scripts/run_ao3_jev.py analyze
.venv/bin/python scripts/validate_ao3_jev.py
.venv/bin/python -m unittest discover -s tests
```

`prepare` and `analyze` are offline; only `run` sends API requests. On another machine, export `OPENROUTER_API_KEY` using your own secret storage. The runner resumes valid caches without repeating paid requests. `--limit 4` runs a small schema check; its incomplete conditions retain null full-sample accuracy. `--conditions` selects named frozen conditions for acquisition, while reporting continues to expose any missing conditions. Each request has one incoming tag and ten real canonical candidates; five Choice variants add an eleventh `none_of_these` option. The pairwise variant asks ten Noul questions in one request. Request hashes preserve option ordering, and raw responses retain the returned model revision, provider, usage and request ID. No API credential is stored in artifacts. The [Decisions API reference](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request) documents the request and response schema.

To reproduce the deferred unmerged review packet from completed Stage 2 caches:

```bash
OPENBLAS_NUM_THREADS=4 .venv/bin/python scripts/ao3_unmerged_review.py --offline
.venv/bin/python scripts/analyze_ao3_review.py --partition calibration
```

The first acquisition omits `--offline`; only templated public query names go to the embedding API. The checked-in sample, candidate lists, protocol and aggregate results are under `results/ao3/unmerged-review/`. Larger evidence, vectors, and the generated review page are under gitignored `data/ao3-unmerged-review/`. Open `review.html` with `review-data.js` in the same directory. See the [review instructions](results/ao3/unmerged-review/README.md) for saving judgments and evaluating a frozen policy.
