import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from modelryx.api.ui_views import LibraryViewSettings, SavedViewWrite, create_view, editable


def test_non_admin_cannot_publish_shared_library_view() -> None:
    payload = SavedViewWrite(name="Team parts", scope="SHARED", settings=LibraryViewSettings())
    principal = SimpleNamespace(role="engineer")
    user = SimpleNamespace(id=uuid.uuid4())
    with patch("modelryx.api.ui_views.user_for_principal", return_value=user):
        with pytest.raises(HTTPException) as error:
            create_view(payload, SimpleNamespace(), MagicMock(), principal)
    assert error.value.status_code == 403


def test_shared_view_is_admin_managed_and_personal_view_is_owner_managed() -> None:
    owner_id, stranger_id = uuid.uuid4(), uuid.uuid4()
    shared = SimpleNamespace(scope="SHARED", owner_id=owner_id)
    personal = SimpleNamespace(scope="PERSONAL", owner_id=owner_id)
    assert editable(shared, stranger_id, True)
    assert not editable(shared, owner_id, False)
    assert editable(personal, owner_id, False)
    assert not editable(personal, stranger_id, True)
