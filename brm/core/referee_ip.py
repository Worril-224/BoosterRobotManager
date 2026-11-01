"""上传前修改机器人接收的裁判机（GameController）IP。

处理两处常见配置：
1. ``src/brain/config/config.yaml`` 中的 ``game_control_ip``（以及同类键）；
2. ``src/game_controller/launch/launch.py`` 中的 ``ip_white_list`` /
   ``game_control_ip`` 的 IP 字面量。

只修改上传 zip 内的副本，不改本地源文件。
"""

from __future__ import annotations

import ipaddress
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

_YAML_IP_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<key>"
    r"(?:game[_-]?control(?:ler)?|referee|gc)[_-]?ip"
    r")\s*:\s*(?P<value>[^#\r\n]*?)(?P<comment>\s*#.*)?(?P<newline>\r?\n)?$",
    re.IGNORECASE,
)
_YAML_WHITELIST_RE = re.compile(
    r"^(?P<indent>[ \t]*)ip_white_list\s*:\s*(?P<value>.*?)"
    r"(?P<newline>\r?\n)?$",
    re.IGNORECASE,
)
_PY_KEY_RE = re.compile(
    r"^(?P<indent>[ \t]*(?:[\"']?)(?:ip_white_list|game_control_ip)(?:[\"']?)"
    r"\s*[:=]\s*)(?P<value>.*)$",
    re.IGNORECASE,
)


@dataclass
class RefereeIpUpdate:
    text: str
    found: bool = False
    changed: bool = False
    labels: List[str] = field(default_factory=list)


@dataclass
class RefereeIpRewriteResult:
    path: Path
    found: bool = False
    changed: bool = False
    changed_files: List[str] = field(default_factory=list)
    message: str = ""


def _valid_ip(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    try:
        return str(ipaddress.IPv4Address(value))
    except ValueError:
        return ""


def _replace_ip_literals(value: str, new_ip: str) -> Tuple[str, int]:
    count = 0

    def repl(match: re.Match) -> str:
        nonlocal count
        count += 1
        return new_ip

    return IPV4_RE.sub(repl, value), count


def apply_referee_text(text: str, new_ip: str, is_python: bool = False) -> RefereeIpUpdate:
    """替换文本中的裁判机 IP 字面量。"""
    lines = text.splitlines(keepends=True)
    found = False
    changed = False
    labels: List[str] = []

    for index, line in enumerate(lines):
        raw = line.rstrip("\r\n")
        newline = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")

        if is_python:
            lowered = raw.lower()
            if "ip_white_list" not in lowered and "game_control_ip" not in lowered:
                continue
            match = _PY_KEY_RE.match(raw)
            if not match:
                # 兼容 DeclareLaunchArgument('ip_white_list', default_value='...') 等写法
                new_raw, count = _replace_ip_literals(raw, new_ip)
                if count:
                    found = True
                    labels.append("ip_white_list")
                    if new_raw != raw:
                        lines[index] = new_raw + newline
                        changed = True
                continue
            value = match.group("value")
            new_value, count = _replace_ip_literals(value, new_ip)
            if count:
                found = True
                labels.append("ip_white_list")
                if new_value != value:
                    lines[index] = match.group("indent") + new_value + newline
                    changed = True
                continue

            # 多行列表：ip_white_list = [\n  "192.168.1.102",\n]
            opens = value.count("[") + value.count("(") + value.count("{")
            closes = value.count("]") + value.count(")") + value.count("}")
            depth = opens - closes
            if depth <= 0:
                continue
            found = True
            labels.append("ip_white_list")
            cursor = index + 1
            while cursor < len(lines) and depth > 0:
                raw_next = lines[cursor]
                replaced, count = _replace_ip_literals(raw_next, new_ip)
                if count:
                    lines[cursor] = replaced
                    changed = True
                depth += (
                    raw_next.count("[")
                    + raw_next.count("(")
                    + raw_next.count("{")
                    - raw_next.count("]")
                    - raw_next.count(")")
                    - raw_next.count("}")
                )
                cursor += 1
            continue

        match = _YAML_IP_RE.match(raw)
        if match:
            found = True
            labels.append(match.group("key"))
            old = _valid_ip(match.group("value"))
            if old != new_ip:
                value = match.group("value").strip()
                quote = value[0] if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'" else ""
                prefix = match.group("indent") + match.group("key") + ": "
                lines[index] = (
                    prefix
                    + quote
                    + new_ip
                    + quote
                    + (match.group("comment") or "")
                    + newline
                )
                changed = True
            continue

        match = _YAML_WHITELIST_RE.match(raw)
        if match:
            value = match.group("value")
            new_value, count = _replace_ip_literals(value, new_ip)
            if count:
                found = True
                labels.append("ip_white_list")
                if new_value != value:
                    lines[index] = (
                        match.group("indent") + "ip_white_list: " + new_value + newline
                    )
                    changed = True

    return RefereeIpUpdate("".join(lines), found, changed, labels)


def _is_yaml(name: str) -> bool:
    return name.lower().endswith((".yaml", ".yml"))


def _is_python(name: str) -> bool:
    return name.lower().endswith(".py")


def rewrite_zip_referee_ip(
    zip_path: Path,
    dest_dir: Path,
    new_ip: str,
) -> RefereeIpRewriteResult:
    """改写 zip 内裁判机 IP；只生成新的临时 zip。"""
    zip_path = Path(zip_path)
    dest_dir = Path(dest_dir)
    target_ip = _valid_ip(new_ip)
    if not target_ip:
        raise ValueError(f"不是合法的 IPv4 地址：{new_ip}")

    overrides: Dict[str, bytes] = {}
    changed_files: List[str] = []
    found_any = False
    labels: List[str] = []

    try:
        source = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"无法读取 zip：{exc}") from exc

    with source:
        for info in source.infolist():
            if info.is_dir():
                continue
            name = info.filename
            is_python = _is_python(name)
            if not (_is_yaml(name) or is_python):
                continue
            if info.file_size > 4 * 1024 * 1024:
                continue
            try:
                text = source.read(info).decode("utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if is_python and "ip_white_list" not in text and "game_control_ip" not in text:
                continue
            update = apply_referee_text(text, target_ip, is_python=is_python)
            if not update.found:
                continue
            found_any = True
            labels.extend(update.labels)
            if update.changed:
                overrides[name] = update.text.encode("utf-8")
                changed_files.append(name)
            else:
                changed_files.append(name)

    if not found_any:
        return RefereeIpRewriteResult(
            path=zip_path,
            found=False,
            message=(
                "zip 内没有找到 game_control_ip / ip_white_list，"
                "无法修改裁判机 IP。"
            ),
        )

    if not overrides:
        return RefereeIpRewriteResult(
            path=zip_path,
            found=True,
            changed=False,
            changed_files=changed_files,
            message=f"裁判机 IP 已经是 {target_ip}，无需修改。",
        )

    work_dir = dest_dir / "referee"
    work_dir.mkdir(parents=True, exist_ok=True)
    target = work_dir / zip_path.name
    if target.exists():
        target.unlink()
    with zipfile.ZipFile(zip_path) as source, zipfile.ZipFile(
        target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as output:
        for info in source.infolist():
            data = overrides.get(info.filename)
            if data is None:
                data = source.read(info)
            output.writestr(info, data)

    return RefereeIpRewriteResult(
        path=target,
        found=True,
        changed=True,
        changed_files=changed_files,
        message=(
            f"上传前已把裁判机 IP 改为 {target_ip}"
            f"（{', '.join(changed_files)}）。"
        ),
    )
