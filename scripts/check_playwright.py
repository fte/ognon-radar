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

Why a revision-precise check?
    A version-blind "any chromium exists" check cannot survive a Playwright
    upgrade: ``pip install -r requirements.txt`` may pull a newer Playwright
    that expects a different browser revision (chromium_headless_shell-1243,
    ...) and Playwright never auto-downloads browsers on pip upgrade.  A
    stale browser left on disk then makes the check pass while the app dies
    at runtime with::

        BrowserType.launch: Executable doesn't exist at
        .../ms-playwright/chromium_headless_shell-1243/...

    To match the revision exactly we ask the *installed* driver itself
    (``playwright install --dry-run chromium``) which directories it
    expects, then verify Playwright's own ``INSTALLATION_COMPLETE`` marker
    inside each directory — the same marker ``playwright install`` uses to
    decide whether a download is already done.
"""

import argparse
import os
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional

# "Install location:" is printed by `playwright install --dry-run` for every
# browser the command would install (same marker line across playwright
# 1.49 → current releases).
_INSTALL_LOCATION_RE = re.compile(r"^\s*Install location:\s*(.+?)\s*$")

# Marker written by playwright inside a browser directory once the download
# has fully completed (see playwright-core registry/browserFetcher.ts).
COMPLETE_MARKER = "INSTALLATION_COMPLETE"


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


def _query_expected_directories(python: Optional[str] = None) -> List[pathlib.Path]:
    """Ask the installed driver which Chromium directories it expects.

    Runs ``python -m playwright install --dry-run chromium`` — the dry-run
    output lists exactly the browsers (and their install locations) that
    ``playwright install chromium`` would download, so the answer is always
    in sync with the playwright version on disk.  Only directories whose
    basename starts with ``chromium`` are kept: the dry-run also lists
    ``ffmpeg`` on some versions, which is not needed for screenshots.

    Returns an empty list when the driver cannot be interrogated.
    """
    executable = python or sys.executable
    try:
        proc = subprocess.run(
            [executable, "-m", "playwright", "install", "--dry-run", "chromium"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []

    directories: List[pathlib.Path] = []
    seen = set()
    for line in proc.stdout.splitlines():
        match = _INSTALL_LOCATION_RE.match(line)
        if not match:
            continue
        directory = pathlib.Path(match.group(1))
        # Deduplicate: the check result must not depend on the driver
        # repeating a directory (e.g. chromium listed twice) — the marker
        # is checked once per unique expected location.
        if directory in seen:
            continue
        seen.add(directory)
        if directory.name.startswith("chromium"):
            directories.append(directory)
    return directories


def _check_via_glob(cache_root: pathlib.Path) -> bool:
    """
    Last-resort fallback: scan the Playwright cache for any existing Chromium
    binary.

    Tries several known layouts (Linux, macOS, Windows) so the check works
    regardless of the platform.  If none match, returns False.

    This is deliberately **imprecise**: it cannot tell whether the browser it
    finds matches the revision the installed ``playwright`` package expects,
    so a stale browser may slip through.  It is only reached when the
    driver-based check (:func:`evaluate`) could not interrogate the driver.
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


@dataclass
class CheckResult:
    """Outcome of the Chromium availability check.

    ``installed`` carries the answer; the remaining fields feed the CLI's
    diagnostics so ``main()`` renders the result instead of re-deriving it.
    """

    installed: bool
    # "driver" (revision-precise) or "glob" (version-blind fallback scan).
    strategy: str
    expected_directories: List[pathlib.Path]
    missing_directories: List[pathlib.Path]
    cache_root: pathlib.Path


def evaluate(cache_root: Optional[pathlib.Path] = None, python: Optional[str] = None) -> CheckResult:
    """Single source of truth: is the expected Chromium browser installed?

    Strategy 1 — revision-precise: ask the installed driver which chromium
    directories it expects and verify each ``INSTALLATION_COMPLETE`` marker.
    Strategy 2 — fallback: version-blind directory scan, only when the driver
    could not be interrogated.
    """
    if cache_root is None:
        cache_root = _resolve_cache_root()

    expected = _query_expected_directories(python)
    if expected:
        missing = [d for d in expected if not (d / COMPLETE_MARKER).exists()]
        return CheckResult(
            installed=not missing,
            strategy="driver",
            expected_directories=list(expected),
            missing_directories=missing,
            cache_root=cache_root,
        )

    return CheckResult(
        installed=_check_via_glob(cache_root),
        strategy="glob",
        expected_directories=[],
        missing_directories=[],
        cache_root=cache_root,
    )


def chromium_installed(cache_root: Optional[pathlib.Path] = None, python: Optional[str] = None) -> bool:
    """Return ``True`` when the expected Chromium browser exists on disk."""
    return evaluate(cache_root, python).installed


def _print_diagnostics(result: CheckResult) -> None:
    """Write human-readable diagnostics to stderr (used by the CLI)."""
    if result.strategy == "driver":
        missing = set(result.missing_directories)
        for directory in result.expected_directories:
            state = "MISSING" if directory in missing else "present"
            print(f"  {directory}  ({state})", file=sys.stderr)
        if result.installed:
            print(
                "  Status     : Chromium found (expected revision(s) installed)",
                file=sys.stderr,
            )
        else:
            print(
                "  Status     : Chromium MISSING — expected revision(s) not installed",
                file=sys.stderr,
            )
        return
    # Fallback path (glob scan, revision not verified).
    print(f"  Cache root : {result.cache_root}", file=sys.stderr)
    if result.installed:
        print(
            "  Status     : Chromium found (via directory-scan fallback — "
            "revision NOT verified)",
            file=sys.stderr,
        )
    else:
        print("  Status     : Chromium MISSING", file=sys.stderr)


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

    result = evaluate()
    if args.verbose:
        _print_diagnostics(result)
    return 0 if result.installed else 1


if __name__ == "__main__":
    sys.exit(main())