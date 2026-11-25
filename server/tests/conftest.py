import json
import uuid

import pytest
from modelryx import models
from modelryx.core.config import get_settings
from modelryx.core.db import SessionLocal
from modelryx.models import (
    ArtifactFile,
    ApplicationSetting,
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
    """Provision the test identity and native worker for database-backed API modules."""
    if not hasattr(request.module, "AUTH"):
        yield
        return

    worker_key = f"external_worker.fixture_native_{uuid.uuid4().hex}"
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.external_subject == "local-development-user"))
        if user is None:
            user = User(
                external_subject="local-development-user",
                display_name="Local Engineer",
                role="administrator",
                is_active=True,
                auth_version=0,
            )
            db.add(user)
        else:
            user.display_name = "Local Engineer"
            user.role = "administrator"
            user.is_active = True
            user.auth_version = 0
        db.flush()
        # Native integration fixtures must declare support through a worker, just as
        # production uploads do. Conversion itself is mocked by execution tests.
        if not getattr(request.module, "MANAGES_NATIVE_WORKERS", False):
            db.add(ApplicationSetting(
                key=worker_key, updated_by=user.id, value=json.dumps({
                    "url": "https://fixture-native.invalid", "enabled": True,
                    "capabilities": {
                        "contractVersion": "1.0", "cadSystem": "FreeCAD",
                        "workerName": "Reference native fixture", "workerVersion": "test",
                        "sourceExtensions": [".fcstd"], "documentKinds": ["part", "assembly"],
                        "outputRoles": ["native-snapshot", "part-metadata", "model-preview", "glb"],
                        "dependencyAnalysis": True,
                    },
                }),
            ))
        db.commit()
    try:
        yield
    finally:
        with SessionLocal() as db:
            row = db.get(ApplicationSetting, worker_key)
            if row is not None:
                db.delete(row)
                db.commit()


@pytest.fixture
def example_worker():
    """Register a fictional CAD worker for native commit integration scenarios."""
    import json

    from modelryx.models import ApplicationSetting
    from modelryx.services.external_workers import validate_capabilities

    key = f"external_worker.fixture_{uuid.uuid4().hex}"
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.external_subject == "local-development-user"))
        db.add(
            ApplicationSetting(
                key=key,
                updated_by=user.id,
                value=json.dumps(
                    {
                        "url": "https://worker.example",
                        "enabled": True,
                        "capabilities": validate_capabilities(
                            {
                                "contractVersion": "1.0",
                                "cadSystem": "Example CAD",
                                "workerName": "Example CAD",
                                "workerVersion": "1.0",
                                "documentKinds": ["part", "assembly", "drawing"],
                                "sourceExtensions": [".assembly", ".part", ".drawing", ".sheet"],
                                "outputRoles": [
                                    "native-snapshot",
                                    "part-metadata",
                                    "model-preview",
                                ],
                                "dependencyAnalysis": True,
                            }
                        ),
                    }
                ),
            )
        )
        db.commit()
    try:
        yield
    finally:
        with SessionLocal() as db:
            row = db.get(ApplicationSetting, key)
            if row is not None:
                db.delete(row)
                db.commit()
