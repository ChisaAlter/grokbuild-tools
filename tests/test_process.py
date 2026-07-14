from pathlib import Path
from unittest.mock import MagicMock, patch

from grok_account_manager.process import find_grok_executable, restart_grok


def test_find_grok_exe(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "grok.exe"
    exe.write_text("x", encoding="utf-8")
    found = find_grok_executable(tmp_path)
    assert found == exe


@patch("grok_account_manager.process.start_grok")
@patch("grok_account_manager.process.kill_grok_processes")
def test_restart_reports(mock_kill, mock_start):
    mock_kill.return_value = [1, 2]
    mock_start.return_value = (True, "C:/g/grok.exe", "Started")
    r = restart_grok(timeout_secs=0.1)
    assert r.ok
    assert r.started
    assert r.killed_pids == [1, 2]
