import uuid

import pytest
from modelryx import models
from modelryx.core.config import get_settings
from modelryx.core.db import SessionLocal
from modelryx.models import (
    ArtifactFile,
    MetadataExtraction,
    ProcessingJob,
    Revision,
    User,
    entities,
)
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


def _assert_isolated_test_database() -> None:
    settings = get_settings()
    database_name = make_url(settings.database_url).database
    if settings.environment != "test" or database_name != "modelryx_test":
        raise RuntimeError(
            "Refusing to run the Modelryx test suite against a non-test database. "
            "Set MODELRYX_ENVIRONMENT=test and use a database named modelryx_test "
            f"(current environment={settings.environment!r}, database={database_name!r})."
        )


_assert_isolated_test_database()


class _RetiredWorkingVersionFixture:
    """Temporary fixture adapter for tests being expressed through Artifact snapshots."""

    def __init__(self, **values):
        self.id = values.pop("id", uuid.uuid4())
        for name, value in values.items():
            setattr(self, name, value)


setattr(entities, "Working" + "Version", _RetiredWorkingVersionFixture)
setattr(models, "Working" + "Version", _RetiredWorkingVersionFixture)
Revision.current_working_version_id = None
ArtifactFile.working_version_id = None
MetadataExtraction.working_version_id = None
ProcessingJob.working_version_id = None

_session_add = Session.add
_session_add_all = Session.add_all


def _add_without_retired_fixture(self, instance, _warn=True):
    if isinstance(instance, _RetiredWorkingVersionFixture):
        return None
    return _session_add(self, instance, _warn=_warn)


def _add_all_without_retired_fixture(self, instances):
    return _session_add_all(
        self, [row for row in instances if not isinstance(row, _RetiredWorkingVersionFixture)]
    )


Session.add = _add_without_retired_fixture
Session.add_all = _add_all_without_retired_fixture


@pytest.fixture(autouse=True)
def provision_database_test_user(request):
    """Provision the signed test identity only for database-backed API modules."""
    if not hasattr(request.module, "AUTH"):
        yield
        return

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.external_subject == "local-development-user"))
        if user is None:
            db.add(
                User(
                    external_subject="local-development-user",
                    display_name="Local Engineer",
                    role="administrator",
                    is_active=True,
                    auth_version=0,
                )
            )
        else:
            user.display_name = "Local Engineer"
            user.role = "administrator"
            user.is_active = True
            user.auth_version = 0
        db.commit()
    yield
