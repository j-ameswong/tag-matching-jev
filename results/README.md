# Experiment artifacts

The active dataset is AO3.

- [ao3/audit/analysis.json](ao3/audit/analysis.json) and [ao3/audit/tag_types.csv](ao3/audit/tag_types.csv): full source-file audit.
- [ao3/benchmark/manifest.json](ao3/benchmark/manifest.json): frozen catalogue/pair export hashes, ID/type rules, splits, and Freeform development sampling policy.
- [ao3/stage1/protocol.json](ao3/stage1/protocol.json): exact normalization, masking, ranking, and statistical protocol.
- [ao3/stage1/summary.json](ao3/stage1/summary.json) and [metrics.csv](ao3/stage1/metrics.csv): completed Freeform Stage 1 development results, with identities reported separately.
- [ao3/stage1/validation.json](ao3/stage1/validation.json): export, checkpoint, shortlist and rank integrity checks.
- [ao3/stage1/analysis.json](ao3/stage1/analysis.json), [lexical_difficulty.csv](ao3/stage1/lexical_difficulty.csv), and [semantic_errors.csv](ao3/stage1/semantic_errors.csv): independent candidate-membership checks and descriptive error analysis.
- [ao3/stage2/protocol.json](ao3/stage2/protocol.json), [coverage.json](ao3/stage2/coverage.json), and [representations.json](ao3/stage2/representations.json): frozen representation conditions, work partitions and feature availability.
- [ao3/stage2/summary.json](ao3/stage2/summary.json), [metrics.csv](ao3/stage2/metrics.csv), and [strata.csv](ao3/stage2/strata.csv): representation results, paired target-group intervals and comparisons on identical context-coverage subsets.
- [ao3/stage2/validation.json](ao3/stage2/validation.json) and [diagnostics.csv](ao3/stage2/diagnostics.csv): profile separation, independent shortlist checks, exact Stage 1 replay, and deterministic examples of context gains/losses.
- [ao3/stage2/html-fixture.json](ao3/stage2/html-fixture.json): provenance and extraction checks for the supplied HTML; summary and reader-engagement effectiveness are not estimated from one work.
- [ao3/stage2/alias-pooling-protocol.json](ao3/stage2/alias-pooling-protocol.json) and [alias-pooling.json](ao3/stage2/alias-pooling.json): separately frozen exploratory follow-up using individual alias vectors, including alias-coverage strata and paired comparisons.
- [ao3/unmerged-review/README.md](ao3/unmerged-review/README.md), [protocol.json](ao3/unmerged-review/protocol.json), [sample.csv](ao3/unmerged-review/sample.csv), and [summary.json](ao3/unmerged-review/summary.json): prepared 400-random / 100-challenge Freeform review experiment; independent reference labels are pending.
- [ao3/unmerged-review/candidates.csv](ao3/unmerged-review/candidates.csv), [judgments.csv](ao3/unmerged-review/judgments.csv), and [proposals.csv](ao3/unmerged-review/proposals.csv): three retrieval shortlists, editable reference judgments, and separate optional system proposals. Blank judgments are not no-match labels.
- [ao3/unmerged-review/evaluation-all.json](ao3/unmerged-review/evaluation-all.json) and [validation.json](ao3/unmerged-review/validation.json): initial null-accuracy report, shortlist integrity and evidence-work separation checks.
- [ao3/jev-top10/protocol.json](ao3/jev-top10/protocol.json) and [inputs.json](ao3/jev-top10/inputs.json): frozen seven-condition Jev experiment using exactly the strongest Stage 2 ten-candidate lists; existing merger labels supply the answers.
- [ao3/jev-top10/summary.json](ao3/jev-top10/summary.json), [metrics.csv](ao3/jev-top10/metrics.csv), and [strata.csv](ao3/jev-top10/strata.csv): conditional selection accuracy, overall matching accuracy, paired target-group intervals, rescues/regressions, alias/context strata, latency, and reported cost. Incomplete conditions retain null accuracy.
- [ao3/jev-top10/predictions.csv](ao3/jev-top10/predictions.csv): one row per query and method, with the expected/selected canonical IDs, success for that ten-candidate trial, and error decomposition. Identity controls have their own `example_kind` and are excluded from synonym totals.
- [ao3/jev-top10/parser-amendment.json](ao3/jev-top10/parser-amendment.json): preserve the API's returned Choice when it differs from the largest reported probability; prompts and shortlists were unchanged.
- [ao3/jev-top10/validation.json](ao3/jev-top10/validation.json): independent reconstruction of 22,500 outcomes against original benchmark IDs, frozen shortlists, and 15,750 raw API responses.

Stage 1 evaluates 2,000 development aliases and 250 identity controls against all 181,067 Freeform canonical candidates. Calibration/test aliases remain unevaluated. The alias-permitted lexical condition additionally uses other development aliases with query masking; it has more input information than the primary name-only comparison.

Stage 2 retains the same queries, catalogue, lexical function and embedding model. It excludes all 2,000 evaluated aliases globally, separates candidate support works from query context works, and compares 19 semantic conditions plus three lexical references. The two follow-up conditions bring the total to 24. Stage 1's leave-one-alias-out result is not the Stage 2 alias baseline. The strongest development result is 92.05% Recall@10 with templated individual alias vectors; this is not final-test performance.

Bulk AO3 exports, work profiles, input descriptions, API vectors, resumable score checkpoints, and detailed predictions live under gitignored `data/ao3-benchmark/`, `data/ao3-baselines/`, and `data/ao3-representations/`. The unmerged review evidence and generated offline review page live under `data/ao3-unmerged-review/`. The [live README](../README.md) explains the experiment and reproduction commands.

Human review and new labelling are deferred as of 2026-10-04. The completed Jev experiment uses only the existing labelled positive benchmark: AO3 instructions plus aliases reach 81.65% overall with returned Choice and 83.60% with the predeclared forced non-none rule, versus 73.15% for retrieval. A correct rejection of a retrieval miss is not a successful canonical mapping or a natural no-match label. Frozen prompts/inputs and raw Jev responses, model/provider metadata, usage, retries, and hashes are retained in `data/ao3-jev-top10/`. `analyze` replays those responses without API access. Total reported Jev cost, including separate identity controls, is US$0.738427914.

The `stage1/` directory belongs to a superseded pilot. Its results and checksums are preserved unchanged; see the [archived writeup](../docs/archive/stackoverflow/README.md). Do not combine those metrics with AO3 results.
