import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from agent.repo import clone_repo, find_mpr


def test_find_mpr_finds_file(tmp_path):
    mpr = tmp_path / "MyApp.mpr"
    mpr.write_bytes(b"")
    result = find_mpr(str(tmp_path))
    assert result == str(mpr)


def test_find_mpr_raises_when_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="No .mpr"):
        find_mpr(str(tmp_path))


def test_clone_repo_calls_git_with_pat():
    captured = []

    def fake_run(args, **kwargs):
        captured.append(args)
        r = MagicMock()
        r.stdout = b""
        return r

    with patch("agent.repo.subprocess.run", side_effect=fake_run):
        clone_repo(
            app_id="abc-123",
            target="/tmp/fake",
            git_base_url="https://git.api.mendix.com",
            mx_pat="mytoken",
        )

    assert len(captured) == 1
    args = captured[0]
    assert "git" in args
    assert "clone" in args
    assert "--depth" in args
    assert any("pat:mytoken" in str(a) for a in args)
    assert "https://git.api.mendix.com/abc-123.git" in args


def test_clone_repo_raises_on_git_failure():
    with patch(
        "agent.repo.subprocess.run",
        side_effect=subprocess.CalledProcessError(128, "git", stderr=b"fatal: not found"),
    ):
        with pytest.raises(subprocess.CalledProcessError):
            clone_repo("abc", "/tmp/fake", "https://git.api.mendix.com", "pat")
