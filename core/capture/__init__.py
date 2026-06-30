from config import settings
from core.capture.base import CaptureProvider, CaptureResult


def get_capture_provider() -> CaptureProvider:
    if settings.capture_backend == "warc":
        from core.capture.warc_provider import WARCCaptureProvider
        from core.tor_client import tor_client
        return WARCCaptureProvider(
            tor_client=tor_client,
            output_dir=settings.capture_output_dir,
        )
    raise ValueError(f"Unknown capture backend: {settings.capture_backend!r}. Supported: warc")


__all__ = ["CaptureProvider", "CaptureResult", "get_capture_provider"]
