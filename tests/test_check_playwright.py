"""
Unit tests for scripts/check_playwright.py.

These cover the revision-precise browser check used by scripts/deploy_live.sh:
parsing of `playwright install --dry-run` output, and the decision logic that
must catch a Playwright upgrade whose expected browser revision is missing
from disk (the "Executable doesn't exist" production failure).
"""
import os
import pathlib
import subprocess
import sys
from types import SimpleNamespace

import pytest

import scripts.check_playwright as cpw


# ── Helpers ───────────────────────────────────────────────────────────────

def _fake_proc(stdout: str = "", returncode: int = 0):
    return SimpleNamespace(stdout=stdout, returncode=returncode)


def _run_result(stdout: str, returncode: int = 0):
    """Monkeypatch subprocess.run inside check_playwright and yield the dirs."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cpw.subprocess, "run", lambda *a, **k: _fake_proc(stdout, returncode))
        return cpw._query_expected_directories(python="python")


# ── Parsing `playwright install --dry-run chromium` ───────────────────────

OLD_STYLE_OUTPUT = """browser: chromium version 113.0.5672.53
  Install location:    /home/user/.cache/ms-playwright/chromium-1055
  Download url:        https://cdn.example/builds/chromium/1055.zip

browser: chromium-headless-shell version 113.0.5672.53
  Install location:    /home/user/.cache/ms-playwright/chromium_headless_shell-1054
  Download url:        https://cdn.example/builds/chromium-headless-shell/1054.zip
"""

CURRENT_STYLE_OUTPUT = """Chrome for Testing 154.0.8037.0 (playwright chromium v1246)
  Install location:    /home/user/.cache/ms-playwright/chromium-1246
  Download url:        https://cdn.example/dbazure/download/playwright/builds/cft/154.0.8037.0/linux64/chrome-linux64.zip

Chrome Headless Shell 154.0.8037.0 (playwright chromium-headless-shell v1246)
  Install location:    /home/user/.cache/ms-playwright/chromium_headless_shell-1246
  Download url:        https://cdn.example/dbazure/download/playwright/builds/cft/154.0.8037.0/linux64/chrome-headless-shell-linux64.zip

FFmpeg (playwright ffmpeg v1011)
  Install location:    /home/user/.cache/ms-playwright/ffmpeg-1011
  Download url:        https://cdn.example/builds/ffmpeg/1011/ffmpeg-linux.zip
"""


@pytest.mark.parametrize("driver_output", [OLD_STYLE_OUTPUT, CURRENT_STYLE_OUTPUT])
def test_query_extracts_only_chromium_directories(driver_output):
    directories = _run_result(driver_output)
    assert [d.name for d in directories] == [
        "chromium-1055" if "chromium-1055" in driver_output else "chromium-1246",
        "chromium_headless_shell-1054" if "chromium_headless_shell-1054" in driver_output else "chromium_headless_shell-1246",
    ]
    # ffmpeg must never slip through, even when the driver lists it.
    assert all(d.name.startswith("chromium") for d in directories)


def test_query_deduplicates_repeated_locations():
    duplicated = """browser: chromium version 1.0
  Install location:    /cache/ms-playwright/chromium-1243
  Install location:    /cache/ms-playwright/chromium-1243
  Install location:    /cache/ms-playwright/chromium_headless_shell-1243
  Install location:    /cache/ms-playwright/chromium_headless_shell-1243
"""
    directories = _run_result(duplicated)
    # Each unique directory appears exactly once, in first-seen order.
    assert [d.name for d in directories] == [
        "chromium-1243",
        "chromium_headless_shell-1243",
    ]


def test_query_returns_empty_when_driver_errors():
    # Non-zero exit (driver failure).
    assert _run_result("some error", returncode=1) == []
    # Successful exit but nothing parseable.
    assert _run_result("nothing here") == []


def test_query_returns_empty_on_subprocess_exception(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("exec failed")

    monkeypatch.setattr(cpw.subprocess, "run", boom)
    assert cpw._query_expected_directories(python="python") == []


# ── Decision logic ─────────────────────────────────────────────────────────

def test_installed_when_expected_revisions_present(tmp_path, monkeypatch):
    for name in ("chromium-1246", "chromium_headless_shell-1246"):
        (tmp_path / name / cpw.COMPLETE_MARKER).parent.mkdir(parents=True)
        (tmp_path / name / cpw.COMPLETE_MARKER).touch()

    monkeypatch.setattr(
        cpw,
        "_query_expected_directories",
        lambda python=None: [tmp_path / "chromium-1246", tmp_path / "chromium_headless_shell-1246"],
    )
    assert cpw.chromium_installed(cache_root=tmp_path, python="python") is True


def test_missing_when_stale_revision_on_disk_but_expected_absent(tmp_path, monkeypatch):
    # The production failure: an OLD revision exists, the one the installed
    # Playwright expects does not — the version-blind scan would pass, the
    # precise driver check must not.
    (tmp_path / "chromium_headless_shell-1194" / cpw.COMPLETE_MARKER).parent.mkdir(parents=True)
    (tmp_path / "chromium_headless_shell-1194" / cpw.COMPLETE_MARKER).touch()

    monkeypatch.setattr(
        cpw,
        "_query_expected_directories",
        lambda python=None: [tmp_path / "chromium_headless_shell-1243", tmp_path / "chromium-1243"],
    )
    assert cpw.chromium_installed(cache_root=tmp_path, python="python") is False


def test_missing_when_one_expected_marker_absent(tmp_path, monkeypatch):
    (tmp_path / "chromium-1246" / cpw.COMPLETE_MARKER).parent.mkdir(parents=True)
    (tmp_path / "chromium-1246" / cpw.COMPLETE_MARKER).touch()
    # headless shell never fully downloaded.

    monkeypatch.setattr(
        cpw,
        "_query_expected_directories",
        lambda python=None: [tmp_path / "chromium-1246", tmp_path / "chromium_headless_shell-1246"],
    )
    assert cpw.chromium_installed(cache_root=tmp_path, python="python") is False


def test_falls_back_to_glob_when_driver_questioned_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(cpw, "_query_expected_directories", lambda python=None: [])
    (tmp_path / "chromium_headless_shell-1246" / "chrome-headless-shell-linux64").mkdir(parents=True)
    (tmp_path / "chromium_headless_shell-1246" / "chrome-headless-shell-linux64" / "chrome-headless-shell").touch()
    assert cpw.chromium_installed(cache_root=tmp_path, python="python") is True


# ── CLI behavior (main renders evaluate(), must not re-derive the result) ──

def _run_cli(monkeypatch, capsys, result):
    monkeypatch.setattr(cpw, "evaluate", lambda cache_root=None, python=None: result)
    monkeypatch.setattr(sys, "argv", ["check_playwright.py", "--verbose"])
    return cpw.main()


def test_main_precise_path_installed(monkeypatch, capsys, tmp_path):
    directories = [tmp_path / "chromium-1246", tmp_path / "chromium_headless_shell-1246"]
    result = cpw.CheckResult(
        installed=True,
        strategy="driver",
        expected_directories=directories,
        missing_directories=[],
        cache_root=tmp_path,
    )
    rc = _run_cli(monkeypatch, capsys, result)
    err = capsys.readouterr().err
    assert rc == 0
    assert all("present" in err for _ in directories)
    assert "expected revision(s) installed" in err
    assert "MISSING" not in err


def test_main_precise_path_missing_version(monkeypatch, capsys, tmp_path):
    expected = tmp_path / "chromium_headless_shell-1243"
    present = tmp_path / "chromium-1243"
    result = cpw.CheckResult(
        installed=False,
        strategy="driver",
        expected_directories=[present, expected],
        missing_directories=[expected],
        cache_root=tmp_path,
    )
    rc = _run_cli(monkeypatch, capsys, result)
    err = capsys.readouterr().err
    assert rc == 1
    assert f"{expected}  (MISSING)" in err
    assert "expected revision(s) not installed" in err


def test_main_fallback_path_via_glob(monkeypatch, capsys, tmp_path):
    result = cpw.CheckResult(
        installed=True,
        strategy="glob",
        expected_directories=[],
        missing_directories=[],
        cache_root=tmp_path,
    )
    rc = _run_cli(monkeypatch, capsys, result)
    err = capsys.readouterr().err
    assert rc == 0
    assert f"Cache root : {tmp_path}" in err
    assert "directory-scan fallback" in err
    assert "revision NOT verified" in err


def test_main_fallback_path_missing(monkeypatch, capsys, tmp_path):
    result = cpw.CheckResult(
        installed=False,
        strategy="glob",
        expected_directories=[],
        missing_directories=[],
        cache_root=tmp_path,
    )
    rc = _run_cli(monkeypatch, capsys, result)
    err = capsys.readouterr().err
    assert rc == 1
    assert f"Cache root : {tmp_path}" in err
    assert "Status     : Chromium MISSING" in err


# ── Cache root resolution (must mirror the driver, incl. PLAYWRIGHT_BROWSERS_PATH=0) ──

def _fake_playwright_package(tmp_path, monkeypatch):
    """Pretend ``playwright`` is installed on the path (mirrors a real wheel layout)."""
    from types import ModuleType

    pkg_init = tmp_path / "site" / "playwright" / "__init__.py"
    pkg_init.parent.mkdir(parents=True)
    mod = ModuleType("playwright")
    mod.__file__ = str(pkg_init)
    monkeypatch.setitem(sys.modules, "playwright", mod)
    return pkg_init.parent


def test_resolve_zero_mode_points_to_package_local_browsers(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")
    monkeypatch.delenv("INIT_CWD", raising=False)
    pkg_dir = _fake_playwright_package(tmp_path, monkeypatch)

    root = cpw._resolve_cache_root()
    assert root == pkg_dir / "driver" / "package" / ".local-browsers"
    # Never the literal string "0" as a path.
    assert root != tmp_path / "0"


def test_resolve_zero_mode_unresolvable_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")
    monkeypatch.delenv("INIT_CWD", raising=False)
    monkeypatch.delitem(sys.modules, "playwright", raising=False)

    assert cpw._resolve_cache_root() is None


def test_resolve_relative_value_absolute_against_init_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "browsers")
    monkeypatch.setenv("INIT_CWD", str(tmp_path))
    assert cpw._resolve_cache_root() == tmp_path / "browsers"


def test_resolve_default_matches_driver_platform_logic(tmp_path, monkeypatch):
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.delenv("INIT_CWD", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    def resolve(os_name=None, platform_name=None):
        return cpw._default_registry_directory(os_name=os_name, platform_name=platform_name)

    # Linux: XDG_CACHE_HOME wins.
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert resolve("posix", "linux") == tmp_path / "xdg" / "ms-playwright"
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    # Linux without XDG_CACHE_HOME: falls back to ~/.cache.
    assert resolve("posix", "linux") == pathlib.Path.home() / ".cache" / "ms-playwright"
    # macOS: fixed ~/Library/Caches/ms-playwright (XDG_CACHE_HOME ignored).
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert resolve("posix", "darwin") == pathlib.Path.home() / "Library" / "Caches" / "ms-playwright"
    # Windows: LOCALAPPDATA wins.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    assert resolve("nt", "win32") == tmp_path / "appdata" / "ms-playwright"


def test_evaluate_zero_mode_fallback_scans_package_local_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")
    monkeypatch.delenv("INIT_CWD", raising=False)
    pkg_dir = _fake_playwright_package(tmp_path, monkeypatch)
    monkeypatch.setattr(cpw, "_query_expected_directories", lambda python=None: [])

    # A browser installed in Playwright's package-local dir must be found.
    local_root = pkg_dir / "driver" / "package" / ".local-browsers"
    binary = local_root / "chromium_headless_shell-1246" / "chrome-headless-shell-linux64" / "chrome-headless-shell"
    binary.parent.mkdir(parents=True)
    binary.touch()

    result = cpw.evaluate(cache_root=None, python="python")
    assert result.installed is True
    assert result.strategy == "glob"
    assert result.cache_root == local_root


def test_evaluate_zero_mode_unresolvable_disables_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")
    monkeypatch.delenv("INIT_CWD", raising=False)
    monkeypatch.delitem(sys.modules, "playwright", raising=False)
    monkeypatch.setattr(cpw, "_query_expected_directories", lambda python=None: [])

    def _must_not_run(cache_root):
        raise AssertionError("glob fallback must be disabled when the cache root is unresolvable")

    monkeypatch.setattr(cpw, "_check_via_glob", _must_not_run)

    result = cpw.evaluate(cache_root=None, python="python")
    assert result.installed is False
    assert result.strategy == "unresolved"
    assert result.cache_root is None