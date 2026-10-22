"""上传前修改压缩包内 config.yaml 的 team_id / player_id。

功能开关由部署面板控制；无论目标是“文件夹自动打包”还是用户直接选择的 zip，
都会在上传前对 zip 内的配置文件做一次替换。**只改 zip 内的副本，不改本地源文件。**
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

_KEY_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<key>team[_-]?id|player[_-]?id)"
    r"\s*:\s*(?P<value>[^#\r\n]*?)(?P<comment>\s*#.*)?(?P<newline>\r?\n)?$",
    re.IGNORECASE,
)
_GAME_RE = re.compile(r"^(?P<indent>[ \t]*)game\s*:\s*(?:#.*)?$", re.IGNORECASE)
_CONFIG_SUFFIXES = (".yaml", ".yml")


@dataclass
class IdentityTextUpdate:
    text: str
    found_keys: List[str] = field(default_factory=list)
    changed: bool = False


@dataclass
class IdentityRewriteResult:
    path: Path
    found: bool = False
    changed: bool = False
    changed_files: List[str] = field(default_factory=list)
    message: str = ""


def _canonical_key(raw: str) -> str:
    key = raw.lower().replace("-", "_")
    return "team_id" if key.startswith("team") else "player_id"


def _replace_value(raw_line: str, match: re.Match, new_value: int) -> str:
    indent = match.group("indent")
    key = match.group("key")
    comment = match.group("comment") or ""
    newline = match.group("newline") or ""
    old_value = match.group("value").strip()
    quote = ""
    if len(old_value) >= 2 and old_value[0] == old_value[-1] and old_value[0] in "\"'":
        quote = old_value[0]
    return f"{indent}{key}: {quote}{new_value}{quote}{comment}{newline}"


def apply_identity_text(
    text: str, team_id: int, player_id: int
) -> IdentityTextUpdate:
    """替换文本中的 team_id / player_id；缺失时在 game: 块下补一行。"""
    lines = text.splitlines(keepends=True)
    found: List[str] = []
    changed = False

    for index, line in enumerate(lines):
        match = _KEY_RE.match(line)
        if not match:
            continue
        key = _canonical_key(match.group("key"))
        if key not in found:
            found.append(key)
        target = team_id if key == "team_id" else player_id
        old_value = match.group("value").strip().strip("\"'")
        if old_value != str(target):
            lines[index] = _replace_value(line, match, target)
            changed = True

    missing = [key for key in ("team_id", "player_id") if key not in found]
    if missing:
        for index, line in enumerate(lines):
            game_match = _GAME_RE.match(line.rstrip("\r\n"))
            if not game_match:
                continue
            base_indent = game_match.group("indent")
            insert_indent = base_indent + "  "
            newline = "\r\n" if line.endswith("\r\n") else "\n"
            inserts = []
            for key in missing:
                value = team_id if key == "team_id" else player_id
                inserts.append(f"{insert_indent}{key}: {value}{newline}")
            for offset, insert_line in enumerate(inserts, start=1):
                lines.insert(index + offset, insert_line)
            found.extend(missing)
            changed = True
            break

    return IdentityTextUpdate("".join(lines), found, changed)


def _is_config_entry(name: str) -> bool:
    return name.lower().endswith(_CONFIG_SUFFIXES)


def rewrite_zip_identity(
    zip_path: Path,
    dest_dir: Path,
    team_id: int,
    player_id: int,
) -> IdentityRewriteResult:
    """改写 zip 内所有 config.yaml/yml 的 team_id/player_id。

    * 找到并修改 → 在新 zip 中返回 ``changed=True``；
    * 找到但已经是目标值 → ``found=True, changed=False``，可继续使用原 zip；
    * 没找到 → ``found=False``，调用方应提示用户。
    """
    zip_path = Path(zip_path)
    dest_dir = Path(dest_dir)
    with zipfile.ZipFile(zip_path) as source:
        overrides: Dict[str, bytes] = {}
        changed_files: List[str] = []
        found_any = False
        for info in source.infolist():
            if info.is_dir() or not _is_config_entry(info.filename):
                continue
            if info.file_size > 4 * 1024 * 1024:
                continue
            try:
                text = source.read(info).decode("utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            update = apply_identity_text(text, int(team_id), int(player_id))
            if not update.found_keys:
                continue
            found_any = True
            if update.changed:
                overrides[info.filename] = update.text.encode("utf-8")
                changed_files.append(info.filename)
            else:
                changed_files.append(info.filename)

    if not found_any:
        return IdentityRewriteResult(
            path=zip_path,
            found=False,
            changed=False,
            message=(
                "压缩包内没有找到 team_id/player_id（检查 src/brain/config/config.yaml），"
                "未上传。"
            ),
        )

    if not overrides:
        return IdentityRewriteResult(
            path=zip_path,
            found=True,
            changed=False,
            changed_files=changed_files,
            message=(
                f"配置中的 team_id={team_id}、player_id={player_id} 已是目标值，无需修改。"
            ),
        )

    # 放在子目录中并使用原文件名：部署时项目目录名依赖 zip 名，不能改变。
    work_dir = dest_dir / "identity"
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

    return IdentityRewriteResult(
        path=target,
        found=True,
        changed=True,
        changed_files=changed_files,
        message=(
            f"上传前已修改 zip 内配置：team_id={team_id}、player_id={player_id}"
            f"（{', '.join(changed_files)}）。"
        ),
    )
