"""Shell 转义、危险路径拦截、格式化与密码打码的单元测试。"""

from __future__ import annotations

import stat

import pytest

from brm.core.errors import UnsafePathError
from brm.core.util import (
    assert_safe_delete_path,
    human_size,
    mask_secrets,
    mode_text,
    normalize_remote_dir,
    posix_join,
    quote,
)

WORKSPACE = "/home/booster/Workspace/"


def test_quote_handles_spaces_and_specials() -> None:
    assert quote("plain") == "plain"
    assert quote("a b") == "'a b'"
    assert quote("it's") == "'it'\"'\"'s'"


def test_posix_join() -> None:
    assert posix_join("/home/booster/Workspace/", "demo.zip") == "/home/booster/Workspace/demo.zip"
    assert posix_join("/", "a", "b") == "/a/b"


def test_normalize_remote_dir() -> None:
    assert normalize_remote_dir("/home/booster") == "/home/booster/"
    assert normalize_remote_dir("/home/booster/Workspace/") == "/home/booster/Workspace/"
    with pytest.raises(UnsafePathError):
        normalize_remote_dir("relative/path")
    with pytest.raises(UnsafePathError):
        normalize_remote_dir("/home/booster/../etc/")


def test_safe_delete_path_allows_workspace_children() -> None:
    assert (
        assert_safe_delete_path("/home/booster/Workspace/demo.zip", WORKSPACE)
        == "/home/booster/Workspace/demo.zip"
    )
    assert (
        assert_safe_delete_path("/home/booster/Workspace/demo", WORKSPACE)
        == "/home/booster/Workspace/demo"
    )


@pytest.mark.parametrize(
    "target",
    [
        "",
        "relative/path",
        "/home/booster/",
        "/home/booster/Workspace",
        "/home/booster/Workspace/../etc/passwd",
        "/etc/passwd",
        "/home/booster/WorkspaceX/demo",
    ],
)
def test_safe_delete_path_rejects_dangerous_targets(target: str) -> None:
    with pytest.raises(UnsafePathError):
        assert_safe_delete_path(target, WORKSPACE)


def test_human_size() -> None:
    assert human_size(0) == "0 B"
    assert human_size(1024) == "1.0 KB"
    assert human_size(1536) == "1.5 KB"
    assert human_size(None) == "-"


def test_mode_text_and_format() -> None:
    mode = stat.S_IFDIR | 0o755
    assert mode_text(mode) == "drwxr-xr-x"
    assert mode_text(None) == "-"


def test_mask_secrets() -> None:
    text = "password=hunter2 并使用 hunter2 登录"
    masked = mask_secrets(text, ["hunter2"])
    assert "hunter2" not in masked
    assert masked.count("******") >= 2
    assert "password=******" in mask_secrets("password=abc", ["abc"])
