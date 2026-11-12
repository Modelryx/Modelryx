import pytest
from modelryx.services.item_numbers import render_item_number, validate_item_number_pattern


def test_renders_configurable_item_number_templates() -> None:
    assert validate_item_number_pattern("PRT-{year}-{sequence:05d}") == "PRT-{year}-{sequence:05d}"
    assert render_item_number("PRT-{year}-{sequence:05d}", 42, year=2026) == "PRT-2026-00042"
    assert render_item_number("ASM-{sequence:04d}", 7) == "ASM-0007"


def test_rejects_templates_without_sequence() -> None:
    with pytest.raises(ValueError, match="must contain"):
        validate_item_number_pattern("PART-{year}")


def test_rejects_templates_that_render_unsafe_numbers() -> None:
    with pytest.raises(ValueError, match="only letters"):
        validate_item_number_pattern("PART {sequence:06d}")
