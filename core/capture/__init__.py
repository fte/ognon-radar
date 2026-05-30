from config import settings
from core.capture.base import CaptureProvider, CaptureResult
from core.tor_client import tor_client


def get_capture_provider() -> CaptureProvider:
    backend = settings.capture.get("backend", "warc")
    if backend == "warc":
        from core.capture.warc_provider import WARCCaptureProvider
        return WARCCaptureProvider(
            tor_client=tor_client,
            output_dir=settings.capture.get("output_dir", "/app/data/captures"),
        )
    raise ValueError(f"Unknown capture backend: {backend!r}. Supported: warc")


__all__ = ["CaptureProvider", "CaptureResult", "get_capture_provider"]
