from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO


class StorageProvider(ABC):
    @abstractmethod
    def create_upload(self, storage_key: str) -> Path: ...

    @abstractmethod
    def complete_upload(self, storage_key: str) -> None: ...

    @abstractmethod
    def open_download(self, storage_key: str) -> BinaryIO: ...

    @abstractmethod
    def exists(self, storage_key: str) -> bool: ...

    @abstractmethod
    def delete(self, storage_key: str) -> None: ...


class LocalStorageProvider(StorageProvider):
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, storage_key: str) -> Path:
        path = (self.root / storage_key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Storage key escapes the configured root")
        return path

    def create_upload(self, storage_key: str) -> Path:
        path = self._path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def path_for(self, storage_key: str) -> Path:
        """Resolve a validated storage key for local-provider maintenance operations."""
        return self._path(storage_key)

    def partial_path(self, storage_key: str) -> Path:
        return self._path(storage_key).with_suffix(self._path(storage_key).suffix + ".partial")

    def complete_upload(self, storage_key: str) -> None:
        if not self.exists(storage_key):
            raise FileNotFoundError(storage_key)

    def open_download(self, storage_key: str) -> BinaryIO:
        return self._path(storage_key).open("rb")

    def exists(self, storage_key: str) -> bool:
        return self._path(storage_key).is_file()

    def delete(self, storage_key: str) -> None:
        self._path(storage_key).unlink(missing_ok=True)
