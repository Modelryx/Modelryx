from datetime import UTC, datetime
from string import Formatter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from modelryx.models import Item, ItemNumberTemplate


def render_item_number(template: str, sequence: int, *, year: int | None = None) -> str:
    return template.format(sequence=sequence, year=year or datetime.now(UTC).year)


def validate_item_number_pattern(pattern: str) -> str:
    pattern = pattern.strip()
    try:
        fields = {field for _, field, _, _ in Formatter().parse(pattern) if field}
    except ValueError as exc:
        raise ValueError("Template format is invalid") from exc
    if "sequence" not in fields or not fields <= {"sequence", "year"}:
        raise ValueError("Template must contain {sequence} and may contain {year}")
    try:
        rendered = render_item_number(pattern, 1, year=2000)
    except (KeyError, ValueError) as exc:
        raise ValueError("Template format is invalid") from exc
    if not rendered or len(rendered) > 100 or not rendered[0].isalnum() or any(
        not (character.isalnum() or character in "._-") for character in rendered
    ):
        raise ValueError(
            "Template must produce only letters, numbers, dots, dashes, or underscores"
        )
    return pattern


def allocate_item_number(
    db: Session, item_type: str, template_id: str | None = None
) -> tuple[str, ItemNumberTemplate]:
    """Allocate a unique number; consumed sequence values are intentionally never reused."""
    query = select(ItemNumberTemplate).where(ItemNumberTemplate.is_enabled.is_(True))
    query = query.where(
        ItemNumberTemplate.is_selected.is_(True)
        if template_id is None else ItemNumberTemplate.id == template_id
    )
    template = db.scalar(query.with_for_update())
    if template is None:
        raise RuntimeError("The selected Item-number template is unavailable")

    for _ in range(1000):
        if item_type == "PART":
            sequence = template.part_next_value
            template.part_next_value += 1
            pattern = template.part_pattern
        elif item_type == "ASSEMBLY":
            sequence = template.assembly_next_value
            template.assembly_next_value += 1
            pattern = template.assembly_pattern
        else:
            raise ValueError("Unsupported Item type")
        value = render_item_number(pattern, sequence)
        exists = db.scalar(select(Item.id).where(func.lower(Item.part_number) == value.casefold()))
        if exists is None:
            return value, template
    raise RuntimeError("Unable to allocate a unique Item number")
