"""本地工程格式检测：用于「直接启动」已部署工程。

BRM 的正常流程是“上传 zip → 解压 → 编译 → 启动”。如果用户选择的本地文件夹
已经是符合 Booster 工程格式的目录，并且机器人上已经存在同名已编译工程，
就可以跳过上传/编译，直接调用远端已有的 start.sh。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class LocalProjectFormat:
    path: Path
    name: str
    start_script: Optional[Path] = None
    has_src: bool = False
    compiled: bool = False
    valid: bool = False
    problems: List[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.valid:
            return "不符合直接启动格式：" + "；".join(self.problems)
        state = "已编译" if self.compiled else "源码"
        return f"格式有效（{state}）"


def detect_local_project(folder: Path) -> LocalProjectFormat:
    """检查本地文件夹是否符合 Booster 工程格式。

    最低要求：
    * 存在 ``scripts/start.sh``（兼容旧的 ``script/`` 或根目录 ``start.sh``）；
    * 存在 ``src/`` 目录。

    ``install/`` 或 ``build/`` 存在时标记为“已编译”，但不是硬性要求：
    远端工程是否真的已编译由 BRM 启动前再次检查 start.sh 决定。
    """
    folder = Path(folder)
    name = folder.name
    result = LocalProjectFormat(path=folder, name=name)

    candidates = [
        folder / "scripts" / "start.sh",
        folder / "script" / "start.sh",
        folder / "start.sh",
    ]
    for candidate in candidates:
        if candidate.is_file():
            result.start_script = candidate
            break
    if result.start_script is None:
        result.problems.append("未找到 scripts/start.sh")

    result.has_src = (folder / "src").is_dir()
    if not result.has_src:
        result.problems.append("未找到 src/ 目录")

    result.compiled = (folder / "install").is_dir() or (folder / "build").is_dir()
    result.valid = result.start_script is not None and result.has_src
    return result
