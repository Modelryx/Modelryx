# Engineering search

This document defines the implemented search behavior, reliability gates, and outstanding external
qualification evidence.

## Status

Implementation complete; external qualification pending. Slices 0–5 are implemented in the
repository, including the locked corpus, advertised-filter result coverage, ranking checks,
correction feedback, privacy controls, aggregate telemetry, and alert rules. Final phase sign-off
still requires the production-like latency run, representative-user study, and named stakeholder
approvals described under **Release evidence**; those results must not be inferred from unit tests.

Implemented behavior currently includes:

- deterministic material-presence paraphrases, including `has no material`;
- present/absent filters for hole, pocket, fillet, chamfer, extrusion, revolution, pattern, and
  thread metadata;
- explicit zero counts from conversion, with null/unknown records excluded from absence results;
- multiple feature constraints in one query and warnings for contradictory constraints;
- explicit unsupported-capability warnings for slot, boss, and rib presence requests;
- applied material and feature filters in the Library UI, with individual removal and an empty-result
  clear-filter recovery action;
- parser and database-backed regression coverage for the reported material and hole failures.
- seeded result-set metrics for exact-filter precision and recall, exclusion correctness, false-zero
  rate, unexpected closed-set results, and required ordering.
- measured deterministic interpretation p95 with a 50 ms upper release threshold.
- ranking evaluation for top-5 recall, required-top-result accuracy, and nDCG@10.
- isolated database-backed result cases for material, controlled features, combined feature filters,
  dimension ranges, mass limits, classification, revision, CAD system, lifecycle, owner, and item
  type.

The versioned evaluation harness has development and locked partitions. Run its locked
interpretation release gate with:

```powershell
python -m modelryx.engineering_search.evaluation `
  --corpus tests/search/search_reliability_v1.json `
  --schema tests/search/search_reliability.schema.json `
  --partition locked `
  --release
```

Release mode enforces correctness thresholds plus corpus size, development/locked partition
presence, and required case-mix ratios. It exits unsuccessfully if any enforced gate fails.

The interpretation corpus currently expands to 237 version-controlled cases and satisfies the
documented size and case-mix requirements. Passing it does not by itself satisfy the result,
ranking, AI-enabled, operational, or representative-user release gates.

Run the complete repository gate (clean migrated PostgreSQL, strict corpus evaluation, result and
ranking checks, API tests, and all other backend regressions) with:

```powershell
docker compose --profile test build test
docker compose --profile test run --rm test
```

Then run `npm ci`, `npm run typecheck`, and `npm run build` from sibling folder `../front`. CI repeats the backend
gate and runs the mocked AI-valid, malformed-output, and timeout contracts separately.

## Purpose

For full implementation details, see [ai/overview.md](overview.md).

Make Modelryx engineering search predictable across common ways that users express the same intent.
The phase hardens the complete search path:

1. natural-language interpretation;
2. validated `SearchRequest` generation;
3. deterministic database execution;
4. ranking and result presentation;
5. explanation, correction, and failure recovery;
6. regression evaluation using representative organization data.

The goal is not to make the system appear conversational. The goal is to ensure that supported
engineering questions return the correct records, disclose what was interpreted, and fail visibly
when an intent is unsupported.

## Motivation and known failure

The current deterministic interpreter recognizes `find part without material` as
`filters.material_presence = "missing"`. It does not recognize the natural variation
`find part has no material`. In that variation, words may remain as full-text terms and eliminate
otherwise valid results.

The existing Phase 2.1 evaluation corpus covers a small number of canonical phrases. Passing that
corpus demonstrates basic capability, but not paraphrase robustness or end-to-end result accuracy.

This phase treats every confirmed user-reported false-zero or incorrect-result query as a regression
case that must be preserved permanently.

## Product principles

- Structured filters, identifiers, units, lifecycle states, and relationships are authoritative.
- The LLM may interpret language but never emits SQL or bypasses `SearchRequest` validation.
- Critical filters use deterministic extraction where practical; LLM output may supplement them.
- Deterministic fallback must remain useful when the AI gateway is disabled or unavailable.
- Equivalent user phrases must produce equivalent normalized requests and result sets.
- Unsupported or ambiguous language must produce a warning instead of an invented constraint.
- A zero-result response must distinguish “no matching data” from interpretation or execution failure.
- The UI must expose applied filters and provide a direct correction path.
- Evaluation uses fixed datasets and expected outcomes; it must not derive expected answers from the
  implementation under test.

## Scope

### In scope

- Query normalization and paraphrase handling.
- Negation and presence/absence filters.
- Item, part, and assembly intent.
- Identifiers, text, classification, material, geometry, mass, CAD system, revision, lifecycle,
  ownership, and date filters.
- Similarity and BOM/where-used relationship intents already exposed by the capability registry.
- Follow-up queries that modify a previous validated request.
- LLM interpretation, schema validation, deterministic fallback, and conflict resolution.
- Search execution, ranking, pagination, and stable ordering.
- Interpretation disclosure, empty-result recovery, and user correction.
- Offline evaluation corpora, database-backed evaluation, observability, and release gates.

### Out of scope

- Adding new PLM domains solely to make them searchable, such as suppliers, costs, effectivity, or
  engineering change orders.
- Autonomous release, approval, deletion, or modification based on search output.
- Replacing PostgreSQL because of unmeasured scale concerns.
- Treating vector similarity as proof that two parts are interchangeable.
- Training or fine-tuning a proprietary model during this phase.

## Supported-intent contract

Create a versioned catalog of supported search intents. Each intent must define:

- canonical `SearchRequest` representation;
- positive and negative examples;
- paraphrases and common informal grammar;
- required context;
- expected warnings for ambiguity;
- deterministic fallback behavior;
- result-set fixtures and expected record identifiers.

The initial intent catalog must cover all fields currently advertised by
`engineering_search.registry.SEARCH_CAPABILITIES`.

Presence/absence intents require special coverage. For missing material, examples include:

- `parts without material`;
- `parts with no material`;
- `parts that have no material`;
- `parts that do not have a material`;
- `material is missing`;
- `material is blank`;
- `material is empty`;
- `material not entered`;
- `unassigned material`;
- `unknown material`.

Negative examples must ensure that phrases such as `not aluminum`, `no material changes`, or
`material is not stainless steel` are not incorrectly mapped to missing material.

### Controlled CAD feature-presence intent

The supported-intent catalog must include a reusable feature-presence grammar rather than a separate
hard-coded expression for every CAD feature:

```text
(no | without | has no | does not have | does not have any) + <supported-feature>
(with | has | contains | includes) + <supported-feature>
```

The initial controlled feature vocabulary should include only features that the normalized CAD
metadata pipeline can identify with measured reliability. Candidate terms are `hole`, `pocket`,
`slot`, `fillet`, `chamfer`, `thread`, `boss`, and `rib`. Singular, plural, and reviewed synonyms
such as `bore` for an applicable hole classification must normalize to one canonical feature name.
Unsupported feature words must produce a warning instead of being converted into an invented
constraint.

Examples that must normalize to the same absence intent include:

- `find parts with no holes`;
- `find part has no hole`;
- `parts without holes`;
- `parts that do not have any holes`;
- `hole-free parts`.

The versioned `SearchRequest` contract should represent the intent explicitly, for example:

```json
{
  "filters": {
    "feature_filters": [
      {
        "feature": "hole",
        "presence": "absent"
      }
    ]
  }
}
```

Positive forms such as `parts with pockets` or `parts that contain threads` use
`presence = "present"`. Later capability versions may add count and dimensional predicates, such
as `hole count = 0`, `at least four holes`, or `holes larger than 5 mm`, only after the extraction
contract exposes the required authoritative fields.

Feature presence has three semantic states:

- **present** — successful supported extraction confirmed at least one matching feature;
- **absent** — successful supported extraction confirmed zero matching features;
- **unknown** — extraction is unavailable, failed, incomplete, or does not support that feature.

An absence query must match only `absent`; it must never treat `unknown` as proof that the feature is
absent. The result explanation must disclose whether the extraction coverage excluded unknown records.
Patterns must be domain-aware: a generic `no <any word>` rule is prohibited because phrases such as
`no supplier`, `no drawing`, and `no material change` have different contracts and semantics.

## Target interpretation architecture

Use a hybrid interpretation pipeline:

1. Normalize Unicode, whitespace, case, units, and known domain aliases.
2. Deterministically extract high-confidence identifiers and critical structured constraints.
3. Ask the LLM to interpret the full query using the versioned capability registry and JSON schema.
4. Validate the LLM output as a `SearchRequest`.
5. Merge deterministic and LLM interpretations using explicit precedence and conflict rules.
6. Remove consumed phrases from residual full-text terms.
7. Reject or warn about unresolved contradictions and unsupported language.
8. Execute only the validated request.
9. Return the normalized request, parser mode, warnings, and trace alongside results.

Deterministic critical constraints take precedence when they are unambiguous. A conflict between a
critical deterministic constraint and LLM output must not be silently resolved; it must use the
deterministic value and emit a traceable warning.

## Evaluation datasets

Maintain three version-controlled datasets.

### 1. Interpretation corpus

At least 200 queries across the supported-intent catalog. Each case records:

- query text;
- optional conversation and workspace context;
- expected normalized request fields;
- fields that must remain unset;
- expected residual text terms;
- expected warnings;
- equivalence-group identifier.

At least 30% of cases must contain paraphrases, informal grammar, reordered clauses, or common
spelling mistakes. At least 20% must exercise negation or presence/absence. At least 15% must be
ambiguous, unsupported, or deliberately adversarial to over-interpretation.

### 2. Result corpus

A deterministic seeded database containing enough positive and negative records to exercise every
supported filter. Each case records:

- query or direct `SearchRequest`;
- expected included item/revision IDs;
- expected excluded IDs;
- expected total when the set is closed;
- required ordering or top-k result where ranking matters.

Fixtures must distinguish null, blank, absent-extraction, and populated metadata. They must also
include records that match only residual text, only structured filters, both, and neither.

### 3. Ranking corpus

Representative queries with relevance judgments (`relevant`, `partially relevant`, `not relevant`)
and, where necessary, a required top result. Ranking judgments must be reviewed by a domain user and
stored independently of computed scores.

Split the combined corpus into a development set and a locked release set. Changes may be tuned using
the development set; the locked set is used only by the release-gate job. Do not lower expected
outcomes merely to make a change pass without documenting and approving the contract change.

## Measurable completion criteria

### Interpretation metrics

- **Critical-filter exact match:** at least 98% across identifiers, presence/absence, lifecycle,
  revision, dates, relationships, and numeric predicates.
- **All-field exact match:** at least 95% across the complete interpretation corpus.
- **Paraphrase consistency:** at least 98% of queries in an equivalence group produce equivalent
  normalized requests after removing non-semantic trace fields.
- **Unsafe or unsupported constraint invention:** 0 cases in the locked release set.
- **Consumed-phrase leakage:** 0 critical-filter cases leave the recognized filter phrase as a
  restrictive residual text term.
- **Required-warning recall:** 100% for locked ambiguous/unsupported cases.

### Result metrics

- **Supported exact-filter precision:** at least 99%.
- **Supported exact-filter recall:** at least 99%.
- **False-zero rate:** 0% for cases with known matching records in the locked result corpus.
- **Exclusion correctness:** 100% for explicitly forbidden records in the locked result corpus.
- **Deterministic execution parity:** the same validated `SearchRequest` always returns the same
  result membership regardless of whether it originated from the LLM or fallback interpreter.
- **Stable pagination:** no duplicate or skipped records across adjacent pages in the locked corpus.
- **Feature-absence certainty:** 100% of feature-absence results have successful extraction coverage
  for that feature; records with unknown coverage are excluded and counted separately.

### Ranking metrics

- **Top-5 recall:** at least 95% for queries with one or more judged relevant records.
- **Required top-result accuracy:** at least 90% for cases that designate one unambiguous best match.
- Record nDCG@10 as a trend metric; establish the initial baseline during Slice 1 and do not permit a
  decrease greater than 2 percentage points without an approved relevance-judgment change.

### Reliability and performance metrics

- 100% of user-reported confirmed defects have a regression test before closure.
- AI gateway failure, timeout, malformed output, or schema violation falls back without HTTP 500.
- Search trace identifies `llm`, `hybrid`, or `deterministic` interpretation mode.
- p95 deterministic interpretation time is below 50 ms in the test environment.
- p95 search execution time is below 500 ms against the agreed reference dataset and hardware.
- p95 AI-assisted search time is below 5 seconds excluding a documented gateway outage.
- No query text, token, credential, or sensitive metadata is written to ordinary application logs.

### User experience metrics

- Every result view displays the applied structured filters and residual text terms.
- Every interpretation warning is visible without opening developer tools.
- Empty results display applied constraints and offer at least one safe recovery action.
- Users can remove or correct an interpreted filter and rerun the search without rewriting the query.
- A usability check with at least five representative users achieves at least 90% task completion on
  a fixed set of ten search tasks, with no critical task below 80% completion.

## Release gates

All of the following are required to mark the phase complete:

1. Interpretation, result, ranking, reliability, and UX thresholds above pass.
2. Both AI-enabled and AI-disabled CI jobs pass.
3. The locked release corpus passes without expected-result edits in the release change.
4. `find part has no material` and its equivalence group return every seeded missing-material item
   and no specified-material item.
5. `find part has no hole`, `parts without holes`, and their equivalence group return only seeded
   parts whose successful CAD extraction confirms zero holes; parts with holes and parts with
   unknown feature coverage are excluded.
6. At least one test proves that invalid LLM output falls back to a correct result set.
7. At least one test proves that contradictory language is disclosed and does not broaden access.
8. Search telemetry and feedback retention have a documented privacy policy and configurable
   retention period.
9. Product, engineering, and one domain user sign off on the supported-intent catalog and evaluation
   results.
10. The implementation document and search capability registry match deployed behavior.
11. No open severity-1 or severity-2 search correctness defect remains.

## Delivery slices

### Slice 0 — Baseline and defect containment

- Add the known `has no material` regression at interpretation and API/database levels.
- Add equivalent missing-material and negative examples.
- Specify feature extraction coverage and add the `has no hole` equivalence group, including present,
  absent, and unknown fixtures. If authoritative feature absence is not yet available, keep the
  intent disabled with an explicit unsupported-capability warning until it is available.
- Record current corpus metrics before changing behavior.
- Make parser mode, normalized request, warnings, and result count available in the response trace.

Exit: the reported failure is fixed, covered end to end, and the baseline report is committed.

### Slice 1 — Evaluation harness

- Define corpus schemas and validation.
- Build the seeded result dataset.
- Implement interpretation, result-set, and ranking metric calculation.
- Establish development and locked release partitions.
- Publish a machine-readable and human-readable evaluation report in CI.

Exit: one command runs the full offline evaluation and fails when any release threshold is missed.

### Slice 2 — Hybrid interpretation

- Add normalization and deterministic critical-constraint extraction.
- Define merge precedence and conflict handling.
- Prevent consumed phrases from leaking into text terms.
- Expand fallback grammar using the supported-intent catalog.
- Validate LLM/fallback parity for critical intents.

Exit: interpretation and paraphrase thresholds pass on the locked corpus.

### Slice 3 — Execution and ranking correctness

- Add database-backed cases for every advertised filter.
- Verify null versus blank versus missing extraction semantics.
- Add deterministic tie-breaking and pagination tests.
- Calibrate hybrid ranking against reviewed judgments.
- Measure query plans and add indexes only where the reference workload demonstrates need.

Exit: result, ranking, and performance thresholds pass.

### Slice 4 — Explanation and correction UX

- Show structured filters, residual terms, parser mode, and warnings clearly.
- Add filter removal/editing.
- Add useful zero-result explanations and safe recovery actions.
- Capture explicit “incorrect interpretation” feedback without silently changing authoritative data.

Exit: UX acceptance criteria and representative-user task study pass.

### Slice 5 — Operational hardening

- Add aggregate metrics for parser mode, fallback rate, zero-result rate, latency, and corrections.
- Add privacy-aware sampling and configurable retention for diagnostic query records.
- Define alerts for sudden fallback, error, latency, or false-zero proxy increases.
- Document corpus maintenance and the process for converting feedback into reviewed regression cases.

Exit: operational dashboards, alerts, runbook, and release evidence are complete.

## Required test matrix

Run the full evaluation with:

- AI gateway disabled;
- AI gateway returning valid expected output;
- AI gateway returning a schema-invalid response;
- AI gateway timing out;
- empty database;
- seeded reference database;
- missing and partially processed metadata;
- first query and contextual follow-up query;
- reader, engineer, reviewer, and administrator visibility rules.

Search must never reveal an item excluded by authorization, even if its content contributes to text,
vector, relationship, count, suggestion, or explanation data.

## Observability and feedback

Prefer aggregate metrics by default. If raw query retention is enabled, explicitly configure:

- purpose;
- access control;
- redaction rules;
- retention duration;
- deletion process;
- whether queries may enter a reviewed evaluation corpus.

Recommended operational measures are query count, zero-result rate, fallback rate, correction rate,
latency distribution, parser/schema failures, and result-click rate. These are diagnostic signals, not
substitutes for the locked evaluation corpus.

## Definition of done

The Search Reliability Phase is complete only when all release gates pass in CI and on a production-
like deployment, the supported-intent catalog is reviewed, the UI permits interpretation correction,
and the evaluation report demonstrates the stated thresholds. Shipping a larger prompt, adding more
regexes, or fixing the single reported phrase does not by itself complete the phase.

## Release evidence

Repository evidence as of 2026-09-08:

| Gate | Evidence | Status |
| --- | --- | --- |
| Locked interpretation corpus | 228 locked cases; all thresholds and qualification checks pass | Pass |
| Result correctness | Seeded precision/recall/exclusion/false-zero cases for every advertised filter | Pass |
| Ranking | Top-5 recall, required-top accuracy, and nDCG@10 regression checks | Pass |
| AI failure safety | Valid, malformed, and gateway-timeout contracts prove deterministic fallback/parity | Pass |
| Authorization and pagination | Four-role visibility matrix and adjacent-page stability tests | Pass |
| UX correction | Applied-filter removal, warning/empty recovery, and incorrect-interpretation feedback | Pass |
| Privacy and operations | Raw query off by default, configurable retention, aggregate metrics, alerts | Pass |
| Automated repository checks | Backend suite, frontend type-check, and production build | Pass |
| Production-like p95 search and AI latency | Run against agreed dataset/hardware; attach report | Pending external |
| Five-user fixed-task study | Record per-task completion; overall >=90%, every critical task >=80% | Pending external |
| Product, engineering, domain sign-off | Record names, date, corpus version, commit, and decision | Pending external |
| Severity-1/2 defect review | Confirm tracker has no open search-correctness issues at release time | Pending external |

Do not change a pending external gate to pass without attaching its dated evidence. The phase can be
released only when every row passes against the exact commit and corpus version being deployed.
