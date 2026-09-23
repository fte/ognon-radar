"""
Unit tests for scripts/check_playwright.py.

These cover the revision-precise browser check used by scripts/deploy_live.sh:
parsing of `playwright install --dry-run` output, and the decision logic that
must catch a Playwright upgrade whose expected browser revision is missing
from disk (the "Executable doesn't exist" production failure).
"""
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