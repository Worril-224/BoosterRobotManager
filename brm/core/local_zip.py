"""把本地文件夹压缩为 zip（部署前自动打包）。

用户选择的是文件夹时，BRM 会先用标准库 ``zipfile`` 在本地打包成 zip，
再走原来的「上传 → 解压 → 编译 → 授权」流程，不依赖任何外部压缩工具。
"""

from __future__ import annotations

import os
import shutil
import threading
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .errors import BrmError
from .util import human_size, sanitize_filename

# 打包时跳过的目录（版本库 / 构建产物 / 依赖缓存，部署时会重新生成）
EXCLUDED_DIR_NAMES = {
    ".git",
    ".svn",
    ".hg",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".idea",
    ".vscode",
    "build",
    "install",
    "log",
    "logs",
}

EXCLUDED_FILE_NAMES = {".DS_Store", "Thumbs.db"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}

_CHUNK_SIZE = 1024 * 1024


@dataclass
class LocalZipResult:
    """本地打包结果。"""

    path: Path
    source_dir: Path
    file_count: int
    total_bytes: int
    skipped: List[str] = field(default_factory=list)
    rerun_enabled: bool = False
    rerun_changed: bool = False
    local_ip: str = ""


def _collect_files(source_dir: Path) -> Tuple[List[Tuple[Path, str]], int, List[str]]:
    """遍历文件夹，返回 [(本地文件, zip 内路径)], 总字节数, 跳过的符号链接。"""
    files: List[Tuple[Path, str]] = []
    skipped: List[str] = []
    total_bytes = 0
    root_name = source_dir.name

    for current, dir_names, file_names in os.walk(source_dir, followlinks=False):
        current_path = Path(current)
        kept_dirs = []
        for name in sorted(dir_names):
            candidate = current_path / name
            if name in EXCLUDED_DIR_NAMES:
                continue
            if candidate.is_symlink():
                skipped.append(str(candidate))
                continue
            kept_dirs.append(name)
        dir_names[:] = kept_dirs

        for name in sorted(file_names):
            candidate = current_path / name
            if name in EXCLUDED_FILE_NAMES or candidate.suffix.lower() in EXCLUDED_SUFFIXES:
                continue
            if candidate.is_symlink():
                skipped.append(str(candidate))
                continue
            try:
                size = candidate.stat().st_size
            except OSError:
                skipped.append(str(candidate))
                continue
            relative = candidate.relative_to(source_dir).as_posix()
            files.append((candidate, f"{root_name}/{relative}"))
            total_bytes += size

    return files, total_bytes, skipped


def create_zip_from_folder(
    source_dir: Path,
    dest_dir: Path,
    progress: Optional[Callable[[int], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    overrides: Optional[Dict[Path, bytes]] = None,
) -> LocalZipResult:
    """把 ``source_dir`` 压缩为 ``dest_dir/<文件夹名>.zip``。

    * zip 内包含顶层文件夹本身（解压后是 ``<文件夹名>/...``）；
    * 跳过 ``.git`` / ``build`` / ``install`` / ``__pycache__`` 等目录；
    * 保留可执行位（Unix 权限写入 zip external_attr）；
    * 支持进度回调（0-100）与取消；
    * ``overrides`` 可替换 zip 内指定源文件的内容（例如只改 zip 内的
      config.yaml，而不动用户本地文件）。
    """
    source_dir = Path(source_dir)
    if not source_dir.is_dir():
        raise BrmError(f"不是文件夹：{source_dir}")

    files, total_bytes, skipped = _collect_files(source_dir)
    if not files:
        raise BrmError(
            f"文件夹中没有可打包的文件：{source_dir}",
            hint="请确认选择的文件夹不是空目录。",
        )

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"{sanitize_filename(source_dir.name)}.zip"
    partial = target.with_name(target.name + ".part")
    if partial.exists():
        partial.unlink()

    written = 0
    last_percent = -1

    def report(force: bool = False) -> None:
        nonlocal last_percent
        if progress is None:
            return
        percent = 100 if total_bytes == 0 else int(written * 100 / total_bytes)
        percent = max(0, min(100, percent))
        if force or percent != last_percent:
            last_percent = percent
            try:
                progress(percent)
            except Exception:
                pass

    report(force=True)
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for source, arcname in files:
                if cancel_event is not None and cancel_event.is_set():
                    raise BrmError("压缩已取消。", hint="未生成 zip 文件。")
                info = zipfile.ZipInfo.from_file(source, arcname)
                info.compress_type = zipfile.ZIP_DEFLATED
                if source.suffix.lower() == ".sh":
                    # Windows 源文件没有 Unix 可执行位，这里统一补上，
                    # 避免远端执行 ./scripts/xxx.sh 时出现 Permission denied
                    mode = (info.external_attr >> 16) | 0o111
                    info.external_attr = (mode & 0xFFFF) << 16
                replacement = (overrides or {}).get(source)
                if replacement is not None:
                    archive.writestr(info, replacement)
                    written += len(replacement)
                    report(force=True)
                    continue
                with source.open("rb") as reader, archive.open(info, "w") as writer:
                    while True:
                        if cancel_event is not None and cancel_event.is_set():
                            raise BrmError("压缩已取消。", hint="未生成 zip 文件。")
                        chunk = reader.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        writer.write(chunk)
                        written += len(chunk)
                        report()
        os.replace(partial, target)
    finally:
        if partial.exists():
            try:
                partial.unlink()
            except OSError:
                pass

    report(force=True)
    return LocalZipResult(
        path=target,
        source_dir=source_dir,
        file_count=len(files),
        total_bytes=total_bytes,
        skipped=skipped,
    )


def cleanup_temp_archives(directory: Path, older_than_seconds: float | None = None) -> int:
    """清理临时打包目录（程序启动 / 退出时调用），返回删除的条目数。

    ``older_than_seconds`` 不为 None 时只清理超过该时间的条目（避免误删另一个
    正在运行的实例刚生成的 zip）。
    """
    import time

    directory = Path(directory)
    if not directory.exists():
        return 0
    removed = 0
    now = time.time()
    for child in directory.iterdir():
        try:
            if older_than_seconds is not None:
                age = now - child.stat().st_mtime
                if age < older_than_seconds:
                    continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def describe_result(result: LocalZipResult) -> str:
    """生成适合写日志的一行描述。"""
    text = (
        f"已自动打包 {result.file_count} 个文件"
        f"（原始大小 {human_size(result.total_bytes)}）→ {result.path}"
    )
    if result.skipped:
        text += f"；跳过 {len(result.skipped)} 个符号链接"
    return text
