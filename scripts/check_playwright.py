#!/usr/bin/env python3
"""
Check whether the correct Playwright Chromium browser is installed.

Exit codes:
    0 — Chromium is present and matches the installed Playwright version.
    1 — Chromium is missing (or version mismatch).

Usage:
    ./scripts/check_playwright.py
    ./scripts/check_playwright.py --verbose

This script is used by scripts/deploy_live.sh to decide whether to
run `playwright install chromium`.  Keeping the check in a separate
file makes it easier to test and maintain.
"""

import argparse
import os
import pathlib
import sys
from typing import Optional

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_cache_root() -> pathlib.Path:
    """Return the Playwright browser cache directory.

    Honour the PLAYWRIGHT_BROWSERS_PATH env var (same as Playwright itself),
    falling back to the default ``~/.cache/ms-playwright``.
    """
    raw = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if raw:
        return pathlib.Path(raw)
    return pathlib.Path.home() / ".cache" / "ms-playwright"


# ---------------------------------------------------------------------------
# Check strategies (tried in order, first match wins)
# ---------------------------------------------------------------------------

def _check_via_internal_api(cache_root: pathlib.Path) -> bool:
    """
    Use Playwright's private ``compute_driver_executable_path`` to get the
    *exact* expected binary path for the **currently installed** playwright
    Python package, then check whether that path exists.

    .. caution::

       This uses the private ``playwright._impl._driver`` module which is
       not part of Playwright's public API and may break across releases.
       If it does, the fallback in :func:`_check_via_glob` will still work.
    """
    try:
        from playwright._impl._driver import (  # type: ignore[import-untyped]
            compute_driver_executable_path,
        )
    except ImportError:
        return False

    try:
        expected = pathlib.Path(compute_driver_executable_path())
        # compute_driver_executable_path already honours PLAYWRIGHT_BROWSERS_PATH
        # internally via _get_cache_directory, so the returned path is correct
        # regardless of whether the env var is set.
        return expected.exists()
    except Exception:
        return False


def _check_via_glob(cache_root: pathlib.Path) -> bool:
    """
    Fallback: scan the Playwright cache for any existing Chromium binary.

    Tries several known layouts (Linux, macOS, Windows) so the check works
    regardless of the platform.  If none match, returns False.

    This is less precise (any version, not necessarily the one the installed
    ``playwright`` package expects), but doesn't depend on any private API.
    """
    patterns = [
        # Linux (headless shell – default for install chromium)
        "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
        # Linux (full Chromium)
        "chromium-*/chrome-linux/chrome",
        # macOS
        "chromium_headless_shell-*/chrome-headless-shell-mac-arm64/chrome-headless-shell",
        "chromium_headless_shell-*/chrome-headless-shell-mac-x64/chrome-headless-shell",
        "chromium-*/chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium",
        "chromium-*/chrome-mac-x64/Chromium.app/Contents/MacOS/Chromium",
        # Windows (WSL / cross-platform)
        "chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe",
        "chromium-*/chrome-win64/chrome.exe",
    ]
    for pattern in patterns:
        if sorted(cache_root.glob(pattern)):
            return True
    return False


def chromium_installed(cache_root: Optional[pathlib.Path] = None) -> bool:
    """Return ``True`` when the expected Chromium binary exists on disk."""
    if cache_root is None:
        cache_root = _resolve_cache_root()

    # Strategy 1 — precise version check (uses private API).
    if _check_via_internal_api(cache_root):
        return True

    # Strategy 2 — glob-based fallback (public, but imprecise).
    return _check_via_glob(cache_root)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print diagnostic information",
    )
    args = parser.parse_args()

    cache_root = _resolve_cache_root()
    found = chromium_installed(cache_root)

    if args.verbose:
        print(f"  Cache root : {cache_root}", file=sys.stderr)
        if found:
            print("  Status     : Chromium found (version matches)" if _check_via_internal_api(cache_root)
                  else "  Status     : Chromium found (via directory-scan fallback)",
                  file=sys.stderr)
        else:
            print("  Status     : Chromium MISSING", file=sys.stderr)

    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main())
