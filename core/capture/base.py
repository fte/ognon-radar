from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class CaptureResult:
    job_id: str
    url: str
    pages_captured: int
    assets_captured: int
    size_bytes: int
    storage_key: str  # local path or S3/R2 object key


class CaptureProvider(ABC):
    """Abstract capture backend. Swap implementations without touching the API layer."""

    @abstractmethod
    def capture(
        self,
        job_id: str,
        start_url: str,
        max_pages: int,
        max_depth: int,
        timeout: int,
        max_size_mb: int = 500,
    ) -> CaptureResult: ...

    @abstractmethod
    def get_download_url(self, storage_key: str) -> str:
        """Return a URL or local path from which the archive can be served."""
        ...

    @abstractmethod
    def delete(self, storage_key: str) -> None: ...
