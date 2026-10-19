"""zip 包安全校验与项目目录推断（R5 / R6）。

本模块只做纯本地计算，不涉及网络，可独立单元测试。
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from .. import constants
from .errors import FileValidationError

# macOS 打包 zip 时自动附带的元数据，判断“唯一顶层目录”时应忽略
_IGNORED_TOP_LEVEL = {"__MACOSX", ".DS_Store"}


@dataclass(frozen=True)
class ZipInspection:
    """zip 校验 / 解析结果。"""

    path: Path
    entry_count: int
    top_level_dirs: Tuple[str, ...]
    top_level_files: Tuple[str, ...]
    project_subdir: Optional[str]  # 若 zip 内只有一个顶层目录，返回该目录名
    size: int

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def stem(self) -> str:
        return self.path.stem


def _split_name(name: str) -> List[str]:
    """把 zip 条目名拆成路径片段（兼容 Windows 风格反斜杠）。"""
    normalized = name.replace("\\", "/")
    return [part for part in normalized.split("/") if part not in ("", ".")]


def _is_illegal_entry(name: str) -> bool:
    """判断 zip 条目是否包含绝对路径 / 盘符 / ``..`` 等非法路径。"""
    raw = name.replace("\\", "/")
    if raw.startswith("/"):
        return True
    if len(raw) >= 2 and raw[1] == ":" and raw[0].isalpha():  # C:\xxx
        return True
    return ".." in _split_name(raw)


def _check_extension(path: Path) -> None:
    suffix = path.suffix.lower()
    if suffix not in constants.ALLOWED_EXTENSIONS:
        shown = path.suffix or "（无扩展名）"
        raise FileValidationError(
            f"只支持 .zip 压缩包，当前文件：{path.name}",
            hint=f"检测到的扩展名为 {shown}，请先打包成 zip 再上传。",
        )


def _check_magic(path: Path) -> None:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            header = handle.read(4)
    except OSError as exc:
        raise FileValidationError(
            f"无法读取文件：{path.name}",
            hint="请检查文件是否存在、是否有读取权限。",
            detail=str(exc),
        ) from exc

    if size == 0:
        raise FileValidationError(f"文件为空：{path.name}")
    if header not in constants.ZIP_MAGIC:
        raise FileValidationError(
            f"文件不是有效的 zip 压缩包：{path.name}",
            hint="文件头不是 PK\\x03\\x04，可能是改了扩展名的其他格式。",
        )


def _inspect_entries(path: Path) -> Tuple[int, Tuple[str, ...], Tuple[str, ...], Optional[str]]:
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise FileValidationError(
            f"zip 压缩包已损坏或格式不正确：{path.name}",
            hint="请重新打包后再试。",
            detail=str(exc),
        ) from exc

    top_dirs: List[str] = []
    top_files: List[str] = []
    entry_count = 0
    with archive:
        for info in archive.infolist():
            entry_count += 1
            name = info.filename
            if _is_illegal_entry(name):
                raise FileValidationError(
                    "压缩包包含非法路径，已中止。",
                    hint=f"非法条目：{name}（不允许绝对路径或 .. 路径）。",
                )
            parts = _split_name(name)
            if not parts:
                continue
            head = parts[0]
            if head in _IGNORED_TOP_LEVEL:
                continue
            is_dir = info.is_dir() or len(parts) > 1
            if len(parts) == 1 and not is_dir:
                if head not in top_files:
                    top_files.append(head)
            else:
                if head not in top_dirs:
                    top_dirs.append(head)

    project_subdir: Optional[str] = None
    if len(top_dirs) == 1 and not top_files:
        project_subdir = top_dirs[0]
    return entry_count, tuple(top_dirs), tuple(top_files), project_subdir


def inspect_zip(path: Path) -> ZipInspection:
    """完整校验 zip 并返回解析结果（扩展名 / 文件头 / 非法路径）。"""
    path = Path(path)
    _check_extension(path)
    _check_magic(path)
    entry_count, top_dirs, top_files, project_subdir = _inspect_entries(path)
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return ZipInspection(
        path=path,
        entry_count=entry_count,
        top_level_dirs=top_dirs,
        top_level_files=top_files,
        project_subdir=project_subdir,
        size=size,
    )


def validate_zip(path: Path) -> ZipInspection:
    """``inspect_zip`` 的语义化别名（校验失败抛出 FileValidationError）。"""
    return inspect_zip(path)


def zip_stem(zip_path: Path) -> str:
    """由 zip 文件名得到解压目标目录名（去掉 .zip 扩展名）。"""
    return Path(zip_path).stem


def resolve_project_subdir(
    inspection: ZipInspection, extracted_entries: Optional[List[str]] = None
) -> Optional[str]:
    """推断项目目录名。

    优先使用 zip 结构判断（唯一顶层目录 → 取该目录，避免 xxx/xxx 双重嵌套）；
    若调用方传入了解压后的目录列表，则以实际解压结果为准做交叉校验。
    """
    if inspection.project_subdir:
        if extracted_entries is None or inspection.project_subdir in extracted_entries:
            return inspection.project_subdir
    if extracted_entries:
        meaningful = [name for name in extracted_entries if name not in _IGNORED_TOP_LEVEL]
        if len(meaningful) == 1:
            return meaningful[0]
    return None
