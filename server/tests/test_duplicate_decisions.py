import uuid

import pytest
from modelryx.api.duplicate_checks import DuplicateDecisionRequest
from pydantic import ValidationError


def test_candidate_decisions_require_candidate_id() -> None:
    for decision in ("USE_EXISTING", "NOT_DUPLICATE"):
        with pytest.raises(ValidationError):
            DuplicateDecisionRequest(decision=decision)


def test_create_new_can_be_a_check_level_decision() -> None:
    decision = DuplicateDecisionRequest(decision="CREATE_NEW", comment="Intentional new item")
    assert decision.candidate_id is None


def test_decision_rejects_unknown_fields_and_limits_comment() -> None:
    with pytest.raises(ValidationError):
        DuplicateDecisionRequest(
            decision="USE_EXISTING",
            candidate_id=uuid.uuid4(),
            unexpected=True,
        )
    with pytest.raises(ValidationError):
        DuplicateDecisionRequest(decision="CREATE_NEW", comment="x" * 2001)
