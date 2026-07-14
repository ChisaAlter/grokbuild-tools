import subprocess
import sys
from pathlib import Path

import pytest

from grok_account_manager.browser_util import (
    _build_auth_url_gate_script,
    is_auth_login_url,
    suppress_default_browser_opens,
)


def test_is_auth_login_url():
    assert is_auth_login_url("https://accounts.x.ai/oauth2/device?user_code=ABCD-EFGH")
    assert is_auth_login_url("https://auth.x.ai/sign-in")
    assert not is_auth_login_url("https://example.com/")
    assert not is_auth_login_url("")


def test_gate_script_swallows_auth_urls(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Point tempfile dir at tmp so we can inspect the script if needed
    gate = _build_auth_url_gate_script(
        r'"C:\FakeBrowser\browser.exe" --single-argument %1'
    )
    assert gate.exists()

    # Auth URL → exit 0, and must NOT launch anything (we just check exit code)
    auth = "https://accounts.x.ai/oauth2/device?user_code=TEST-CODE"
    r = subprocess.run(
        ["cmd", "/c", str(gate), auth],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert r.returncode == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry gate")
def test_suppress_default_browser_opens_restores_registry():
    """Install/restore should not leave the default browser broken."""
    import winreg

    from grok_account_manager.browser_util import _win_read_open_command, _win_user_choice_progids

    progids = _win_user_choice_progids()
    if not progids:
        pytest.skip("No UserChoice ProgId")

    before = {p: _win_read_open_command(p) for p in progids}
    with suppress_default_browser_opens():
        during = {p: _win_read_open_command(p) for p in progids}
        for p in progids:
            cmd = during[p] or ""
            assert "grok_am_default_browser_gate.cmd" in cmd.replace("/", "\\") or "grok_am_default_browser_gate" in cmd
    after = {p: _win_read_open_command(p) for p in progids}
    assert after == before
