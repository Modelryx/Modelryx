"""Production credential checks independent of database fixtures."""
import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError

spec = importlib.util.spec_from_file_location(
    "production_config", Path(__file__).resolve().parents[2]
    / "server/src/server/modelryx/core/config.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Settings = module.Settings
VALID = {
    "jwt_secret": "JWT-a91Bc72De83Fg94Hi05Jk16Lm27No38P",
    "bootstrap_admin_password": "Admin-a91Bc72De83Fg94H",
    "internal_worker_key": "Worker-b82Cd73Ef64Gh95Ij06Kl17Mn28O",
}


@pytest.mark.parametrize(("field", "value"), [
    ("jwt_secret", "replace-with-at-least-32-random-characters"),
    ("jwt_secret", "x" * 40),
    ("jwt_secret", "short"),
    ("bootstrap_admin_password", "replace-with-secure-admin-password-32-chars"),
    ("bootstrap_admin_password", "admin"),
    ("bootstrap_admin_password", "abcdefgh"),
    ("internal_worker_key", "replace-with-a-long-random-worker-key"),
    ("internal_worker_key", None),
    ("internal_worker_key", "short"),
])
def test_production_rejects_unsafe_credentials(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production", **{**VALID, field: value})


def test_production_accepts_unique_credentials():
    assert Settings(_env_file=None, environment="production", **VALID).environment == "production"


def test_development_keeps_local_defaults():
    assert Settings(_env_file=None, environment="development").environment == "development"
