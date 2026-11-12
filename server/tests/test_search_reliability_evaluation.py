from pathlib import Path

from modelryx.engineering_search.evaluation import evaluate_corpus, load_corpus

SEARCH_DIR = Path(__file__).parent / "search"


def test_search_reliability_corpus_schema_and_thresholds() -> None:
    corpus = load_corpus(
        SEARCH_DIR / "search_reliability_v1.json",
        SEARCH_DIR / "search_reliability.schema.json",
    )
    report = evaluate_corpus(corpus)
    assert report["case_count"] == 252
    assert report["threshold_failures"] == []
    assert report["release_ready"] is True
    assert report["qualification_failures"] == []
    assert report["corpus_composition"] == {
        "total_cases": 252,
        "development_cases": 9,
        "locked_cases": 243,
        "paraphrase_ratio": 190 / 252,
        "negation_ratio": 113 / 252,
        "ambiguous_or_unsupported_ratio": 38 / 252,
    }
    assert all(case["passed"] for case in report["cases"]), report


def test_locked_partition_is_independently_releasable() -> None:
    corpus = load_corpus(
        SEARCH_DIR / "search_reliability_v1.json",
        SEARCH_DIR / "search_reliability.schema.json",
    )
    report = evaluate_corpus(corpus, "locked")
    assert report["case_count"] == 243
    assert report["threshold_failures"] == []


def test_duplicate_case_ids_are_rejected(tmp_path: Path) -> None:
    corpus_path = tmp_path / "duplicate.json"
    corpus_path.write_text(
        '{"version":"1","cases":['
        '{"id":"same","partition":"locked","query":"find parts","expected":{}},'
        '{"id":"same","partition":"development","query":"find items","expected":{}}]}',
        encoding="utf-8",
    )
    try:
        load_corpus(corpus_path, SEARCH_DIR / "search_reliability.schema.json")
    except ValueError as error:
        assert "duplicate corpus case IDs" in str(error)
    else:
        raise AssertionError("duplicate case IDs were accepted")
