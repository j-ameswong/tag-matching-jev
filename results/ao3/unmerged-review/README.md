# Unmerged Freeform review

**Status: retrieval and review tooling complete; independent reference judgments pending.** The experiment contains 400 random tags and 100 challenge cases from the 166,244 visible unmerged Freeform tags in the 2021-02-26 snapshot. There are no inferred ground-truth labels. The [live experiment writeup](../../../README.md#unmerged-tags-review-experiment) contains methods, descriptive findings, costs and limitations.

## Review the cases

Open [the local review page](../../../data/ao3-unmerged-review/review.html) in a browser. Keep its adjacent `review-data.js` file in the same directory. It works offline and includes searches of all 181,067 canonical Freeform names, permitted alias examples, and all 166,244 visible unmerged Freeform names. Search matches words literally; try alternate terms and inspect related concepts. It does not search held-out synonym names.

The page shows the union of three top-ten lists in ID order. Scores and sampling details start collapsed. Query context comes from the development work partition; candidate context comes from separate support works. Lack of query context is not evidence that a tag was unused. Work counts are not independent-author counts or reader engagement.

1. Read the tag, qualifiers, and available context. Search the catalogue beyond the shortlist, especially before a new-canonical decision.
2. Enter your reviewer name, decision, confidence and rationale. Use `synonym` only when the chosen canonical preserves the entire meaning, including scope. Broader/narrower or related concepts are not automatically synonyms.
3. Use `new_canonical` for a distinct useful concept after catalogue search and set `catalogue_checked` to `yes`. **The proposed name is optional**: leave it blank if you want to decide the name later. This proposes a concept for the historical catalogue; it does not apply AO3's current wrangling policy or prove independent-user support.
4. Use `unresolved` for ambiguity, composites, expressive tags, wrong-type tags, insufficient evidence, or no useful filterable concept. Put tentative matches and broader/narrower relationships in the rationale and leave target fields empty.
5. Export judgments CSV and a JSON backup regularly. Browser drafts are local to the browser/file location; exports are the portable copy. The page does not overwrite repository files automatically. Supply the downloaded CSV with `--judgments`, or deliberately replace the starter `judgments.csv` after keeping a backup.

The exported CSV retains every sampled ID. Unreviewed rows have an empty decision. CSV drafts with a decision but missing required evidence fail validation; finish that entry or clear its decision. Independent reviewers should use separate browser profiles/backups. Backup import checks the packet identity and refuses to replace conflicting completed judgments. It imports JSON backups, not CSV files; direct CSV edits can be evaluated by the CLI.

The [2026-10-04 amendment](review-amendment-2026-10-04.json) makes proposed names optional for new-canonical judgments and system proposals. The original acquisition protocol, sample and browser draft identity are preserved. A blank name does not change a `new_canonical` decision into an unresolved or unreviewed case.

Have another reviewer independently check proposed new canonicals and disagreements before using them as final references. LLM judgments can be recorded as separate system proposals, but should not be treated as independent evidence of the same system's accuracy.

## Evaluate

The random arm has 200 review-calibration and 200 review-validation cases. Challenge cases are development diagnostics. These are new tag-level splits, distinct from the original benchmark holdouts; target-family overlap is reported once reviewed labels are available.

```bash
# Run from the repository root. The default judgment file starts blank.
.venv/bin/python scripts/analyze_ao3_review.py \
  --judgments /path/to/reviewed-judgments.csv --partition calibration

# Optional separate system decisions: action is synonym, new_canonical, or abstain.
.venv/bin/python scripts/analyze_ao3_review.py \
  --judgments /path/to/reviewed-judgments.csv \
  --proposals /path/to/system-proposals.csv --partition calibration
```

Choose any thresholds on calibration only, save the exact policy and proposals, then run the evaluator with `--partition validation`. `--partition all` reports the arms separately and combines only the random arm for descriptive population estimates. Do not use partially reviewed rows to estimate prevalence: review order and difficulty can bias completion. Do not fold challenge cases into the random arm.

The fixed decision baselines are: always choose the template first result; always choose the alias-pooled first result; and choose the pooled result only when both semantic first results agree. No score threshold has been fitted. Proposal coverage is observable without labels; precision requires resolved references. Wrong synonym targets and merges of reviewed new concepts count as false merges. Merges on unresolved or unreviewed tags are reported separately and excluded from precision denominators. New-canonical decision precision evaluates the class decision; it does not score proposed wording or usefulness. Blank proposals are missing decisions, not abstentions.

The initial [evaluation](evaluation-all.json) has zero reviewed cases and null precision/recall. Its nonzero proposal counts are baseline behavior, not accepted mappings. A 500-case pilot, with only 200 random validation cases, cannot certify extremely rare false merges even if no errors are observed.

## Reproduce

```bash
OPENBLAS_NUM_THREADS=4 .venv/bin/python scripts/ao3_unmerged_review.py --offline
.venv/bin/python -m unittest discover -s tests
```

The first acquisition omits `--offline`; it sends only 2,400 templated public names through the previously selected embedding model. Candidate and alias vectors are reused. Frozen protocol/input changes fail rather than silently resampling. Existing judgment and proposal files are preserved. Phases `prepare`, `score`, and `packet` support resumption; `packet` verifies cached semantic evidence before producing lexical/context evidence and the review page. PostgreSQL runs in a disposable local cluster only when lexical checkpoints are missing.

| File | Purpose |
|---|---|
| [protocol.json](protocol.json) | Frozen sampling, retrieval, review and evaluation rules |
| [review-amendment-2026-10-04.json](review-amendment-2026-10-04.json) | Makes new-canonical names optional; acquisition and class-decision metrics stay fixed |
| [sample.csv](sample.csv) | All 500 source IDs, names, approximate usage, arms and review splits |
| [candidates.csv](candidates.csv) | 15,000 ranked candidate entries across three methods |
| [judgments.csv](judgments.csv) | Editable blank reference-label template; preserved on rerun |
| [proposals.csv](proposals.csv) | Separate optional system decision template |
| [summary.json](summary.json) | Descriptive observations, costs, artifact and implementation hashes |
| [embeddings.json](embeddings.json), [postgres.json](postgres.json) | Acquisition and scoring provenance |
| [validation.json](validation.json) | Candidate-ID integrity and disjoint work-evidence checks |
| [evaluation-all.json](evaluation-all.json) | Initial pending-reference evaluation |

The larger profiles, cached scores/vectors, source population, and generated review page are under gitignored `data/ao3-unmerged-review/`. No mappings or canonical tags have been created or modified.
