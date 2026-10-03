# Tag Canonicalisation Experiment Plan

## Goal

Evaluate a tag-canonicalisation pipeline that combines lexical retrieval, semantic embedding retrieval, candidate fusion, and optional Jev adjudication.

The experiment should answer four separate questions:

1. Can the correct canonical tag be retrieved?
2. Which retrieval configuration gives the best candidate recall?
3. Does Jev improve final selection or abstention once the correct candidate is present?
4. What configuration gives the best automation coverage at an acceptable incorrect-mapping rate?

A core principle throughout the experiment is:

> Retrieval should optimise for candidate recall. Jev, or another adjudicator, should optimise for final discrimination. The final policy should optimise for safe automation.

## First step: Stack Exchange CSV snapshot — completed

The initial dataset was collected on 2026-10-03, before designing the database. Snapshot ID: `stackoverflow-7d95f64002496ef5`.

The collector uses the two requested public endpoints:

```text
GET https://api.stackexchange.com/2.3/tags?site=stackoverflow&pagesize=100&page=1&sort=popular&order=desc
GET https://api.stackexchange.com/2.3/tags/synonyms?site=stackoverflow&pagesize=100&page=1&sort=applied&order=desc
```

Each endpoint was paged from 1 through 25. These reads succeeded anonymously, so this snapshot needs no OAuth token or API key. Anonymous access is limited to page 25 and pages contain at most 100 items. An authenticated expansion should follow the [authentication documentation](https://api.stackexchange.com/docs/authentication). See the [API overview](https://api.stackexchange.com/docs) and [paging documentation](https://api.stackexchange.com/docs/paging) for the sample limits.

| Artifact | Rows | Purpose |
|---|---:|---|
| [experiment.csv](data/stackoverflow/experiment.csv) | 4,224 | Main benchmark: incoming tag, canonical target, example kind, label, split, source group, counts, and provenance |
| [tags.csv](data/stackoverflow/tags.csv) | 2,476 | Distinct sampled tags and metadata; filter `is_canonical_candidate=true` for the 2,454-tag catalogue |
| [synonyms.csv](data/stackoverflow/synonyms.csv) | 2,500 | All sampled direct mappings, resolved targets, and `target_in_catalogue` flags |
| [manifest.json](data/stackoverflow/manifest.json) | — | Sampling parameters, capture times, checksums, counts, split rules, and validation results |
| [raw/](data/stackoverflow/raw/) | 50 responses | Original response objects wrapped with request URLs and UTC fetch times |

The 2,500 returned tag records contained 24 repeated names across pages. The collector retains the first occurrence of each name. Of the distinct tags, 22 are known synonym sources in this synonym sample and are excluded from the canonical catalogue. Synonym chains are resolved within the sampled mappings, while `to_tag` retains the original direct target. Conflicting targets and cycles fail the build.

The benchmark contains 2,454 canonical-name identity examples and 1,770 synonym examples whose resolved targets occur in the catalogue. The other 730 synonym mappings remain in `synonyms.csv` and are excluded from this benchmark. They are candidates for a later, explicitly defined missing-canonical experiment; they are not automatically labelled as real-world no-match examples.

### Split and evaluation rules

Canonical tags define the source groups. A deterministic SHA-256 hash of `tag-matching-jev-v1:stackoverflow:canonical_name` assigns each entire group to development, calibration, or test using 60/20/20 hash buckets. Row proportions vary because groups contain different numbers of synonyms.

| Split | Canonical groups / identity examples | Synonym examples | Total examples |
|---|---:|---:|---:|
| Development | 1,420 | 1,033 | 2,453 |
| Calibration | 517 | 369 | 886 |
| Test | 517 | 368 | 885 |

All examples are currently `matchable`, with no observed question context or curated definitions. Treat `synonyms.csv` as ground-truth labels. Calibration and test aliases must be excluded from alias retrieval and canonical enrichment; also mask any evaluated development alias when measuring held-out synonym retrieval. Canonical names remain available across all splits because they define the catalogue. Report canonical-name and synonym metrics separately so identity examples do not inflate the synonym result.

The API names are preserved without punctuation removal or other custom normalisation. CSVs use UTF-8, standard CSV quoting, lowercase boolean strings, and blank cells for unavailable values. Synonym dates retain API Unix seconds; fetch times are UTC ISO timestamps. [The synonym type documentation](https://api.stackexchange.com/docs/types/tag-synonym) permits an absent `last_applied_date`.

Both final pages returned `has_more=true`: this is a bounded sample, with the requested popularity/applied-count sorts, rather than a complete or uniformly sampled Stack Overflow vocabulary. The returned tags were not ordered by count within each page, so CSV row order is alphabetical and is not a popularity rank. Synonym-source exclusion is limited to the mappings actually sampled; unobserved mappings may remain. These limits must accompany experiment results.

### Reproduce the CSVs

The [collector](scripts/build_stackexchange_dataset.py) uses Python's standard library and `curl`. Rerunning reuses cached successful pages and resumes a partial collection. Requests are sequential, paced, and honour response `backoff`; quota exhaustion and API errors stop the collection. See the [API throttle documentation](https://api.stackexchange.com/docs/throttle).

```bash
python3 scripts/build_stackexchange_dataset.py
```

Rebuild the frozen snapshot without network access:

```bash
python3 scripts/build_stackexchange_dataset.py --offline
```

For a fresh capture, use a new output directory with `--output`; preserve this directory for the current benchmark. The anonymous collector intentionally caps each endpoint at 2,500 records.

### Full tag catalogue: API continuation and historical dump

The API continuation is prepared in `data/stackoverflow-full/raw/` using copies of the existing 25 tag pages and 25 synonym pages. Its first uncached tag page is 26. A direct `curl` request to page 26 returned HTTP 400 with API error `403 access_denied`: `page above 25 requires access token or app key`. No remaining pages of that unfiltered API query have been fetched.

Anonymous filtered queries do work: a request with `sort=popular&order=desc&max=2882&page=1` returned 100 tags with counts from 2,762 through 2,882. The API's [documented inclusive min/max windows](https://api.stackexchange.com/docs/min-max) offer a possible way to enumerate smaller result sets without requesting page 26. However, roughly 65,000 tags need roughly 650 requests at 100 items per page, exceeding the observed anonymous daily quota of 300 and the 247 requests remaining at the probe. Full live enumeration would require a key or collection over multiple quota periods, plus careful boundary handling.

#### Historical catalogue — completed

The accessible, company-published [April 2024 dump](https://archive.org/details/stackexchange) has a standalone [Stack Overflow Tags archive](https://archive.org/download/stackexchange/stackoverflow.com-Tags.7z). Downloaded anonymously with `curl`, it contains **65,675 distinct tag names** in `Tags.xml`. Its 1,117,182-byte compressed file matches the size, MD5, and SHA-1 in the [Internet Archive metadata](https://archive.org/metadata/stackexchange). The archive file's metadata modification time is `2024-04-06T21:11:23+00:00`; the release label is month-level provenance rather than a per-tag observation timestamp.

| Artifact | Purpose |
|---|---|
| [Historical tags.csv](data/stackoverflow-dump-2024-04/tags.csv) | All 65,675 rows, with name, question count, source tag ID, optional excerpt/wiki post IDs, snapshot ID, and release month |
| [Historical manifest.json](data/stackoverflow-dump-2024-04/manifest.json) | Source URLs, historical date, download time, checksum validation, row counts, and unavailable fields |
| [Historical raw files](data/stackoverflow-dump-2024-04/raw/) | Downloaded `.7z`, extracted `Tags.xml`, and original archive metadata |

The file contains tag names and counts, with excerpt/wiki post IDs where available. It contains no synonym mappings, definitions, question context, or API flags such as `has_synonyms`. Missing IDs are blank; unavailable flags must stay unknown during database import, rather than becoming false. The [dump schema documentation](https://meta.stackexchange.com/questions/2677/database-schema-documentation-for-the-public-data-dump-and-sede) describes the tag ID and post-ID fields.

Rebuild the historical CSV offline using the [dump converter](scripts/build_stackexchange_dump_csv.py):

```bash
python3 -B scripts/build_stackexchange_dump_csv.py
```

The [June 2026 community mirror](https://archive.org/details/stackexchange_20260630) is newer, but its [metadata](https://archive.org/metadata/stackexchange_20260630) lists Stack Overflow as one 68,946,936,349-byte archive, with no separate Stack Overflow Tags archive. The April 2024 tag-only file supplies the complete historical catalogue with a small download.

The existing API synonyms were captured in October 2026. A benchmark combining those labels with the April 2024 catalogue must record both dates and recompute target membership. The `target_in_catalogue` flags in the original `synonyms.csv` refer to the original 2,454-tag sample. The historical catalogue is preserved separately and has not been silently mixed into the existing benchmark. Catalogue membership alone also does not establish that a tag is canonical.

#### Future live API continuation

The collector now accepts `--all-tags` and reads an app key from `STACKEXCHANGE_API_KEY` or `--key-file`. Once the key is available, run:

```bash
python3 scripts/build_stackexchange_dataset.py --output data/stackoverflow-full --all-tags --key-file /path/to/stackexchange-api-key
```

This command reuses the copied cache, continues with page 26, and stops when the API returns `has_more=false`, rather than assuming exactly 65,000 tags. It keeps the existing 2,500-synonym sample, rebuilds the CSVs against the expanded catalogue, and records the resulting counts in the new manifest. The key is sent in an authorization header through curl's standard input and is omitted from cached URLs and metadata. The original sample remains in `data/stackoverflow/`.

An offline rebuild of a completed live capture will use the same `--output` and `--all-tags` options with `--offline`. Requests honour API backoff and stop on live quota exhaustion across endpoints. Until the API has returned its final tag page, that continuation must not be reported as a complete catalogue. Use the selected snapshot's manifest counts for the later database import checks.

## Next step: proposed PostgreSQL schema — planning only

This section proposes the later database work. Database provisioning, migrations, imports, embeddings, indexes, and retrieval experiments have not been implemented. The intended extensions are `pg_trgm` and pgvector; the PostgreSQL extension name for pgvector is `vector`. Choose and pin PostgreSQL and extension versions when implementation starts.

### Proposed tables

The snapshot is the catalogue version. Give canonical tags database IDs scoped to their snapshot rather than assuming Stack Exchange provides stable tag IDs.

| Table | Principal columns and types | Keys and purpose |
|---|---|---|
| `dataset_snapshot` | `snapshot_id text`, `site text`, source type `text`, nullable `api_version text`, optional historical release month `text`, capture start/end `timestamptz`, `manifest jsonb`, manifest checksum `text` | Primary key `snapshot_id`; immutable source, historical date, and sampling provenance |
| `canonical_tag` | `tag_id bigint` identity, `snapshot_id text`, `name text`, `normalised_name text`, `question_count bigint`, nullable source flags `boolean`, optional source tag/post IDs in `source_metadata jsonb`, `source_url text`, `fetched_at timestamptz` | Primary key `tag_id`; unique `(snapshot_id, name)` and `(snapshot_id, tag_id)`; load candidates after applying the selected catalogue's synonym policy |
| `tag_synonym` | `snapshot_id text`, `from_tag text`, `to_tag text`, `resolved_target_name text`, nullable `target_tag_id bigint`, `applied_count bigint`, `created_at timestamptz`, nullable `last_applied_at timestamptz`, source URL/fetch time | Primary key `(snapshot_id, from_tag)`; preserve all 2,500 labels, including targets outside the catalogue |
| `benchmark_group` | `snapshot_id text`, `source_group text`, `split text` | Primary key `(snapshot_id, source_group)`; store development/calibration/test once per group |
| `benchmark_example` | `example_id text`, `snapshot_id text`, `source_group text`, `incoming_tag text`, `example_kind text`, `label text`, `expected_target_name text`, nullable `expected_tag_id bigint`, source URL/fetch time; optional context text and provenance later | Primary key `example_id`, preserving the CSV ID; group foreign key determines the split; load `experiment.csv` |
| `embedding_configuration` | configuration ID `bigint`, provider/model/revision `text`, `dimensions integer`, distance metric `text`, normalisation policy `text`, canonical/query template versions `text`, alias-mask policy `text`, optional enrichment model/prompt hash, configuration fingerprint `text` | Primary key configuration ID; unique fingerprint and unique `(configuration_id, dimensions)`; freeze the complete embedding recipe |
| `canonical_embedding` | `snapshot_id text`, `tag_id bigint`, configuration ID `bigint`, `dimensions integer`, `input_text text`, input checksum `text`, `embedding vector`, `created_at timestamptz` | Primary key `(tag_id, configuration_id)`; immutable canonical vector cache for a frozen snapshot/configuration |
| `query_embedding` | `snapshot_id text`, `example_id text`, configuration ID `bigint`, `dimensions integer`, `input_text text`, input checksum `text`, `embedding vector`, `created_at timestamptz` | Primary key `(example_id, configuration_id)`; independently cache each incoming representation |

Keep generated descriptions and observed context out of the initial import. Later additions must record their provenance and create a new snapshot or configuration when embedding inputs change. Experiment-run, candidate-rank, fusion, and Jev-decision tables can be designed once those stages begin.

### Integrity and import policy

- Foreign keys referencing a tag or example must include `snapshot_id`, with matching composite unique keys on the parent tables. This prevents labels or embeddings from referring to another catalogue version.
- Require non-empty names, non-negative counts, and one of the defined split/category values. A `matchable` example must have an expected tag in its own catalogue; a `no_match` example must have no expected catalogue tag. Ambiguous examples need an explicit later labelling policy.
- Store direct and resolved synonym targets separately. A null `target_tag_id` means the resolved name is outside this sampled catalogue; it does not erase the known upstream target.
- Enforce group membership through `benchmark_group`. Validate that each CSV group's split and expected canonical target agree before importing.
- Preserve original names and derive a versioned normalised name for lookup. Retain punctuation in `c`, `c++`, `c#`, and `.net`; allow normalisation collisions to be detected rather than forcing a unique normalised-name constraint.
- Convert API Unix seconds to `timestamptz` during the later import; blank optional values and unavailable dump flags become SQL `NULL`. Keep historical release dates separate from download times. Import through staging and verify manifest checksums, counts, and foreign keys before exposing a snapshot to retrieval.

### Lexical retrieval with `pg_trgm`

Propose B-tree indexes on snapshot/name and snapshot/normalised-name for exact lookups. Initially rank all canonical names in the selected snapshot using `similarity`, with deterministic name/ID tie breaks; compare `word_similarity` and `strict_word_similarity` separately. The `%` operator applies a threshold, so using it to prune the initial top-K baseline could hide relevant candidates. For later speed work, GiST `gist_trgm_ops` supports nearest-neighbour ordering by trigram distance; GIN `gin_trgm_ops` supports threshold/filter searches. `pg_trgm` ignores non-alphanumeric characters, so punctuation-heavy tags need separate error reporting and an exact-name baseline. [PostgreSQL `pg_trgm` documentation](https://www.postgresql.org/docs/current/pgtrgm.html).

Alias lookup must apply the benchmark's holdout mask. Start synonym retrieval against canonical names only; permitted aliases can be introduced as a separate configuration and deduplicated by canonical tag ID.

### Semantic retrieval with pgvector

Propose unconstrained `vector` columns so different configurations can use different dimensions. Each embedding row carries a dimension count, checks `vector_dims(embedding) = dimensions`, and references `(configuration_id, dimensions)` in the configuration table. This avoids choosing a model or hardcoding a dimension now. Keep configurations immutable and validate input hashes before reusing embeddings. [pgvector dimension guidance](https://github.com/pgvector/pgvector#can-i-store-vectors-with-different-dimensions-in-the-same-column).

Initial searches must restrict both snapshot and configuration and use exact vector search. The distance operators are `<=>` for cosine, `<->` for Euclidean distance, and `<#>` for negative inner product; choose the configured model's metric and sort distance ascending. Record normalisation explicitly. HNSW/IVFFlat and metric-specific operator classes belong to the later efficiency study, after an exact baseline is established. [pgvector querying and indexing documentation](https://github.com/pgvector/pgvector#querying).

### Later implementation sequence

1. Review this schema against the frozen CSVs and select PostgreSQL/extension versions.
2. Implement migrations and a transactional, repeatable CSV import, using the selected manifest's counts. The original API sample has 2,454 candidates, 2,500 synonyms, 2,454 groups, and 4,224 examples; the separate historical catalogue has 65,675 raw tags and requires an explicit synonym/date policy before becoming a benchmark.
3. Implement exact-name and canonical-name trigram baselines with synonym-only Recall@K reporting.
4. Select an embedding model, freeze its dimensions/representation/metric, generate cached vectors, and measure exact retrieval.
5. Add definitions, context, negatives, fusion, adjudication, and approximate indexes in the experimental order below.

---

# 0. Define the Task and Benchmark

Before comparing models or retrieval methods, define what counts as a correct mapping.

## 0.1 Define canonical equivalence

Decide whether the task is:

- strict canonical equivalence, or
- broader "best tag for this context" assignment.

For this experiment, prefer strict equivalence:

> Map the incoming tag to a canonical tag only when they represent the same underlying concept at the intended ontology granularity.

A broader, narrower, or related concept should not count as equivalent.

Example:

- `postgres` -> `PostgreSQL`: correct
- `postgres` -> `Relational Database`: related but not equivalent
- `postgres` -> `Database`: broader, not equivalent
- `postgres` -> `MySQL`: related but incorrect

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

- remove the tested synonym from alias lookup;
- do not include the held-out synonym in generated canonical descriptions;
- do not let the enrichment process see the answer;
- keep related examples from the same source group together when splitting data.

Where possible, maintain:

- development set;
- calibration set;
- untouched final test set.

## 0.4 Benchmark notes for Stack Overflow

If Stack Overflow tags/synonyms are used:

- treat synonym mappings as platform-defined canonicalisation ground truth;
- distinguish genuinely observed context from reconstructed context;
- remember that Stack Overflow may rewrite synonym tags to their target tags automatically;
- keep lexical-easy mappings separate from semantically difficult cases.

Useful evaluation subsets:

- exact/near-exact aliases;
- spelling variants;
- abbreviations;
- semantically similar aliases;
- ambiguous aliases;
- related but non-equivalent hard negatives;
- missing-canonical cases.

---

# 1. Establish Simple Baselines

Do not use Jev yet.

The purpose of this stage is to understand how much of the task can already be solved with simple methods.

## 1.1 Exact normalised matching

Test:

- exact canonical-name matching;
- exact alias matching;
- case folding;
- whitespace normalisation;
- safe punctuation normalisation.

Avoid destructive normalisation that collapses distinct tags such as:

- `C`
- `C++`
- `C#`

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
Canonical tag: PostgreSQL

Definition:
A specific open-source relational database management system.

Examples:
postgres, PostgreSQL database

Exclusions:
Do not use for databases generically, MySQL, SQLite, or SQL as a language.
```

## 2.2 Incoming-side representation

Compare:

1. raw tag only;
2. raw tag + title;
3. raw tag + title + short relevant excerpt;
4. raw tag + larger source context.

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

The strict-equivalence version should be the main production candidate.

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

For the current Stack Exchange CSV snapshot, start with canonical name only. Curated definitions are not supplied by these endpoints. Once definitions have been collected with provenance and holdout checks, compare:

```text
canonical name + curated definition
```

## Incoming representation

The current snapshot supplies raw tag names only. Once observed context is available, compare:

```text
tag only
```

versus:

```text
tag + title + short relevant excerpt
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
