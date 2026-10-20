"""处理工程里的 ``src/brain/config/config.yaml`` 中 rerunLog 配置。

打包文件夹时，如果检测到::

    rerunLog:
      enable_tcp: true
      server_ip: 127.0.0.1

则把 ``server_ip`` 改为本机（运行 BRM 的电脑）的局域网 IP，**只修改 zip 内的
副本**，不修改用户本地源文件；如果 ``enable_tcp`` 为 false 则保持原样。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 递归查找时跳过的目录
_EXCLUDED_DIRS = {
    ".git",
    ".svn",
    ".hg",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "build",
    "install",
    "log",
    "logs",
    ".mypy_cache",
    ".pytest_cache",
}

_RERUN_LOG_RE = re.compile(r"^([ \t]*)rerunLog\s*:\s*(?:#.*)?$")
_ENABLE_RE = re.compile(
    r"^([ \t]*)enable_tcp\s*:\s*([^#\r\n]*?)(\s*#.*)?(\r?\n)?$"
)
_SERVER_RE = re.compile(
    r"^([ \t]*)server_ip\s*:\s*([^#\r\n]*?)(\s*#.*)?(\r?\n)?$"
)
_TRUE_VALUES = {"true", "yes", "on", "1"}


@dataclass
class RerunConfigUpdate:
    """一次 rerunLog 配置检查结果。"""

    path: Optional[Path] = None
    enabled: bool = False
    changed: bool = False
    old_ip: str = ""
    new_ip: str = ""
    message: str = ""


def _indent_width(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _clean_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value


def find_rerun_config(source_dir: Path) -> Optional[Path]:
    """在工程文件夹中定位 rerun 配置文件。"""
    source_dir = Path(source_dir)
    direct_candidates = [
        source_dir / "src" / "brain" / "config" / "config.yaml",
        source_dir / "brain" / "config" / "config.yaml",
        source_dir / "config" / "config.yaml",
    ]
    for candidate in direct_candidates:
        if candidate.is_file():
            return candidate

    matches: List[Path] = []
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [name for name in dirs if name not in _EXCLUDED_DIRS]
        if "config.yaml" not in files:
            continue
        candidate = Path(root) / "config.yaml"
        posix = candidate.as_posix()
        if posix.endswith("/src/brain/config/config.yaml"):
            matches.append(candidate)
        elif posix.endswith("/brain/config/config.yaml"):
            matches.append(candidate)
    if not matches:
        return None
    matches.sort(key=lambda path: (len(path.parts), str(path)))
    return matches[0]


def _find_blocks(lines: List[str]) -> List[Tuple[int, int, int]]:
    """返回 [(rerunLog 行索引, block 结束索引, 缩进)]。"""
    blocks: List[Tuple[int, int, int]] = []
    for index, line in enumerate(lines):
        match = _RERUN_LOG_RE.match(line.rstrip("\r\n"))
        if not match:
            continue
        base = _indent_width(match.group(1))
        end = index + 1
        while end < len(lines):
            raw = lines[end]
            if not raw.strip() or raw.lstrip().startswith("#"):
                end += 1
                continue
            if _indent_width(raw) <= base:
                break
            end += 1
        blocks.append((index, end, base))
    return blocks


def update_server_ip(
    text: str, new_ip: str
) -> Tuple[str, bool, bool, List[str]]:
    """把 rerunLog.enable_tcp=true 的 server_ip 改为 ``new_ip``。

    返回 (新文本, 是否发现启用项, 是否修改, 旧 IP 列表)。
    """
    if not new_ip:
        return text, False, False, []
    lines = text.splitlines(keepends=True)
    blocks = _find_blocks(lines)
    enabled_any = False
    changed_any = False
    old_ips: List[str] = []

    # 倒序处理，避免插入 server_ip 行时影响前面的 block 索引
    for block_start, block_end, base in reversed(blocks):
        enable_index: Optional[int] = None
        enable_indent = " " * (base + 2)
        enabled = False
        server_index: Optional[int] = None

        for index in range(block_start + 1, block_end):
            raw = lines[index]
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            match = _ENABLE_RE.match(raw)
            if match:
                enable_index = index
                enable_indent = match.group(1)
                enabled = _clean_value(match.group(2)).lower() in _TRUE_VALUES
                continue
            match = _SERVER_RE.match(raw)
            if match:
                server_index = index

        if not enabled:
            continue
        enabled_any = True

        if server_index is None:
            newline = "\r\n" if lines[block_start].endswith("\r\n") else "\n"
            insertion = f"{enable_indent}server_ip: {new_ip}{newline}"
            insert_at = (enable_index if enable_index is not None else block_start) + 1
            lines.insert(insert_at, insertion)
            changed_any = True
            continue

        raw = lines[server_index]
        match = _SERVER_RE.match(raw)
        if match is None:
            continue
        old_value = _clean_value(match.group(2))
        old_ips.append(old_value)
        if old_value == new_ip:
            continue
        prefix = match.group(1) + "server_ip: "
        comment = match.group(3) or ""
        newline = match.group(4) or ""
        quote = ""
        stripped = match.group(2).strip()
        if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in "\"'":
            quote = stripped[0]
        lines[server_index] = f"{prefix}{quote}{new_ip}{quote}{comment}{newline}"
        changed_any = True

    return "".join(lines), enabled_any, changed_any, old_ips


def prepare_zip_overrides(
    source_dir: Path, local_ip: str
) -> Tuple[Dict[Path, bytes], RerunConfigUpdate]:
    """生成 zip 内需要替换的文件内容（目前只处理 rerun 配置）。"""
    config_path = find_rerun_config(source_dir)
    if config_path is None:
        return {}, RerunConfigUpdate()

    try:
        original = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return {}, RerunConfigUpdate(
            path=config_path,
            message=f"读取 rerun 配置文件失败：{exc}",
        )

    if not local_ip:
        return {}, RerunConfigUpdate(
            path=config_path,
            message="检测到 rerun 配置文件，但无法获取本机局域网 IP，未修改 server_ip。",
        )

    new_text, enabled, changed, old_ips = update_server_ip(original, local_ip)
    update = RerunConfigUpdate(
        path=config_path,
        enabled=enabled,
        changed=changed,
        old_ip=old_ips[0] if old_ips else "",
        new_ip=local_ip,
    )

    if not enabled:
        update.message = (
            f"检测到 {config_path.name}，但 rerunLog.enable_tcp 未开启，"
            "未修改 server_ip。"
        )
        return {}, update

    if not changed:
        update.message = (
            f"rerunLog.enable_tcp=true，server_ip 已是本机 IP（{local_ip}），无需修改。"
        )
        return {}, update

    update.message = (
        f"rerunLog.enable_tcp=true，打包时将 server_ip 从 "
        f"{update.old_ip or '空'} 改为本机 IP {local_ip}（仅修改 zip 内文件）。"
    )
    return {config_path: new_text.encode("utf-8")}, update
