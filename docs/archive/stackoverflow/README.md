> Archived pilot, superseded on 2026-10-03. These methods, dataset counts, and results belong to the earlier experiment. The active experiment now uses AO3; see the [current README](../../../README.md) and [current plan](../../../plan.md). Commands in this archive assume the repository root.

# Tag canonicalisation: retrieval and Jev experiment

This is the live experimental writeup. It records completed work, frozen inputs, measured results, and the questions that remain. [plan.md](plan.md) contains the full experimental sequence.

**Status, 2026-10-03:** Stage 1, Establish Simple Baselines, is complete on the development split. Among methods using canonical names alone, `text-embedding-3-small` retrieved the correct target in its top ten for **876/1,033 synonyms (84.80%)**, and PostgreSQL trigram similarity did so for **847/1,033 (81.99%)**. The paired uncertainty interval for that difference includes zero. Each method retrieves cases missed by the other, supporting a later fusion experiment. Jev has not been evaluated.

## Research question

Can a combination of lexical retrieval, semantic retrieval, and a final adjudicator map an incoming tag to its canonical equivalent accurately enough to automate useful work?

We distinguish three questions throughout:

1. **Retrieval:** does the correct canonical target enter the candidate list?
2. **Adjudication:** when the target is present, does the decision layer select it?
3. **Automation:** which mappings can be accepted at a validated error rate?

Stage 1 measures retrieval and deterministic exact matching. A retrieved neighbour is not necessarily an equivalent concept, and Recall@10 does not measure the precision of automatic mappings.

## Dataset and ground truth

The frozen input is the original Stack Overflow API sample, `stackoverflow-7d95f64002496ef5`, captured on 2026-10-03. It comprises 25 pages each from `/tags` and `/tags/synonyms`, with 100 records per page. The requested sorts were popularity and synonym application count. Both final pages indicated more data remained; this is a bounded, non-uniform sample.

| Component | Count |
|---|---:|
| Returned tag records | 2,500 |
| Distinct tag names | 2,476 |
| Canonical candidates after excluding 22 known synonym sources | 2,454 |
| Distinct synonym mappings | 2,500 |
| Synonyms with a resolved target in the catalogue | 1,770 |
| Distinct canonical targets represented by those synonyms | 1,049 |
| Synonyms with a resolved target outside the catalogue | 730 |
| Canonical-name identity examples | 2,454 |
| Total benchmark examples | 4,224 |

Stack Overflow's synonym mappings supply the labels. Chains are resolved before checking membership. For example, `team-foundation-server -> tfs -> azure-devops-server` is excluded because the final target is absent, even though `tfs` occurs in the sampled tags. This accounts for the difference between a naive direct-target join of 1,771 mappings and the usable count of 1,770.

The sample contains raw names and metadata, with no curated definitions or observed question context. Every benchmark row is `matchable`. The 730 excluded synonyms have known upstream targets; they have not been relabelled as real-world no-match examples. The separate April 2024 historical catalogue is not part of this run.

Source artifacts: [tags.csv](../../../data/stackoverflow/tags.csv), [synonyms.csv](../../../data/stackoverflow/synonyms.csv), [experiment.csv](../../../data/stackoverflow/experiment.csv), and [manifest.json](../../../data/stackoverflow/manifest.json). The runner checks all three CSV hashes against the manifest before scoring.

## Evaluation protocol

Canonical targets define groups. The existing SHA-256 split with seed `tag-matching-jev-v1` keeps all examples for a canonical target in the same development, calibration, or test split.

| Split | Identity examples | Synonym examples | Canonical groups with synonyms | Stage 1 use |
|---|---:|---:|---:|---|
| Development | 1,420 | 1,033 | 618 | Evaluated |
| Calibration | 517 | 369 | 225 | Reserved for later calibration |
| Test | 517 | 368 | 206 | Reserved for final locked evaluation |

All **2,454 canonical names** remain available as retrieval candidates across splits. This includes candidates with no sampled synonyms. Calibration and test queries are neither embedded nor scored in Stage 1; their canonical names remain available because they define the shared catalogue.

The primary comparison uses canonical names only. A secondary lexical condition adds the 1,033 development aliases, with the evaluated alias removed for each query. Other aliases for that same target may remain. Alias scores are combined by taking the maximum score per canonical target before ranking. Calibration and test aliases are never added. This secondary condition measures retrieval when other aliases are already known; it differs from retrieval against names alone and from holding out an entire target's aliases.

For each synonym, Recall@K is one if its labelled canonical target appears among the first K distinct candidates, and zero otherwise. We report K = 1, 3, 5, and 10. All ranked methods score the entire catalogue, with no threshold pruning, approximate index, or exact-match boost. Equal scores are ordered by canonical name, ascending. Identity examples are scored and reported separately.

Macro recall gives each canonical target equal weight after averaging its aliases. Approximate 95% intervals use 2,000 bootstrap resamples of canonical groups, seed `20261003`; all aliases from a sampled group move together. Paired comparisons use the same examples and group resamples. These development intervals are exploratory, unadjusted for multiple comparisons, and do not remove API sampling bias.

## Stage 1 methods

### Exact matching and normalization

We evaluated four cumulative name transformations: unchanged text; case folding; case folding plus trimmed/collapsed whitespace; and conservative normalization of internal underscores, whitespace, and Unicode dashes to ASCII hyphens. The final rule preserves `+`, `#`, `.`, and leading punctuation, keeping `c`, `c++`, `c#`, and `.net` distinct. A normalized lookup mapping to multiple canonical targets abstains.

Each transformation was tested both against canonical names alone and against canonical names plus permitted development aliases. An evaluated alias is masked before normalization. Consequently, the exact-alias condition cannot retrieve the answer merely by consulting that example's ground-truth mapping.

### Lexical retrieval

We ran the actual PostgreSQL **18.6** implementation of **pg_trgm 1.6**, evaluating `similarity(query, candidate)`, `word_similarity(query, candidate)`, and `strict_word_similarity(query, candidate)`. The query is always the first argument; the word functions are directional. PostgreSQL ignores non-alphanumeric characters in trigram extraction. [PostgreSQL documentation](https://www.postgresql.org/docs/18/pgtrgm.html).

We also evaluated RapidFuzz **3.14.6** normalized Levenshtein similarity with unit edit costs, and `WRatio` with `processor=None`. These scorers use the original tag strings; the exact-matching normalization variants are separate experiments. [Levenshtein documentation](https://rapidfuzz.github.io/RapidFuzz/Usage/distance/Levenshtein.html), [WRatio documentation](https://rapidfuzz.github.io/RapidFuzz/Usage/fuzz.html).

PostgreSQL runs in a disposable local cluster with TCP disabled. It supplies exhaustive trigram scores, which are cached. The full persistent database schema proposed in the plan remains future work.

### Semantic retrieval

The semantic baseline follows [the supplied example](../../../example_api/openai_embedding.py): `openai/text-embedding-3-small` through OpenRouter, with 1,536 dimensions and raw tag names on both sides. No descriptions, context, instructions, aliases, or enrichment are appended to canonical names. The response reported model `text-embedding-3-small` and provider `OpenAI`.

We explicitly normalize vectors to unit length and compute exhaustive cosine similarity using NumPy in float64. Cosine is the model's documented recommended similarity measure. [OpenAI embedding documentation](https://developers.openai.com/api/docs/guides/embeddings). Requests use the OpenRouter embeddings endpoint and preserve response indices when matching vectors to inputs. [OpenRouter API reference](https://openrouter.ai/docs/api/api-reference/embeddings/submit-an-embedding-request).

There are 3,487 distinct input strings: 2,454 canonical names and 1,033 development synonyms. Identity queries reuse the same string's cached vector. Vectors and request metadata are stored in batches with checksums; source names, model settings, and input hashes determine the cache directory. The hosted model is an unversioned alias, so the cached vectors freeze this run. Semantic search currently uses NumPy; pgvector storage and indexing have not been implemented.

## Stage 1 results

### Primary comparison: canonical names only

All percentages below concern the **1,033 development synonyms**, representing **618 canonical targets**.

| Method | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Macro Recall@10 |
|---|---:|---:|---:|---:|---:|
| Exact lookup, every tested normalization | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% |
| pg_trgm `similarity` | 57.50% | 73.96% | 78.80% | 81.99% | 84.83% |
| pg_trgm `word_similarity` | 45.89% | 66.51% | 72.51% | 77.93% | 81.06% |
| pg_trgm `strict_word_similarity` | 46.95% | 67.86% | 74.25% | 79.57% | 83.17% |
| Normalized Levenshtein | 40.95% | 53.92% | 60.89% | 67.76% | 71.70% |
| RapidFuzz `WRatio` | 39.30% | 63.99% | 70.96% | 75.90% | 79.76% |
| `text-embedding-3-small`, cosine | **62.54%** | **75.90%** | **80.93%** | **84.80%** | **86.06%** |

Trigram `similarity` is the strongest lexical method at every measured cutoff. Embeddings have the highest observed recall among the primary baselines. At K=1 the counts are 646 correct for embeddings and 594 for trigram similarity; at K=10 they are 876 and 847.

| Embeddings minus trigram similarity | Observed difference | Paired 95% group-bootstrap interval |
|---|---:|---:|
| Recall@1 | +5.03 percentage points | +1.11 to +8.90 |
| Recall@3 | +1.94 percentage points | −1.71 to +5.49 |
| Recall@5 | +2.13 percentage points | −1.06 to +5.30 |
| Recall@10 | +2.81 percentage points | −0.10 to +5.69 |

The top-one difference is supported by this exploratory interval. The larger-shortlist intervals include zero. Individual Recall@10 intervals are 81.82–87.39% for embeddings and 78.89–85.20% for trigram similarity. We retain both retrieval approaches for subsequent stages.

### Exact matches and identity controls

All four exact-matching variants correctly resolve **1,420/1,420 identity queries**, with zero incorrect mappings or normalization ambiguities. They resolve **0/1,033 held-out synonyms**, even when other development aliases are permitted. Thus deterministic exact matching covers **57.89% of the combined development benchmark**, entirely through its identity examples. The normalization changes add no coverage on this already standardized sample; that result does not establish how they behave on noisy user input.

| Standalone method, canonical names only | Identity Recall@1 | Identity Recall@10 |
|---|---:|---:|
| Exact lookup | 100.00% | 100.00% |
| pg_trgm `similarity` | 99.86% | 100.00% |
| pg_trgm `word_similarity` | 94.30% | 100.00% |
| pg_trgm `strict_word_similarity` | 94.37% | 100.00% |
| Normalized Levenshtein | 100.00% | 100.00% |
| RapidFuzz `WRatio` | 100.00% | 100.00% |
| Embeddings | 100.00% | 100.00% |

Trigram similarity ranks `c#` second and `c++` third for their own identity queries: both tie with `c` at a score of 1, and alphabetical ordering places `c` first. Word similarity also gives perfect scores to some longer candidates containing the query, causing further ties. A later composed pipeline should preserve an exact-name fast path. These standalone baseline results retain the tie behaviour so it remains visible.

### Secondary comparison: other development aliases available

The evaluated alias is hidden, and each canonical target occupies one candidate position. These results still use the same 1,033 development synonym queries.

| Method | Recall@1 | Recall@3 | Recall@5 | Recall@10 |
|---|---:|---:|---:|---:|
| pg_trgm `similarity` | 67.67% | 82.19% | 86.25% | **89.45%** |
| pg_trgm `word_similarity` | 53.05% | 73.96% | 80.64% | 85.38% |
| pg_trgm `strict_word_similarity` | 55.86% | 75.02% | 82.19% | 87.12% |
| Normalized Levenshtein | 54.02% | 67.86% | 72.89% | 78.99% |
| RapidFuzz `WRatio` | 45.89% | 72.22% | 79.77% | 85.58% |

Trigram similarity reaches 924/1,033 at K=10, 77 more than with canonical names alone. Other known aliases can help materially. This condition has additional labelled information, so its 89.45% should not be interpreted as a like-for-like victory over the names-only embedding result.

### Complementarity and errors

At K=10, embeddings and trigram similarity both retrieve the target for 779 synonyms. Embeddings alone retrieve another 97, trigram similarity alone retrieves another 68, and both miss 89. Their union contains the target for **944/1,033 (91.38%)**, but may contain up to **20 candidates**. This overlap calculation motivates fusion; it is not a measured Recall@10 result for a fused ranking.

The following development examples illustrate different failure modes. Ranks refer to the expected target within the complete 2,454-candidate ranking.

| Incoming tag | Labelled target | Trigram rank | Embedding rank |
|---|---|---:|---:|
| `amd64` | `x86-64` | 63 | 1 |
| `as3` | `actionscript-3` | 195 | 1 |
| `activity` | `android-activity` | 1 | 173 |
| `android-performance` | `performance` | 1 | 938 |
| `ai` | `artificial-intelligence` | 335 | 214 |
| `aws` | `amazon-web-services` | 309 | 199 |

Embeddings recover some aliases with little lexical overlap, but isolated short tags and platform-specific mappings remain difficult. Semantic relatedness also does not guarantee the platform's canonical equivalent. The examples are illustrative selections, not a separately labelled or statistically evaluated difficulty subset.

## Cost, execution, and artifacts

The embedding collection made **28 requests** for **7,865 reported input tokens**, with **$0.0001573 in API-reported cost**. Requests took 46.14 seconds in total. Raw vector lengths ranged from 0.99934 to 1.00069 before explicit normalization. Input strings were deduplicated, and cached reruns make no embedding requests.

The original PostgreSQL scoring phase took 32.85 seconds for all three scorers over all development queries and available representations. The completed run's exact semantic matrix computation and ranking took approximately 0.39 seconds with vectors already in memory. These are batch experiment measurements with different scopes and overheads; they are not a comparison of per-request production latency. Timing metadata accompanies the results and will vary on reruns.

| Artifact | Purpose |
|---|---|
| [metrics.csv](../../../results/stage1/metrics.csv) | All raw hit counts, micro/macro recall, intervals, methods, and candidate policies |
| [summary.json](../../../results/stage1/summary.json) | Dataset/source hashes, versions, exact-match outcomes, paired comparisons, embedding provenance, and timing metadata |
| [validation.json](../../../results/stage1/validation.json) | Independent audit of all saved predictions, metric rows, input membership, and report checksums |
| [Predictions](../../../data/baselines/stage1/06e7afc439601d3f1cfe/predictions.jsonl.gz) | 46,607 per-example records: 19 method/policy configurations × 2,453 development examples, with target rank and up to ten candidates |
| [Embedding cache](../../../data/baselines/embeddings/acdc00c9b9715901783e/) | Frozen input strings, vector batches, checksums, provider usage, and capture metadata |
| [Baseline runner](../../../scripts/run_stage1_baselines.py) | Exhaustive lexical and semantic evaluation and report generation |
| [Embedding collector](../../../scripts/cache_stage1_embeddings.py) | Resumable API collection and offline cache validation |

Large dataset, PostgreSQL score, and vector files stay under the ignored `data/` directory. The README, scripts, dependency pins, and compact result files are retained in the repository. Anyone reproducing this exact run needs the frozen dataset and caches; fetching a new API sample or regenerating vectors from an unversioned model creates a different observation.

## Reproduction and validation

The recorded environment uses Python **3.11.16**, NumPy **2.4.6**, RapidFuzz **3.14.6**, Psycopg **3.3.6**, PostgreSQL **18.6**, and pg_trgm **1.6**. PostgreSQL executables (`initdb`, `pg_ctl`, `postgres`) and the pg_trgm extension must be installed. A new scoring run starts and stops its own temporary cluster and requires permission to create a local Unix socket. It does not connect to an existing database.

From the repository root, with the frozen `data/stackoverflow/` snapshot present:

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

For a first embedding collection, provide `OPENROUTER_API_KEY` through the environment, then run:

```bash
.venv/bin/python scripts/cache_stage1_embeddings.py
.venv/bin/python scripts/run_stage1_baselines.py --offline
```

With the frozen embedding and score caches already present, the second command reproduces the metrics without network access or starting PostgreSQL. To run the lexical portion independently:

```bash
.venv/bin/python scripts/run_stage1_baselines.py --lexical-only --output results/stage1-lexical
```

The runner always evaluates development examples. Use `--output` for a separate report directory and `--cache` / `--embedding-cache` for separate cache roots. Preserve the recorded artifacts when changing the dataset or experimental configuration. A fresh embedding cache can incur new API charges and may return different vectors.

The ten automated tests passed. They cover technical punctuation, normalization collisions, query-alias masking, retention of permitted other aliases, canonical deduplication, deterministic ties, macro weighting, response-index ordering, invalid vectors, and exclusion of calibration/test queries. An independent audit checked all 46,607 prediction records, recomputed raw hits and micro/macro metrics for all 38 report rows, checked CSV/JSON agreement, and verified that embedding inputs contain exactly the catalogue plus development queries. The audit passed and records the validated report hashes in `validation.json`.

## Interpretation and next stages

Stage 1 establishes a useful lexical baseline in `pg_trgm similarity` and an initial semantic baseline in `text-embedding-3-small`. The methods have different errors. Exact canonical lookup remains valuable for technical tags, and known aliases are a separate source of useful information.

The next planned stage is representation testing, temporarily keeping one lexical method and one embedding model fixed. Definitions and observed context need to be collected with provenance before those comparisons can run. Later stages will refine retrieval, fuse candidate lists, compare decision baselines, and measure Jev's incremental contribution on identical candidate lists.

No automation threshold has been calibrated, and no claim about safe production mapping follows from this positive-only sample. All current findings are development results on a bounded catalogue. The eventual 368-synonym test split is small for rare-error claims, and neither no-match nor ambiguous-case false acceptance can yet be measured. Those evaluations require additional labelled cases and the final locked protocol in the plan.
