# AO3 Tag Canonicalisation Experiment Plan

## Goal and current status

Evaluate lexical retrieval, semantic embedding retrieval, candidate fusion, and optional Jev adjudication for AO3 canonicalisation. Separate candidate recall, final selection, and the decision to automate a mapping.

**Active dataset:** the local AO3 selective dump under `data/ao3-dump/`, with filenames dated 2021-02-26. The [README](README.md) is the live writeup. Dataset analysis, the [frozen benchmark export](results/ao3/benchmark/manifest.json), the AO3 ID/type adapter, and **Stage 1 on the frozen Freeform development sample are complete**. See the [AO3 results](results/ao3/stage1/summary.json). No previous dataset's scores carry over.

## Dataset decision — adopt AO3

The audited tags table contains **14,467,138 unique tag IDs**, of which **1,621,975 have visible names**. `Redacted` occurs on **12,845,163 rows** and is a missing-name marker. There are **1,175,987 named canonical candidates without an outgoing merger**, and **224,744 usable named synonym mappings to 105,340 canonical targets** after integrity checks.

| Type | Canonical candidates | Usable synonyms | Targets with usable synonyms |
|---|---:|---:|---:|
| Freeform | 181,067 | 116,643 | 46,901 |
| Character | 327,424 | 47,646 | 28,888 |
| Relationship | 625,122 | 45,219 | 24,592 |
| Fandom | 42,344 | 15,236 | 4,959 |
| ArchiveWarning, Category, Media, Rating, UnsortedTag combined | 30 | 0 | 0 |

This supports a substantial retrieval/adjudication study with natural-language aliases, structured types, and optional observed co-tag context. Start with **Freeform** as the primary semantic canonicalisation task; add the other three large types as separate evaluations. Do not let identity controls or large relationship vocabularies dominate a combined score.

### Input and integrity policy

- Keep both downloaded CSVs unchanged and record their SHA-256 hashes. The file date and the analysis date are different provenance fields; these are historical labels.
- Retain `id`, `type`, raw `name`, `canonical`, `cached_count`, and direct `merger_id`. Use SQL `NULL` for absent IDs/counts; use a separate flag for suppressed names.
- Canonical candidates require a visible name, `canonical=true`, and no outgoing merger.
- Positive queries require a visible noncanonical source, a merger resolving to a named candidate, and matching source/target type. Keep direct and resolved target IDs separately.
- Quarantine 51 canonical rows with merger links and paths reaching those conflicts. The graph also contains 20 references to missing IDs and one merged path ending at a noncanonical node. No cycles or self-mergers were found. Seven valid chains need two hops.
- Of 224,747 visible noncanonical rows with a merger, one reaches a missing target and two reach canonical/merger conflicts; the remaining 224,744 are eligible. No eligible named mapping crosses types.
- Do not infer visibility from `cached_count`: 96,873 redacted rows have counts at least five, while 849,932 named canonical rows have counts below five. Use the actual name marker; counts are approximate.
- Keep the 221,190 visible noncanonical rows without a merger as unlabelled data. They need a separate adjudication process before they can support no-match or ambiguous-case evaluation.

### Frozen split and initial scope

Use canonical IDs as the grouping unit. The frozen rule hashes `tag-matching-jev-ao3-v1:canonical_id`, takes the first 64 bits modulo ten, and assigns buckets 0–5 to development, 6–7 to calibration, and 8–9 to test. Source aliases and identity controls follow their resolved canonical group.

| Freeform split | Eligible synonyms | Canonical groups with synonyms |
|---|---:|---:|
| Development | 70,294 | 28,205 |
| Calibration | 24,943 | 9,394 |
| Test | 21,406 | 9,302 |

These are the frozen population split counts. Stage 1 takes 2,000 development aliases in SHA-256 order under seed `tag-matching-jev-ao3-freeform-stage1-v1`, skipping targets after three aliases. The sample covers 1,761 groups. Separately select 250 development canonical identity controls with the seed's `:identity` suffix. The [manifest](results/ao3/benchmark/manifest.json) fixes the exact rules before scoring and records the visible-name/capped-family sampling bias. All 181,067 eligible Freeform canonical names remain available across splits. Keep calibration and test for their designated later stages.

Any catalogue reduction is a separate experimental condition: adding or removing distractors changes recall. Never retain only the correct targets of selected queries and report that as full-catalogue performance.

### Context and leakage

The works CSV provides associated tag IDs and metadata. It does not provide text descriptions or a canonical hierarchy. A work's co-tags are observed context, not a definition, synonym label, or assertion that two concepts are equivalent.

Use `(works_file_hash, CSV_record_ordinal)` as a local work identifier if contexts are materialized. Mask the query, all equivalents of its expected target, and held-out aliases before constructing prompts or descriptions. Define work-level separation as well as canonical-group separation so a work's context is not reused across splits. Keep the tag-only benchmark as the primary baseline.

## AO3 Stage 1 implementation

1. **Complete:** export a frozen AO3 catalogue and positive-pair pool using audited ID/type rules, with checksums, exclusions, sampling policy, and splits in the manifest.
2. **Complete:** implement a separate AO3 loader using source IDs and tag types, with integrity checks.
3. **Complete:** freeze the initial Freeform development sample; report identities separately and mask evaluated aliases in every retrieval path.
4. **Complete:** implement batched exhaustive lexical/vector ranking with numeric-ID tie breaks and resumable rank checkpoints. A dense matrix for all 70,294 development aliases against 181,067 canonical names would have about 12.73 billion cells, around 101.8 GB in float64; the runner does not allocate it.
5. **Complete:** evaluate Stage 1 below with one embedding model and record AO3-only measurements, paired intervals, and limitations in the live README.

### Proposed persistent schema

PostgreSQL with `pg_trgm` and pgvector remains the intended serving backend. The extension name for pgvector is `vector`. Stage 1 uses a disposable PostgreSQL 18.6 cluster with `pg_trgm` 1.6 and local embedding files with exact NumPy search. Persistent AO3 schema/imports and pgvector indexes remain later work; pin versions before starting them.

| Table | Key and main fields | Purpose |
|---|---|---|
| `dataset_snapshot` | `snapshot_id`, file dates, source paths, SHA-256 hashes, audit/benchmark manifests | Immutable source and filtering provenance |
| `source_tag` | `(snapshot_id, source_tag_id)`, type, raw name, redaction flag, canonical flag, approximate count, direct merger ID | Preserve every source record, including quarantined/unlabelled rows |
| `canonical_tag` | `(snapshot_id, canonical_id)`, type, name, versioned normalized name | Named candidates satisfying the integrity policy |
| `tag_synonym` | `(snapshot_id, source_tag_id)`, direct merger ID, resolved canonical ID, resolution status/hops | Ground-truth edges and exclusion reasons |
| `benchmark_group` | `(snapshot_id, canonical_id)`, split | Keep target families together |
| `benchmark_example` | example ID, snapshot, source tag ID, expected canonical ID, type, kind, label, group | Frozen identity and synonym queries |
| `work_record` / `work_tag` | snapshot, source record ordinal, metadata; associated source tag IDs | Optional observed co-tag context and work-level holdouts |
| `embedding_configuration` | model/provider/revision, dimensions, metric, normalization, input template, mask policy | Version the complete embedding recipe |
| `canonical_embedding` / `query_embedding` | snapshot, entity ID, configuration, input checksum, dimensions, vector | Cache vectors without crossing snapshot/configuration boundaries |

Use composite foreign keys including the snapshot. Preserve raw names, detect normalization collisions, and allow identical normalized names to point to multiple IDs. Do not use names as relational identity. Enforce source/target type agreement in the primary benchmark; record optional cross-type tasks separately.

For lexical retrieval, score every candidate within the declared type and use stable ID tie breaks; do not prune with the `%` threshold operator during the recall study. `pg_trgm` discards non-alphanumeric characters, so relationship punctuation needs specific tests and an exact-name control. [PostgreSQL documentation](https://www.postgresql.org/docs/18/pgtrgm.html).

For embeddings, cache both sides with the same recipe and use exact search initially. Keep snapshot/configuration filters explicit. Batch matrix computation or database scans so memory stays bounded. HNSW/IVFFlat and serving optimizations belong to the later efficiency stage.

---

# 0. Define the Task and Benchmark

Before comparing models or retrieval methods, define what counts as a correct mapping.

## 0.1 Define canonical equivalence

The primary task is to recover AO3's recorded canonicalisation policy in the frozen dump. Resolve `merger_id` to a named, internally consistent canonical tag and use its ID as the expected answer. These are platform-defined labels, which can reflect wrangling conventions beyond literal dictionary synonymy.

Do not substitute a merely related tag, infer a synonym from co-occurrence, or treat a metatag/subtag relationship as an equivalence label. This dump does not contain the metatag graph. For later adjudication, check that the prompt's equivalence rule agrees with the intended AO3 label policy; evaluate generic strict semantic equivalence separately if that is a different objective.

Tag type is an observed input field. The initial experiment retrieves within that type. Inferring the type from an unknown tag string is a separate task and requires an untyped evaluation.

## 0.2 Define benchmark categories

Label examples as one of:

### Matchable
A correct canonical tag exists in the catalogue.

### No match
The intended concept is clear, but the catalogue does not contain an equivalent canonical tag.

### Ambiguous
The available tag/context does not provide enough evidence to identify one canonical concept reliably.

Keep these categories separate in evaluation.

## 0.3 Avoid leakage

For held-out synonym tests:

- keep `merger_id`, resolved target IDs/names, and canonical-status labels out of query embeddings and adjudicator inputs; the initial incoming representation is the raw source name and its observed type;
- remove the tested synonym from alias lookup;
- do not include the held-out synonym in generated canonical descriptions;
- do not let the enrichment process see the answer;
- keep related examples from the same source group together when splitting data.

Where possible, maintain:

- development set;
- calibration set;
- untouched final test set.

## 0.4 Benchmark notes for AO3

- Use numeric source and target tag IDs, scoped to the frozen snapshot; preserve names and types as attributes.
- Exclude the literal `Redacted`, blanks, invalid targets, and canonical/merger conflicts from named positive examples.
- A noncanonical tag without a merger is unlabelled. It is not automatically a negative or ambiguous example.
- Preserve meaningful punctuation, especially `/`, `&`, `|`, and parenthetical fandom qualifiers. Do not strip diacritics or reorder relationship participants by default.
- Keep `Freeform`, `Character`, `Relationship`, and `Fandom` results separate. The small controlled types supply identity controls but no synonym positives in this dump.
- Distinguish tag-only input from observed work co-tags. Work metadata contains no title, summary, body text, or original work ID.
- Before using a work's co-tags, remove the query tag, the expected canonical target, and all tags resolving to that target; also exclude held-out aliases from retrieval and enrichment. Prevent the same work record from supplying contexts across splits.
- Report performance by usage range, name length, script/language where actually established, punctuation, and lexical difficulty. Work language alone is not a tag-language label.
- Keep an untouched test set. Dataset auditing and split counts do not constitute evaluation.

---

# 1. Establish Simple Baselines

**Complete for the initial AO3 Freeform development pilot.** Name-only Recall@10 is 70.35% for the best lexical method (`pg_trgm similarity`) and 80.95% for `text-embedding-3-small`; the paired improvement is 10.60 points (95% group-bootstrap interval 8.57–12.65). Trigram retrieval with other development aliases reaches 85.00% under the separate alias-permitted condition. Results are under `results/ao3/stage1/` and summarized in the [README](README.md). Calibration/test evaluation and Jev adjudication remain later stages.

Do not use Jev yet.

The purpose of this stage is to understand how much of the task can already be solved with simple methods.

## 1.1 Exact normalised matching

Test:

- exact canonical-name matching;
- exact alias matching;
- case folding;
- whitespace normalisation;
- safe punctuation normalisation.

Avoid destructive normalisation that collapses AO3 distinctions such as `/` versus `&` in relationship tags, removes `|` name variants, or drops parenthetical fandom qualifiers. Keep raw names and record normalization collisions; ambiguous normalized lookups must abstain.

Record the percentage of the benchmark solved deterministically.

## 1.2 Lexical baselines

Compare at least:

### PostgreSQL trigram similarity

Using `pg_trgm`, evaluate:

- `similarity`
- `word_similarity`
- `strict_word_similarity`

### Edit-distance based matching

For example:

- normalised Levenshtein distance;
- RapidFuzz scorers such as `WRatio`.

For each incoming tag, compare against canonical names and permitted aliases.

Aggregate candidates by canonical tag ID so that one canonical tag with many aliases does not occupy several shortlist positions.

## 1.3 Initial semantic baseline

Use one embedding model with:

- incoming representation: tag name only;
- canonical representation: canonical name only;
- exact vector search;
- the model's recommended similarity measure.

Measure:

- Recall@1
- Recall@3
- Recall@5
- Recall@10

At this stage, do not optimise for final mapping accuracy. The main question is:

> Does the correct canonical tag appear in the candidate set?

---

# 2. Test Text Representation

**Next stage; not yet run.** Stage 1 supports retaining ordinary `pg_trgm similarity` and `text-embedding-3-small` as the lexical and semantic references. Keep the frozen Freeform sample and full catalogue when comparing representations.

Freeze one lexical method and one embedding model temporarily.

The purpose of this stage is to determine what text should be embedded.

## 2.1 Canonical-side representation

Compare:

1. canonical name only;
2. canonical name + curated definition;
3. canonical name + curated definition + examples;
4. canonical name + definition + examples + exclusions.

Example canonical representation:

```text
Canonical tag: [AO3 canonical name]
Tag type: [observed AO3 type]

Definition:
[Curated description consistent with the intended wrangling policy]

Examples:
[Permitted development aliases; exclude every evaluated alias]

Exclusions:
[Related concepts or relationships that are not equivalent]
```

## 2.2 Incoming-side representation

Compare:

1. raw tag only, within its observed tag type;
2. raw tag + permitted observed co-tags from the same work;
3. raw tag + selected observed fandom/character co-tags;
4. richer observed text only after separately acquiring it with provenance.

The current dump contains no work titles, summaries, or excerpts. Co-tag context must pass the leakage rules in Stage 0.

Do not assume more context is always better. Long or irrelevant context may dilute the tag meaning.

## 2.3 Context placement

Test context independently in retrieval and adjudication later.

For retrieval, compare:

| Embedding gets context | Meaning |
|---|---|
| No | semantic retrieval based only on the tag |
| Yes | semantic retrieval may use source evidence to disambiguate the tag |

Later, Jev will separately be tested with and without context.

## 2.4 Generated semantic enrichment

Only after the non-enriched representations have been tested, evaluate LLM-generated enrichment.

Treat these as separate experiments:

- canonical-side enrichment only;
- incoming-side enrichment only;
- both sides enriched.

Always preserve:

- raw tag;
- original source context;
- generated enrichment as an additional field, not a replacement.

Freeze the enrichment model and prompt during each experiment and cache outputs.

Primary metric:

- Recall@N of the correct canonical tag.

Secondary metrics:

- latency;
- cost;
- failure rate;
- sensitivity to noisy context.

---

# 3. Compare Embedding Configurations

Take the strongest text representations from Stage 2.

## 3.1 Embedding model

Compare several plausible embedding models.

For each model, record:

- provider/model/version;
- embedding dimension;
- query/document formatting;
- maximum input length;
- normalisation policy;
- recommended similarity metric;
- cost;
- latency.

Always re-embed both incoming and canonical text with the same model.

## 3.2 Similarity metric

Treat the similarity function as part of the embedding configuration.

Test only genuinely different settings.

Candidate metrics include:

- cosine similarity / cosine distance;
- dot product;
- Euclidean distance.

Important:

If both vectors are unit-normalised, then:

```text
cosine ranking
= dot-product ranking
= Euclidean nearest-neighbour ranking
```

So testing all three adds little value when normalisation makes them mathematically equivalent.

If vectors are not normalised, rankings may differ.

For each embedding model:

1. start with the provider/model's recommended metric;
2. test alternatives only if they produce meaningfully different rankings;
3. record whether vectors are explicitly normalised.

## 3.3 Search method

Use exact vector search during the quality study.

Do not introduce approximate nearest-neighbour search yet.

Reason:

> Approximate indexing creates a second source of recall loss, which would make it harder to tell whether misses come from the embedding representation or the index.

Measure:

- Recall@1
- Recall@3
- Recall@5
- Recall@10
- mean/median retrieval latency

Select the strongest few embedding configurations rather than prematurely choosing exactly one.

---

# 4. Compare Lexical Retrieval Methods Properly

Now refine lexical retrieval using the same benchmark.

Candidate methods:

- exact alias/name;
- `pg_trgm similarity`;
- `word_similarity`;
- `strict_word_similarity`;
- normalised edit distance;
- RapidFuzz scorers.

Consider separate treatment of:

- full-string tags;
- multiword tags;
- abbreviations;
- spelling mistakes;
- punctuation-heavy technical tags.

Measure candidate recall independently of semantic retrieval.

Keep the top lexical configurations for fusion.

---

# 5. Candidate Fusion

Now combine lexical and semantic retrieval.

Separate three concepts:

```text
K_lexical  = number retrieved lexically
K_semantic = number retrieved semantically
N_final    = number of distinct canonical candidates passed to the final adjudicator
```

Do not treat these as the same parameter.

Example:

```text
top 20 lexical
+
top 20 semantic
->
deduplicated candidate pool
->
fusion/reranking
->
top 5 final candidates
```

## 5.1 Fusion baseline

Start with a simple deterministic policy that does not privilege one source accidentally.

Do not use:

```python
dedupe(lexical + semantic)[:N]
```

because that implicitly favours the lexical list.

Use either:

- quota/interleaving;
- explicit rank fusion.

## 5.2 Reciprocal Rank Fusion

Use RRF as the main early hybrid baseline.

Conceptually:

```text
score(candidate)
=
1 / (k + lexical_rank)
+
1 / (k + semantic_rank)
```

if the candidate appears in both lists.

Advantages:

- avoids pretending trigram and embedding scores are on the same scale;
- rewards agreement between retrievers;
- simple and robust.

Test several retrieval depths, for example:

```text
K_lexical: 10, 20, 50
K_semantic: 10, 20, 50
```

Evaluate:

- fused Recall@3;
- fused Recall@5;
- fused Recall@10.

## 5.3 Weighted raw-score fusion

Only after RRF is established, evaluate weighted score combinations.

Example:

```text
hybrid_score
=
w_lexical * lexical_score
+
w_semantic * semantic_score
```

Potential weights:

```text
0.2 / 0.8
0.4 / 0.6
0.5 / 0.5
0.6 / 0.4
0.8 / 0.2
```

Important:

Raw lexical and semantic scores are not naturally calibrated to the same scale.

Before combining them, either:

- normalise scores appropriately;
- convert them into percentiles/ranks;
- learn the transformation from labelled data.

Do not treat an absent candidate as having a raw similarity score of zero unless that is explicitly justified by the scoring design.

## 5.4 Learned fusion

Only consider this later, after enough labelled examples exist.

Possible features:

- lexical rank;
- lexical score;
- semantic rank;
- semantic similarity;
- exact alias flag;
- tag length;
- number of candidates agreeing;
- score margins;
- context availability.

A simple logistic model or small gradient-boosted model may be sufficient.

Primary metric for the fusion stage:

> Recall@N of the correct canonical tag at a fixed final candidate budget.

---

# 6. Establish the Best Non-Jev Decision Baseline

Before evaluating Jev, freeze candidate lists and compare simple final decision rules.

This prevents Jev from being compared only against an artificially weak baseline.

Candidate baselines:

1. top fused candidate;
2. top fused candidate with rejection threshold;
3. simple learned scorer;
4. cross-encoder reranker;
5. small general-purpose LLM classifier, if relevant.

Record:

- final top-1 accuracy;
- false merge rate;
- no-match false-acceptance rate;
- abstention rate;
- latency;
- cost.

---

# 7. Evaluate Jev's Incremental Value

Use the exact same frozen candidate lists as the non-Jev baselines.

The main question is:

> When the correct canonical tag is already present in the candidate list, does Jev improve final selection or rejection?

## 7.1 Candidate count

Test:

```text
N = 1
N = 3
N = 5
N = 10
```

`N = 1` is useful because it tests Jev purely as an accept/reject adjudicator.

Larger `N` tests whether Jev can choose among several plausible neighbours.

Always include:

```text
none_of_these
```

when no valid candidate is possible.

## 7.2 Jev input design

Test candidate descriptions:

1. name only;
2. name + definition;
3. name + definition + examples;
4. name + definition + exclusions.

Test instructions:

### Generic matching

```text
Which candidate best matches the incoming tag?
```

### Strict canonical equivalence

```text
Which candidate represents the same underlying concept as the incoming tag?

Do not select a candidate merely because it is broader, narrower, related, or commonly associated.
```

Compare this strict-equivalence prompt with instructions aligned to AO3's recorded wrangling policy. Select a production candidate only after checking that its definition of equivalence agrees with the Stage 0 label policy. A semantic-equivalence study using a different ontology must have separate labels and reporting.

## 7.3 Context placement in Jev

Compare:

| Embedding gets context | Jev gets context |
|---|---|
| No | No |
| Yes | No |
| No | Yes |
| Yes | Yes |

This isolates whether context helps:

- candidate retrieval;
- final adjudication;
- both.

## 7.4 Retrieval scores shown to Jev

Compare:

### Hidden retrieval evidence

Jev receives only:

- incoming tag;
- source context;
- candidate names;
- definitions.

### Retrieval evidence supplied

Jev additionally receives:

- lexical rank;
- semantic rank;
- possibly retrieval scores.

The default preference should be to keep retrieval scores hidden so Jev acts as an independent semantic adjudicator.

Test whether supplying them helps or simply biases Jev toward the existing retrieval ranking.

## 7.5 Candidate ordering

Test robustness to candidate order.

Compare:

- retrieval-ranked order;
- randomised/shuffled order.

A strong adjudicator should not change substantially merely because options are presented in a different sequence.

## 7.6 Error decomposition

For every Jev decision, classify failures as:

### Retrieval miss
The correct canonical tag never reached Jev.

### Selection error
The correct tag was present, but Jev chose another candidate.

### False acceptance
Jev mapped something that should have been `none` or ambiguous.

### Unnecessary abstention
Jev rejected a sufficiently supported mapping.

Also record:

```text
rescues
=
cases where retrieval top-1 was wrong but Jev selected the correct candidate

regressions
=
cases where retrieval top-1 was correct but Jev changed it incorrectly
```

This is one of the clearest ways to measure Jev's actual value.

---

# 8. Tune the Final Decision Policy

Only after retrieval and adjudication have been evaluated should acceptance thresholds be tuned.

The decision layer should separate:

```text
auto-map
manual review / secondary processing
leave unmapped
```

## 8.1 Main operating metrics

Use:

### Auto-map precision

```text
correct automatic mappings
/
all automatic mappings
```

### Auto-map coverage

```text
all automatic mappings
/
all incoming examples
```

Also report:

- no-match false-acceptance rate;
- ambiguous-case false-acceptance rate;
- manual-review rate;
- unmapped rate.

Compare systems at:

- equal target precision; or
- equal target coverage.

Do not compare arbitrary thresholds such as `0.9` across different models as if the numbers were directly comparable.

## 8.2 Candidate decision features

Potential features include:

- Jev top-choice probability;
- Jev runner-up margin;
- `none_of_these` probability;
- retrieval agreement;
- lexical exact-match flag;
- fusion score;
- presence/absence of context.

Evaluate whether each feature actually improves decision quality.

Do not assume every available score adds independent information.

## 8.3 Calibration

Use the calibration split to determine:

- auto-map thresholds;
- review thresholds;
- abstention thresholds.

Evaluate whether predicted probabilities correspond to observed correctness.

Do not tune thresholds on the untouched final test set.

---

# 9. Interaction Study

After the strongest individual settings have been identified, test a small number of combinations.

Example:

```text
2 embedding/representation configurations
x
2 fusion methods
x
3 Jev candidate counts
=
12 end-to-end configurations
```

Purpose:

- detect interactions;
- ensure a locally best setting is not globally poor;
- determine whether stronger retrieval reduces Jev's value;
- determine whether richer context changes the ideal shortlist size.

---

# 10. Robustness Tests

Evaluate the selected configurations on targeted difficult subsets.

Include:

## Lexical edge cases

- spelling mistakes;
- pluralisation;
- punctuation;
- abbreviations;
- casing;
- multiword variants.

## Semantic edge cases

- broad versus specific concepts;
- parent versus child ontology concepts;
- closely related technologies;
- same acronym with different meanings;
- synonyms with low lexical overlap.

## Context edge cases

- no context;
- useful context;
- irrelevant context;
- misleading context;
- very long context;
- contradictory context.

## Catalogue edge cases

- correct canonical missing;
- many near-duplicate canonical tags;
- canonical definition incomplete;
- canonical definition overly broad.

Measure performance by subset rather than only reporting one aggregate score.

---

# 11. Efficiency and Production-Oriented Tests

Only after quality is understood should performance optimisations be introduced.

## 11.0 PostgreSQL index experiment

Use the frozen Stage 1 rankings as the reference for indexed retrieval. Proposed indexes for tables already scoped to one snapshot, Freeform type, and (for vectors) embedding configuration:

```sql
CREATE INDEX freeform_term_trgm
    ON freeform_term USING gist (name gist_trgm_ops);

CREATE INDEX freeform_embedding_cosine
    ON freeform_embedding USING hnsw (embedding vector_cosine_ops);
```

These are proposed DDL examples; the persistent tables and indexes have not been created.

- **Lexical:** use GiST nearest-neighbour ordering: `name <-> $1` for similarity, `$1 <<-> name` for word similarity, and `$1 <<<-> name` for strict word similarity, with `LIMIT`. GIN is an alternative for threshold/pattern filtering. [pg_trgm index support](https://www.postgresql.org/docs/18/pgtrgm.html#PGTRGM-INDEX).
- **Exact lookup:** use a nonunique B-tree on `(snapshot_id, type, normalization_version, normalized_name)` and retain all colliding canonical IDs for abstention. [PostgreSQL index types](https://www.postgresql.org/docs/18/indexes-types.html).
- **Vectors:** start with HNSW cosine ordering, `embedding <=> $1`, ascending with `LIMIT`. Vary `hnsw.ef_search`; compare IVFFlat if build time or memory becomes limiting. Use type/configuration partitions or partial indexes, and evaluate iterative scans where filters reduce returned neighbours. [pgvector indexing and filtering](https://github.com/pgvector/pgvector#hnsw).

Preserve alias masking, canonical-ID deduplication and numeric-ID tie breaks. For lexical alias retrieval, fetch enough representation rows and cutoff ties before selecting ten distinct canonical IDs. A fixed ten alias rows can hide valid targets. Test indexed shortlists against the existing exhaustive outputs, including punctuation and tied-score cases.

First compare exact pgvector ranking on the cached float32 vectors with the float64 NumPy reference; then measure additional ANN loss separately. Record `EXPLAIN (ANALYZE, BUFFERS)`, p50/p95 latency, build time, index size, shortlist overlap and labelled Recall@k on development only. Keep query type, snapshot, and model configuration identical across conditions.

Indexes speed shortlist retrieval. Exhaustive all-pairs scoring and full target ranks still require broader work. For Levenshtein/WRatio, test trigram retrieval followed by reranking as a separate configuration and measure the effect of candidate pruning on recall.

## 11.1 Approximate vector search

Compare:

- exact search;
- HNSW;
- IVFFlat, if relevant.

Measure:

- additional candidate recall loss;
- latency;
- index size;
- operational complexity.

## 11.2 Candidate budget

Reduce retrieval depth and Jev shortlist size gradually.

Measure quality versus:

- embedding query cost;
- database latency;
- Jev latency;
- Jev cost.

## 11.3 Selective Jev invocation

Evaluate whether Jev can be skipped on easy cases.

Potential fast paths:

- exact canonical-name match;
- exact unambiguous alias;
- extremely strong retrieval agreement;
- deterministic business rule.

Example architecture:

```text
incoming tag
    |
    v
exact name/alias?
    |
    +-- yes --> deterministic mapping
    |
    no
    |
    v
lexical + semantic retrieval
    |
    v
candidate fusion
    |
    +-- clearly safe under validated policy --> map
    |
    v
Jev adjudication
    |
    v
auto-map / review / unmapped
```

Compare:

- quality;
- percentage of requests invoking Jev;
- average cost;
- p50/p95 latency.

---

# 12. Final Locked Evaluation

Before final evaluation, freeze:

- benchmark definition;
- normalisation rules;
- lexical method;
- embedding model/version;
- embedding text format;
- similarity metric;
- vector normalisation;
- retrieval depths;
- fusion method;
- fusion parameters;
- canonical descriptions;
- enrichment prompt/model, if used;
- Jev model/version;
- Jev instructions;
- Jev candidate descriptions;
- candidate count;
- acceptance/review thresholds.

Run the untouched test set once.

Report:

## Retrieval

- Recall@1
- Recall@3
- Recall@5
- Recall@10

## Final canonicalisation

- overall accuracy on matchable examples;
- auto-map precision;
- auto-map coverage;
- false merge rate;
- no-match false-acceptance rate;
- ambiguous-case false-acceptance rate.

## Jev contribution

- rescues;
- regressions;
- accuracy conditional on the correct tag being in the shortlist;
- improvement over fused top-1;
- improvement over non-Jev rerankers.

## Operational metrics

- embedding cost per query;
- Jev cost per adjudicated query;
- percentage of queries requiring Jev;
- p50/p95 latency;
- database/index size.

Include confidence intervals or raw counts where possible.

---

# Recommended First Experiment

To avoid an excessively large search space, start with the following constrained configuration.

## Canonical representation

For the AO3 snapshot, start with canonical name only and restrict candidates by the observed input tag type. Curated definitions are absent. Once definitions have been collected with provenance and holdout checks, compare:

```text
canonical name + curated definition
```

## Incoming representation

Start with raw tag names. The works table can later supply co-tag context after resolving visible names and masking label leakage. Compare:

```text
tag only
```

versus:

```text
tag + permitted observed co-tags
```

## Lexical retrieval

Compare:

```text
pg_trgm similarity
```

versus:

```text
normalised edit distance / RapidFuzz
```

## Semantic retrieval

Use two embedding models.

For each:

- use the model's recommended normalisation;
- use the model's recommended similarity metric;
- use exact vector search.

## Fusion

Start with:

```text
Reciprocal Rank Fusion
```

using:

```text
top 20 lexical
top 20 semantic
```

## Jev shortlist

Test:

```text
1, 3, 5, 10 candidates
```

plus `none_of_these`.

## Final comparison

Compare:

```text
fused top-1
```

against:

```text
fused top-1 with rejection
```

against:

```text
cross-encoder or simple reranker
```

against:

```text
Jev Choice
```

## Main success criteria

Prioritise:

1. candidate Recall@N;
2. auto-map precision;
3. auto-map coverage;
4. no-match false-acceptance rate;
5. Jev rescues versus regressions;
6. cost and latency.

Do not add LLM-generated enrichment until this baseline has been measured.

---

# Experimental Order Summary

Run the work in this sequence:

```text
0. Define benchmark and ground truth
        |
        v
1. Exact + simple lexical + simple embedding baselines
        |
        v
2. Test incoming/canonical text representations
        |
        v
3. Compare embedding models and meaningful similarity configurations
        |
        v
4. Compare/refine lexical retrieval
        |
        v
5. Fuse lexical + semantic candidates
        |
        v
6. Establish strongest non-Jev final-decision baseline
        |
        v
7. Evaluate Jev's incremental value
        |
        v
8. Tune auto-map / review / abstention policy
        |
        v
9. Test interactions between strongest configurations
        |
        v
10. Run robustness subsets
        |
        v
11. Optimise latency/cost/indexing/selective Jev use
        |
        v
12. Freeze configuration and run untouched final test
```

The most important separation is:

```text
Retrieval question:
Did the correct canonical tag make it into the shortlist?

Adjudication question:
Given the correct tag in the shortlist, did the decision layer choose it?

Policy question:
Was the system confident enough to automate the mapping safely?
```

Keeping those three questions separate will make the results much easier to interpret.
