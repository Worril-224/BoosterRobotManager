"""通用工具：Shell 转义、危险路径校验、大小 / 权限格式化、密码打码。"""

from __future__ import annotations

import re
import shlex
import time
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional

from .errors import UnsafePathError


# ---------------------------------------------------------------------------
# Shell / 路径
# ---------------------------------------------------------------------------
def quote(value: str) -> str:
    """对拼进远端 shell 命令的参数做转义，避免空格与特殊字符出错。"""
    return shlex.quote(str(value))


def posix_join(*parts: str) -> str:
    """拼接远端 POSIX 路径并规范化（不会引入本地平台差异）。"""
    cleaned = [str(p).strip("/") for p in parts if str(p).strip("/")]
    if not cleaned:
        return "/"
    return "/" + "/".join(cleaned)


def normalize_remote_dir(path: str) -> str:
    """规范化远端目录：必须是绝对路径，以 ``/`` 结尾。"""
    text = (path or "").strip()
    if not text:
        raise UnsafePathError("远端目录不能为空。")
    if not text.startswith("/"):
        raise UnsafePathError(f"远端目录必须是绝对路径：{path}")
    normalized = text.rstrip("/") + "/"
    if "/../" in normalized or normalized.endswith("/.."):
        raise UnsafePathError(f"远端目录不能包含 ..：{path}")
    return normalized


def assert_safe_delete_path(path: str, workspace_dir: str) -> str:
    """删除前校验：目标必须位于配置的上传目录（默认 /home/booster/Workspace/）内。

    这是防止误删机器人系统文件的最后一道防线。返回规范化后的路径。
    """
    workspace = normalize_remote_dir(workspace_dir)
    raw = (path or "").strip()
    if not raw:
        raise UnsafePathError("拒绝删除空路径。")
    if not raw.startswith("/"):
        raise UnsafePathError(f"拒绝删除相对路径：{raw}")

    pure = PurePosixPath(raw)
    parts = pure.parts
    if any(part == ".." for part in parts):
        raise UnsafePathError(f"路径包含非法片段 ..，已拦截：{raw}")

    normalized = str(pure)
    if not normalized.startswith(workspace):
        raise UnsafePathError(
            f"拒绝删除上传目录之外的路径：{normalized}",
            hint=f"只允许删除 {workspace} 下的内容。",
        )
    if normalized.rstrip("/") == workspace.rstrip("/"):
        raise UnsafePathError(f"拒绝删除上传目录本身：{normalized}")
    return normalized


def strip_trailing_slash(path: str) -> str:
    return path.rstrip("/") or "/"


def basename(path: str) -> str:
    return PurePosixPath(path.rstrip("/")).name


def join_project_name(project_dir: str) -> str:
    """由项目目录得到项目名（用于 pid / 日志文件名）。"""
    return basename(project_dir) or "project"


def sanitize_filename(name: str) -> str:
    """把任意字符串转换为安全的本地文件名片段。"""
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", str(name)).strip("_")
    return cleaned or "robot"


def timestamp_slug() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def timestamp_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def clock_text() -> str:
    return time.strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# 展示格式化
# ---------------------------------------------------------------------------
def human_size(num: Optional[int]) -> str:
    if num is None:
        return "-"
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def format_mtime(value: Optional[float]) -> str:
    if not value:
        return "-"
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(value))
    except (ValueError, OSError):
        return "-"


def mode_text(mode: Optional[int]) -> str:
    """把 st_mode 转成 ``drwxr-xr-x`` 形式。"""
    if mode is None:
        return "-"
    import stat as stat_module

    try:
        return stat_module.filemode(mode)
    except (ValueError, TypeError):
        return "-"


# ---------------------------------------------------------------------------
# 密码打码
# ---------------------------------------------------------------------------
def mask_secrets(text: str, secrets: Iterable[str]) -> str:
    """把文本中出现的所有密码字面量替换为 ``******``（日志安全兜底）。"""
    result = text or ""
    for secret in secrets:
        if not secret:
            continue
        result = result.replace(secret, "******")
    # 兜底：命令行里形如 --password=xxx / password: xxx 的内容也打码
    result = re.sub(
        r"(?i)(password|passwd|pwd)(\s*[:=]\s*)(\S+)",
        lambda m: f"{m.group(1)}{m.group(2)}******",
        result,
    )
    return result


def local_file_ok(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False
