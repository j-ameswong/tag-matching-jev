# Experiment artifacts

The active dataset is AO3.

- [ao3/audit/analysis.json](ao3/audit/analysis.json) and [ao3/audit/tag_types.csv](ao3/audit/tag_types.csv): full source-file audit.
- [ao3/benchmark/manifest.json](ao3/benchmark/manifest.json): frozen catalogue/pair export hashes, ID/type rules, splits, and Freeform development sampling policy.
- [ao3/stage1/protocol.json](ao3/stage1/protocol.json): exact normalization, masking, ranking, and statistical protocol.
- [ao3/stage1/summary.json](ao3/stage1/summary.json) and [metrics.csv](ao3/stage1/metrics.csv): completed Freeform Stage 1 development results, with identities reported separately.
- [ao3/stage1/validation.json](ao3/stage1/validation.json): export, checkpoint, shortlist and rank integrity checks.
- [ao3/stage1/analysis.json](ao3/stage1/analysis.json), [lexical_difficulty.csv](ao3/stage1/lexical_difficulty.csv), and [semantic_errors.csv](ao3/stage1/semantic_errors.csv): independent candidate-membership checks and descriptive error analysis.

Stage 1 evaluates 2,000 development aliases and 250 identity controls against all 181,067 Freeform canonical candidates. Calibration/test aliases remain unevaluated. The alias-permitted lexical condition additionally uses other development aliases with query masking; it has more input information than the primary name-only comparison.

Bulk AO3 exports, API vectors, resumable score checkpoints, and detailed predictions live under gitignored `data/ao3-benchmark/` and `data/ao3-baselines/`. The [live README](../README.md) explains the experiment and reproduction commands.

The `stage1/` directory belongs to a superseded pilot. Its results and checksums are preserved unchanged; see the [archived writeup](../docs/archive/stackoverflow/README.md). Do not combine those metrics with AO3 results.
