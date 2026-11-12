from copy import deepcopy

import pytest
from modelryx.metadata import EngineeringMetadataError, validate_engineering_metadata
from test_metadata_schema import valid_metadata


def test_accepts_consistent_part_metadata() -> None:
    metadata = valid_metadata()
    assert validate_engineering_metadata(metadata) is metadata


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda value: value["geometry"]["topology"].__setitem__("solid_count", 0),
            "positive volume but solid_count is zero",
        ),
        (
            lambda value: value["geometry"]["bounding_box"].__setitem__("z_mm", 0),
            "three positive bounding-box dimensions",
        ),
        (
            lambda value: value["geometry"]["shape"].__setitem__("volume_mm3", 200000),
            "volume exceeds its bounding-box volume",
        ),
        (
            lambda value: value["assembly"].update({"is_assembly": False, "instance_count": 2}),
            "non-assembly metadata cannot report component instances",
        ),
    ],
)
def test_rejects_inconsistent_engineering_values(mutate, message: str) -> None:
    metadata = deepcopy(valid_metadata())
    mutate(metadata)
    with pytest.raises(EngineeringMetadataError, match=message):
        validate_engineering_metadata(metadata)


def test_allows_assembly_instance_volume_to_exceed_outer_box() -> None:
    metadata = valid_metadata()
    metadata["identity"]["kind"] = "assembly"
    metadata["assembly"] = {
        "is_assembly": True,
        "instance_count": 10,
        "unique_component_count": 1,
    }
    metadata["geometry"]["shape"]["volume_mm3"] = 2_000_000
    assert validate_engineering_metadata(metadata) is metadata


def test_allows_overlapping_multi_solid_part_volume_to_exceed_outer_box() -> None:
    metadata = valid_metadata()
    metadata["geometry"]["topology"]["solid_count"] = 2
    metadata["geometry"]["shape"]["volume_mm3"] = 2_000_000

    assert validate_engineering_metadata(metadata) is metadata
