"""测试辅助：构造各种 zip 工程。"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Dict, Union

Content = Union[str, bytes]


def build_zip(path: Path, files: Dict[str, Content]) -> Path:
    """按 ``{条目名: 内容}`` 构造 zip（父目录自动创建）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            if isinstance(content, str):
                content = content.encode("utf-8")
            archive.writestr(name, content)
    return path


def build_demo_project_zip(
    path: Path,
    top_dir: str = "demo",
    build_exit_code: int = 0,
    build_sleep: float = 0.0,
    include_start: bool = True,
    include_build: bool = True,
    start_content: str = "",
) -> Path:
    """构造一个可直接部署的示例工程 zip。

    目录结构（顶层目录模式）::

        demo/
          scripts/build.sh
          scripts/start.sh
          app/main.py
    """
    files: Dict[str, Content] = {
        f"{top_dir}/app/main.py": "print('hello booster')\n",
        f"{top_dir}/README.txt": "demo project\n",
    }
    if include_build:
        build_lines = [
            "#!/bin/bash",
            "set -u",
            "echo 'build-line-1'",
        ]
        if build_sleep:
            build_lines.append(f"sleep {build_sleep}")
        build_lines.extend(
            [
                "echo 'build-line-2'",
                "echo '生成 app.bin'",
                "printf 'artifact' > app.bin",
                f"exit {build_exit_code}",
            ]
        )
        files[f"{top_dir}/scripts/build.sh"] = "\n".join(build_lines) + "\n"
    if include_start:
        files[f"{top_dir}/scripts/start.sh"] = start_content or (
            "#!/bin/bash\n"
            "echo 'demo service starting'\n"
            "exec python3 -c \"import time; print('demo service running', flush=True); time.sleep(300)\"\n"
        )
    return build_zip(path, files)
