"""SFTP 封装层测试（基于本地 FakeRemote，覆盖 R2 / R3 的数据层）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from brm.core.errors import BrmError
from brm.core.sftp_client import SFTPClient
from tests.fake_remote import FakeRemote


@pytest.fixture()
def sftp(remote: FakeRemote) -> SFTPClient:
    return SFTPClient(remote)


def test_listdir_dirs_first_and_attributes(remote: FakeRemote, sftp: SFTPClient) -> None:
    remote.write_text("/home/booster/z.txt", "z")
    remote.write_text("/home/booster/a.txt", "a")
    entries = sftp.listdir("/home/booster")
    names = [entry.name for entry in entries]
    assert names[0] == "Workspace"  # 目录排在最前
    assert names[1:] == ["a.txt", "z.txt"]

    workspace = entries[0]
    assert workspace.is_dir
    assert workspace.mode_text.startswith("d")
    assert workspace.size_text == "-"

    a_txt = entries[1]
    assert not a_txt.is_dir
    assert a_txt.mode_text.startswith("-")
    assert a_txt.size_text == "1 B"
    assert a_txt.mtime_text != "-"


def test_stat_and_exists(remote: FakeRemote, sftp: SFTPClient) -> None:
    assert sftp.stat("/home/booster/Workspace") is not None
    assert sftp.stat("/home/booster/not-exist") is None
    assert not sftp.exists("/home/booster/not-exist")


def test_read_text_and_truncation(remote: FakeRemote, sftp: SFTPClient) -> None:
    content = "0123456789" * 100
    remote.write_text("/home/booster/big.txt", content)
    text, truncated = sftp.read_text("/home/booster/big.txt", 100)
    assert text == content[:100]
    assert truncated is True

    full, not_truncated = sftp.read_text("/home/booster/big.txt", 2000)
    assert full == content
    assert not_truncated is False


def test_read_binary_rejected(remote: FakeRemote, sftp: SFTPClient) -> None:
    remote.local_path("/home/booster/blob.bin").write_bytes(b"abc\x00def")
    with pytest.raises(BrmError) as excinfo:
        sftp.read_text("/home/booster/blob.bin", 1000)
    assert "二进制" in excinfo.value.message


def test_download_file(remote: FakeRemote, sftp: SFTPClient, tmp_path: Path) -> None:
    remote.write_text("/home/booster/data.txt", "download me")
    target = tmp_path / "data.txt"
    size = sftp.download("/home/booster/data.txt", str(target))
    assert target.read_text(encoding="utf-8") == "download me"
    assert size == len("download me")


def test_upload_file_with_progress(remote: FakeRemote, sftp: SFTPClient, tmp_path: Path) -> None:
    source = tmp_path / "upload.bin"
    payload = bytes(range(256)) * 400  # 100 KB
    source.write_bytes(payload)
    progress = []
    size = sftp.upload(str(source), "/home/booster/Workspace/upload.bin", lambda done, total: progress.append((done, total)))
    assert size == len(payload)
    assert remote.local_path("/home/booster/Workspace/upload.bin").read_bytes() == payload
    assert progress
    assert progress[-1][0] == len(payload)


def test_mkdir_p_and_remove_tree(remote: FakeRemote, sftp: SFTPClient) -> None:
    sftp.mkdir_p("/home/booster/Workspace/a/b/c")
    assert remote.exists("/home/booster/Workspace/a/b/c")
    remote.write_text("/home/booster/Workspace/a/b/c/file.txt", "x")
    sftp.remove_tree("/home/booster/Workspace/a")
    assert not remote.exists("/home/booster/Workspace/a")


def test_remove_file(remote: FakeRemote, sftp: SFTPClient) -> None:
    remote.write_text("/home/booster/Workspace/gone.txt", "x")
    sftp.remove_file("/home/booster/Workspace/gone.txt")
    assert not remote.exists("/home/booster/Workspace/gone.txt")
