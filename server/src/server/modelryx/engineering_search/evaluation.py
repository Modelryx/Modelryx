import argparse
import json
import math
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from modelryx.engineering_search.catalog_loader import active_scenario_catalog
from modelryx.engineering_search.interpreter import deterministic_interpret
from modelryx.engineering_search.models import AISearchRequest

RELEASE_THRESHOLDS = {
    "critical_filter_exact_match": 0.98,
    "case_contract_exact_match": 0.95,
    "paraphrase_consistency": 0.98,
    "required_warning_recall": 1.0,
    "consumed_phrase_leakage_count": 0,
    "deterministic_interpretation_p95_ms": 50.0,
}
UPPER_BOUND_METRICS = {
    "consumed_phrase_leakage_count",
    "deterministic_interpretation_p95_ms",
}
CORPUS_REQUIREMENTS = {
    "minimum_cases": 200,
    "minimum_paraphrase_ratio": 0.30,
    "minimum_negation_ratio": 0.20,
    "minimum_ambiguous_or_unsupported_ratio": 0.15,
}
RESULT_THRESHOLDS = {
    "supported_exact_filter_precision": 0.99,
    "supported_exact_filter_recall": 0.99,
    "exclusion_correctness": 1.0,
    "false_zero_rate": 0.0,
    "unexpected_result_count": 0,
}
RANKING_THRESHOLDS = {
    "top_5_recall": 0.95,
    "required_top_result_accuracy": 0.90,
}


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    passed: bool
    critical: bool
    warning_required: bool
    warning_matched: bool
    leakage: bool
    equivalence_group: str | None
    normalized_request: str
    failures: tuple[str, ...]
    duration_ms: float


def _path_value(value: Any, path: str) -> Any:
    for component in path.split("."):
        value = value[int(component)] if component.isdigit() else value[component]
    return value


def _canonical_request(request: dict[str, Any]) -> str:
    return json.dumps(request, sort_keys=True, separators=(",", ":"))


def _render_template(value: Any, substitutions: dict[str, str]) -> Any:
    if isinstance(value, str):
        return value.format(**substitutions)
    if isinstance(value, list):
        return [_render_template(entry, substitutions) for entry in value]
    if isinstance(value, dict):
        return {
            key: _render_template(entry, substitutions) for key, entry in value.items()
        }
    return value


def load_corpus(corpus_path: Path, schema_path: Path) -> dict[str, Any]:
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(corpus)
    expanded_cases = list(corpus["cases"])
    for matrix in corpus.get("matrices", []):
        variable_names = list(matrix["variables"])
        combinations = product(*(matrix["variables"][name] for name in variable_names))
        for combination in combinations:
            base_values = dict(zip(variable_names, combination, strict=True))
            for variant in matrix["variants"]:
                values = {**base_values, **variant.get("values", {})}
                expanded_cases.append(
                    {
                        "id": "-".join(
                            [
                                matrix["id_prefix"],
                                *(str(value) for value in combination),
                                variant["id"],
                            ]
                        ),
                        "partition": matrix["partition"],
                        "query": _render_template(variant["query"], values),
                        "critical": variant.get("critical", matrix.get("critical", False)),
                        "traits": variant.get("traits", matrix.get("traits", [])),
                        "equivalence_group": _render_template(
                            variant.get("equivalence_group"), values
                        ),
                        "expected": _render_template(variant["expected"], values),
                        "must_be_unset": _render_template(
                            variant.get("must_be_unset", []), values
                        ),
                        "expected_residual_terms": _render_template(
                            variant.get("expected_residual_terms"), values
                        ),
                        "warning_contains": _render_template(
                            variant.get("warning_contains", []), values
                        ),
                        "consumed_phrases": _render_template(
                            variant.get("consumed_phrases", []), values
                        ),
                    }
                )
    corpus = {"version": corpus["version"], "cases": expanded_cases}
    case_ids = [case["id"] for case in corpus["cases"]]
    duplicate_ids = sorted({case_id for case_id in case_ids if case_ids.count(case_id) > 1})
    if duplicate_ids:
        raise ValueError(f"duplicate corpus case IDs: {', '.join(duplicate_ids)}")
    return corpus


def evaluate_case(case: dict[str, Any]) -> CaseResult:
    started = time.perf_counter()
    request, warnings, _ = deterministic_interpret(AISearchRequest(query=case["query"]))
    duration_ms = (time.perf_counter() - started) * 1000
    request_data = request.model_dump(mode="json")
    failures: list[str] = []
    for path, expected in case.get("expected", {}).items():
        try:
            actual = _path_value(request_data, path)
        except (IndexError, KeyError, TypeError):
            actual = "<missing>"
        if actual != expected:
            failures.append(f"{path}: expected {expected!r}, got {actual!r}")
    for path in case.get("must_be_unset", []):
        try:
            actual = _path_value(request_data, path)
        except (IndexError, KeyError, TypeError):
            actual = None
        if actual not in (None, [], {}):
            failures.append(f"{path}: expected unset, got {actual!r}")
    expected_terms = case.get("expected_residual_terms")
    if expected_terms is not None and request_data["text"]["terms"] != expected_terms:
        failures.append(
            f"text.terms: expected {expected_terms!r}, got {request_data['text']['terms']!r}"
        )
    required_warnings = case.get("warning_contains", [])
    warning_matched = all(
        any(fragment.casefold() in warning.casefold() for warning in warnings)
        for fragment in required_warnings
    )
    if not warning_matched:
        failures.append(f"warnings did not contain every required fragment: {required_warnings!r}")
    consumed_phrases = case.get("consumed_phrases", [])
    residual = " ".join(request_data["text"]["terms"]).casefold()
    leakage = any(phrase.casefold() in residual for phrase in consumed_phrases)
    if leakage:
        failures.append("a consumed critical phrase leaked into residual text")
    return CaseResult(
        case_id=case["id"],
        passed=not failures,
        critical=case.get("critical", False),
        warning_required=bool(required_warnings),
        warning_matched=warning_matched,
        leakage=leakage,
        equivalence_group=case.get("equivalence_group"),
        normalized_request=_canonical_request(request_data),
        failures=tuple(failures),
        duration_ms=duration_ms,
    )


def evaluate_corpus(corpus: dict[str, Any], partition: str = "all") -> dict[str, Any]:
    catalog = active_scenario_catalog()
    cases = [
        case
        for case in corpus["cases"]
        if partition == "all" or case["partition"] == partition
    ]
    results = [evaluate_case(case) for case in cases]
    critical = [result for result in results if result.critical]
    warning_cases = [result for result in results if result.warning_required]
    groups: dict[str, list[CaseResult]] = defaultdict(list)
    for result in results:
        if result.equivalence_group:
            groups[result.equivalence_group].append(result)
    comparable_groups = [group for group in groups.values() if len(group) > 1]
    consistent_groups = [
        group for group in comparable_groups if len({row.normalized_request for row in group}) == 1
    ]
    def ratio(numerator: int, denominator: int) -> float:
        return numerator / denominator if denominator else 1.0

    sorted_durations = sorted(result.duration_ms for result in results)
    p95_index = max(0, math.ceil(len(sorted_durations) * 0.95) - 1)

    metrics = {
        "critical_filter_exact_match": ratio(
            sum(result.passed for result in critical), len(critical)
        ),
        "case_contract_exact_match": ratio(sum(result.passed for result in results), len(results)),
        "paraphrase_consistency": ratio(len(consistent_groups), len(comparable_groups)),
        "required_warning_recall": ratio(
            sum(result.warning_matched for result in warning_cases), len(warning_cases)
        ),
        "consumed_phrase_leakage_count": sum(result.leakage for result in results),
        "deterministic_interpretation_p95_ms": (
            sorted_durations[p95_index] if sorted_durations else 0.0
        ),
    }


    threshold_failures = [
        name
        for name, threshold in RELEASE_THRESHOLDS.items()
        if (
            metrics[name] > threshold
            if name in UPPER_BOUND_METRICS
            else metrics[name] < threshold
        )
    ]
    all_cases = corpus["cases"]

    def has_trait(case: dict[str, Any], trait: str) -> bool:
        if trait in case.get("traits", []):
            return True
        expected_values = case.get("expected", {}).values()
        if trait == "paraphrase":
            return bool(case.get("equivalence_group"))
        if trait == "negation":
            return any(value in ("missing", "absent") for value in expected_values)
        if trait == "ambiguous_or_unsupported":
            return bool(case.get("warning_contains"))
        return False

    def trait_ratio(trait: str) -> float:
        return ratio(
            sum(has_trait(case, trait) for case in all_cases), len(all_cases)
        )

    composition = {
        "total_cases": len(all_cases),
        "development_cases": sum(case["partition"] == "development" for case in all_cases),
        "locked_cases": sum(case["partition"] == "locked" for case in all_cases),
        "paraphrase_ratio": trait_ratio("paraphrase"),
        "negation_ratio": trait_ratio("negation"),
        "ambiguous_or_unsupported_ratio": trait_ratio("ambiguous_or_unsupported"),
    }
    qualification_failures = []
    if composition["total_cases"] < CORPUS_REQUIREMENTS["minimum_cases"]:
        qualification_failures.append("minimum_cases")
    if not composition["development_cases"]:
        qualification_failures.append("development_partition_required")
    if not composition["locked_cases"]:
        qualification_failures.append("locked_partition_required")
    for metric, requirement in (
        ("paraphrase_ratio", "minimum_paraphrase_ratio"),
        ("negation_ratio", "minimum_negation_ratio"),
        ("ambiguous_or_unsupported_ratio", "minimum_ambiguous_or_unsupported_ratio"),
    ):
        if composition[metric] < CORPUS_REQUIREMENTS[requirement]:
            qualification_failures.append(requirement)
    return {
        "corpus_version": corpus["version"],
        "catalog_version": catalog.version,
        "catalog_digest": catalog.digest,
        "generated_at": datetime.now().astimezone().isoformat(),
        "partition": partition,
        "case_count": len(results),
        "metrics": metrics,
        "thresholds": RELEASE_THRESHOLDS,
        "threshold_failures": threshold_failures,
        "corpus_composition": composition,
        "corpus_requirements": CORPUS_REQUIREMENTS,
        "qualification_failures": qualification_failures,
        "release_ready": not threshold_failures and not qualification_failures,
        "cases": [
            {"id": result.case_id, "passed": result.passed, "failures": result.failures}
            for result in results
        ],
    }


def evaluate_result_cases(
    cases: list[dict[str, Any]], execute: Callable[[str], list[str]]
) -> dict[str, Any]:
    """Evaluate result membership against independently seeded identifiers."""
    evaluated = []
    included_expected = 0
    included_found = 0
    excluded_expected = 0
    excluded_correct = 0
    false_zero_cases = 0
    unexpected_results = 0
    closed_results = 0
    closed_expected_results = 0
    for case in cases:
        actual = execute(case["query"])
        actual_set = set(actual)
        expected_included = set(case["expected_included"])
        expected_excluded = set(case.get("expected_excluded", []))
        missing = sorted(expected_included - actual_set)
        forbidden = sorted(expected_excluded & actual_set)
        unexpected = sorted(actual_set - expected_included) if case.get("closed_set") else []
        ordering_failures = []
        for before, after in case.get("ordered_before", []):
            if (
                before in actual_set
                and after in actual_set
                and actual.index(before) >= actual.index(after)
            ):
                ordering_failures.append([before, after])
        included_expected += len(expected_included)
        included_found += len(expected_included & actual_set)
        excluded_expected += len(expected_excluded)
        excluded_correct += len(expected_excluded - actual_set)
        false_zero_cases += bool(expected_included and not actual)
        unexpected_results += len(unexpected)
        if case.get("closed_set"):
            closed_results += len(actual)
            closed_expected_results += len(expected_included & actual_set)
        evaluated.append(
            {
                "id": case["id"],
                "passed": not (missing or forbidden or unexpected or ordering_failures),
                "missing": missing,
                "forbidden": forbidden,
                "unexpected": unexpected,
                "ordering_failures": ordering_failures,
            }
        )

    def ratio(numerator: int, denominator: int) -> float:
        return numerator / denominator if denominator else 1.0

    metrics = {
        "supported_exact_filter_precision": ratio(closed_expected_results, closed_results),
        "supported_exact_filter_recall": ratio(included_found, included_expected),
        "exclusion_correctness": ratio(excluded_correct, excluded_expected),
        "false_zero_rate": ratio(false_zero_cases, len(cases)),
        "unexpected_result_count": unexpected_results,
    }
    threshold_failures = [
        name
        for name, threshold in RESULT_THRESHOLDS.items()
        if (
            metrics[name] > threshold
            if name in ("false_zero_rate", "unexpected_result_count")
            else metrics[name] < threshold
        )
    ]
    return {
        "case_count": len(evaluated),
        "metrics": metrics,
        "thresholds": RESULT_THRESHOLDS,
        "threshold_failures": threshold_failures,
        "passed": all(case["passed"] for case in evaluated) and not threshold_failures,
        "cases": evaluated,
    }


def evaluate_ranking_cases(
    cases: list[dict[str, Any]], execute: Callable[[str], list[str]]
) -> dict[str, Any]:
    evaluated = []
    relevant_total = 0
    relevant_top_five = 0
    required_top_total = 0
    required_top_correct = 0
    ndcg_values = []
    for case in cases:
        actual = execute(case["query"])
        judgments = case["judgments"]
        relevant = {item_id for item_id, relevance in judgments.items() if relevance > 0}
        relevant_total += len(relevant)
        relevant_top_five += len(relevant & set(actual[:5]))
        required_top = case.get("required_top")
        if required_top:
            required_top_total += 1
            required_top_correct += bool(actual and actual[0] == required_top)
        gains = [judgments.get(item_id, 0) for item_id in actual[:10]]
        ideal = sorted(judgments.values(), reverse=True)[:10]

        def discounted_gain(values: list[int]) -> float:
            return sum((2**value - 1) / math.log2(index + 2) for index, value in enumerate(values))

        ideal_gain = discounted_gain(ideal)
        ndcg = discounted_gain(gains) / ideal_gain if ideal_gain else 1.0
        ndcg_values.append(ndcg)
        evaluated.append(
            {
                "id": case["id"],
                "required_top_correct": required_top is None
                or bool(actual and actual[0] == required_top),
                "ndcg_at_10": ndcg,
            }
        )

    def ratio(numerator: int, denominator: int) -> float:
        return numerator / denominator if denominator else 1.0

    metrics = {
        "top_5_recall": ratio(relevant_top_five, relevant_total),
        "required_top_result_accuracy": ratio(required_top_correct, required_top_total),
        "ndcg_at_10": sum(ndcg_values) / len(ndcg_values) if ndcg_values else 1.0,
    }
    threshold_failures = [
        name
        for name, threshold in RANKING_THRESHOLDS.items()
        if metrics[name] < threshold
    ]
    return {
        "case_count": len(evaluated),
        "metrics": metrics,
        "thresholds": RANKING_THRESHOLDS,
        "threshold_failures": threshold_failures,
        "passed": not threshold_failures,
        "cases": evaluated,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate deterministic engineering search")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--partition", choices=("all", "development", "locked"), default="all")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--release",
        action="store_true",
        help=(
            "also fail when corpus size, partitions, or composition are below release "
            "requirements"
        ),
    )
    args = parser.parse_args()
    report = evaluate_corpus(load_corpus(args.corpus, args.schema), args.partition)
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    failures = report["threshold_failures"]
    if args.release:
        failures = [*failures, *report["qualification_failures"]]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
