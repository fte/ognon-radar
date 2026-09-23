"""
Unit tests for scripts/check_playwright.py.

These cover the revision-precise browser check used by scripts/deploy_live.sh:
parsing of `playwright install --dry-run` output, and the decision logic that
must catch a Playwright upgrade whose expected browser revision is missing
from disk (the "Executable doesn't exist" production failure).
"""
import pathlib
import subprocess
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