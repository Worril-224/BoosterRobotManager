"""远端脚本查找（R7 / R8 共用）。

规则：优先 ``<项目目录>/scripts/<脚本名>``（Booster 工程常用目录）；
兼容旧的 ``<项目目录>/script/<脚本名>``；都找不到时在项目目录内递归查找；
必须唯一，找不到或多于一个都报错。
"""

from __future__ import annotations

from typing import List

from .errors import ScriptNotFoundError
from .util import posix_join, quote

# 工程内脚本目录的查找顺序（scripts 为当前约定，script 为旧约定）
SCRIPT_DIR_NAMES = ("scripts", "script")


def _filter_metadata(paths: List[str]) -> List[str]:
    return [
        path
        for path in paths
        if "/__MACOSX/" not in path and not path.endswith("/.DS_Store")
    ]


def find_script(session, sftp, project_dir: str, script_name: str, log=None) -> str:
    """在项目目录中定位脚本的绝对路径。"""
    for dir_name in SCRIPT_DIR_NAMES:
        preferred = posix_join(project_dir, dir_name, script_name)
        if sftp.exists(preferred):
            return preferred

    command = f"find {quote(project_dir)} -type f -name {quote(script_name)} 2>/dev/null"
    result = session.exec_command(command)
    candidates = _filter_metadata(
        [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    )
    if not candidates:
        raise ScriptNotFoundError(
            f"未找到 {script_name}",
            hint=(
                f"请确认 {project_dir}/scripts/{script_name} 存在，"
                f"或项目内存在唯一的 {script_name}。"
            ),
            detail=result.output.strip() or "",
        )
    if len(candidates) > 1:
        raise ScriptNotFoundError(
            f"在项目目录内找到多个 {script_name}，无法自动选择。",
            hint="请整理工程结构，保留唯一脚本（推荐放在 scripts/ 目录下）。",
            detail="\n".join(candidates),
        )
    found = candidates[0]
    if log is not None:
        log(f"未找到 scripts/{script_name}，使用递归查找到的：{found}")
    return found


def find_build_script(session, sftp, project_dir: str, log=None) -> str:
    return find_script(session, sftp, project_dir, "build.sh", log=log)


def find_start_script(session, sftp, project_dir: str, log=None) -> str:
    return find_script(session, sftp, project_dir, "start.sh", log=log)
