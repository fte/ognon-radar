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
from dataclasses import dataclass, field
from typing import List, Optional

# "Install location:" is printed by `playwright install --dry-run` for every
# browser the command would install (same marker line across playwright
# 1.49 → current releases).
_INSTALL_LOCATION_RE = re.compile(r"^\s*Install location:\s*(.+?)\s*$")

# Marker written by playwright inside a browser directory once the download
# has fully completed (see playwright-core registry/browserFetcher.ts).
COMPLETE_MARKER = "INSTALLATION_COMPLETE"

# Executable layouts per platform, matching what `playwright install` produces
# inside a browser directory.  Used both by the version-blind fallback scan
# (_check_via_glob) and by the system-library probe (_find_executables), so the
# two always agree on where a binary lives.
_EXECUTABLE_PATTERNS = [
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _package_local_browsers_root() -> Optional[pathlib.Path]:
    """Directory Playwright uses when ``PLAYWRIGHT_BROWSERS_PATH=0``.

    The driver bundles playwright-core at ``<playwright>/driver/package`` and
    resolves the special ``0`` value to ``packageRoot/.local-browsers`` (see
    playwright-core registry/index.ts).  Returns ``None`` when the
    ``playwright`` package cannot be located.
    """
    try:
        import playwright  # noqa: PLC0415
    except ImportError:
        return None
    package_root = pathlib.Path(playwright.__file__).resolve().parent / "driver" / "package"
    return package_root / ".local-browsers"


def _default_registry_directory(
    os_name: Optional[str] = None, platform_name: Optional[str] = None
) -> pathlib.Path:
    """Mirror the driver's default cache directory (no PLAYWRIGHT_BROWSERS_PATH).

    ``os_name``/``platform_name`` default to the running system but are
    injectable so every platform branch can be exercised without spoofing
    module globals (pathlib picks its path class from the real ``os.name``).
    """
    os_name = os_name or os.name
    platform_name = platform_name or sys.platform
    home = pathlib.Path.home()
    if os_name == "nt":
        base = pathlib.Path(os.environ.get("LOCALAPPDATA") or (home / "AppData" / "Local"))
    elif platform_name == "darwin":
        base = home / "Library" / "Caches"
    else:
        base = pathlib.Path(os.environ.get("XDG_CACHE_HOME") or (home / ".cache"))
    return base / "ms-playwright"


def _resolve_cache_root() -> Optional[pathlib.Path]:
    """Return the Playwright browser cache directory, mirroring the driver.

    ``PLAYWRIGHT_BROWSERS_PATH=0`` is special in Playwright: it selects the
    package-local ``.local-browsers`` directory instead of a literal ``0``
    path.  A relative value is made absolute the same way the driver does
    (against ``$INIT_CWD`` if set, otherwise the cwd).  Returns ``None`` when
    the value cannot be resolved — only possible for the ``0`` mode when the
    ``playwright`` package is not importable; the caller must then treat the
    check as inconclusive rather than scan a wrong directory.
    """
    raw = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if raw == "0":
        return _package_local_browsers_root()
    if raw:
        root = pathlib.Path(raw)
        if not root.is_absolute():
            base = pathlib.Path(os.environ.get("INIT_CWD") or os.getcwd())
            root = base / root
        return root
    return _default_registry_directory()


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
    patterns = _EXECUTABLE_PATTERNS
    for pattern in patterns:
        if sorted(cache_root.glob(pattern)):
            return True
    return False


def _find_executables(directory: pathlib.Path) -> List[pathlib.Path]:
    """Locate the Chromium binary(ies) inside a single browser directory.

    The executable patterns are expressed relative to the cache root (they
    include the ``chromium_headless_shell-*`` revision prefix), so glob the
    parent — the actual browser cache root — and keep only matches that fall
    under ``directory``.
    """
    found: List[pathlib.Path] = []
    for pattern in _EXECUTABLE_PATTERNS:
        for match in directory.parent.glob(pattern):
            if match.is_relative_to(directory):
                found.append(match)
    return sorted(found)


def _missing_shared_libraries(executable: pathlib.Path) -> List[str]:
    """Return system libraries the binary needs but cannot load (Linux only).

    ``playwright install chromium`` only downloads binaries — the OS-level
    dependencies come from ``playwright install-deps chromium`` (apt-get).  A
    present ``INSTALLATION_COMPLETE`` marker therefore does not mean the
    browser can actually launch: on a minimal host a missing lib (e.g.
    ``libasound.so.2``) makes the screenshot job die at
    ``BrowserType.launch``, as seen in production:

        error while loading shared libraries: libasound.so.2:
        cannot open shared object file: No such file or directory

    Run ``ldd`` on the binary and collect every dependency reported as
    "not found".  Returns an empty list when ``ldd`` is unavailable (the
    check cannot judge — it must not block the deploy on that basis) or on
    non-Linux platforms.
    """
    if sys.platform != "linux" or os.name != "posix":
        return []
    try:
        proc = subprocess.run(
            ["ldd", str(executable)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    missing: List[str] = []
    for line in proc.stdout.splitlines():
        if "not found" in line:
            name = line.split("=>")[0].strip()
            if name:
                missing.append(name)
    return missing


@dataclass
class CheckResult:
    """Outcome of the Chromium availability check.

    ``installed`` carries the answer; the remaining fields feed the CLI's
    diagnostics so ``main()`` renders the result instead of re-deriving it.
    """

    installed: bool
    # "driver" (revision-precise), "glob" (version-blind fallback scan), or
    # "unresolved" (cache root could not be determined — fallback disabled).
    strategy: str
    expected_directories: List[pathlib.Path]
    missing_directories: List[pathlib.Path]
    cache_root: Optional[pathlib.Path]
    # System libraries (e.g. libasound.so.2) the installed binary cannot
    # load.  Non-empty only when every expected marker was found but the
    # binary is nonetheless unlaunchable — the actionable fix is
    # `playwright install-deps chromium`.
    missing_libraries: List[str] = field(default_factory=list)


def evaluate(cache_root: Optional[pathlib.Path] = None, python: Optional[str] = None) -> CheckResult:
    """Single source of truth: is the expected Chromium browser installed?

    Strategy 1 — revision-precise: ask the installed driver which chromium
    directories it expects and verify each ``INSTALLATION_COMPLETE`` marker.
    When every marker is present, additionally probe (Linux only) that the
    binary can actually be launched — a missing system library such as
    ``libasound.so.2`` makes the marker lie.  Strategy 2 — fallback:
    version-blind directory scan, only when the driver could not be
    interrogated.
    """
    if cache_root is None:
        cache_root = _resolve_cache_root()

    expected = _query_expected_directories(python)
    if expected:
        missing = [d for d in expected if not (d / COMPLETE_MARKER).exists()]
        missing_libraries: List[str] = []
        for directory in expected:
            if directory in missing:
                continue
            for executable in _find_executables(directory):
                missing_libraries.extend(_missing_shared_libraries(executable))
        return CheckResult(
            installed=not missing and not missing_libraries,
            strategy="driver",
            expected_directories=list(expected),
            missing_directories=missing,
            cache_root=cache_root,
            missing_libraries=sorted(set(missing_libraries)),
        )

    if cache_root is None:
        # The driver is silent and the browser cache cannot be located
        # (PLAYWRIGHT_BROWSERS_PATH=0 but the playwright package is not
        # importable).  Scanning anything would be guessing — report missing
        # (safe direction: the deploy will run `playwright install chromium`,
        # which resolves the location itself).
        return CheckResult(
            installed=False,
            strategy="unresolved",
            expected_directories=[],
            missing_directories=[],
            cache_root=None,
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
        elif result.missing_libraries:
            print(
                "  Status     : Chromium present but MISSING SYSTEM LIBRARIES — "
                "run 'playwright install-deps chromium'",
                file=sys.stderr,
            )
            for lib in result.missing_libraries:
                print(f"    - {lib}", file=sys.stderr)
        else:
            print(
                "  Status     : Chromium MISSING — expected revision(s) not installed",
                file=sys.stderr,
            )
        return
    if result.strategy == "unresolved":
        print(
            "  Cache root : unresolvable (PLAYWRIGHT_BROWSERS_PATH=0 and "
            "playwright package not importable)",
            file=sys.stderr,
        )
        print(
            "  Status     : Chromium MISSING — fallback disabled, cannot verify",
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