from pathlib import Path

import pytest
from modelryx.services.storage import LocalStorageProvider


def test_local_storage_round_trip(tmp_path: Path) -> None:
    storage = LocalStorageProvider(tmp_path)
    upload = storage.create_upload("parts/one/file.step")
    upload.write_bytes(b"step-data")
    storage.complete_upload("parts/one/file.step")

    assert storage.exists("parts/one/file.step")
    with storage.open_download("parts/one/file.step") as stream:
        assert stream.read() == b"step-data"


def test_local_storage_rejects_path_traversal(tmp_path: Path) -> None:
    storage = LocalStorageProvider(tmp_path)
    with pytest.raises(ValueError):
        storage.create_upload("../outside")
