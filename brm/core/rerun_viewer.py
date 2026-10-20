"""在运行 start.sh 前自动打开本机的 rerun 查看器。

工程目录里通常带有 ``rerun-0.20.3/``（官方单文件二进制 / macOS .app）。
BRM 会在本机启动其中的 ``rerun`` 查看器；它默认监听 gRPC 9876，配合
``src/brain/config/config.yaml`` 中 rerunLog.server_ip 指向本机 IP，
机器人即可把 rerun 数据发到本机查看器。
"""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from .. import constants

RERUN_DIR_NAME = "rerun-0.20.3"
RERUN_GRPC_PORT = 9876


class RerunViewerLauncher:
    """负责定位并启动本机 rerun 查看器（每个进程只启动一次）。"""

    def __init__(self, extra_dirs: Iterable[Path] = ()) -> None:
        self._project_dir: Optional[Path] = None
        self._extra_dirs: List[Path] = [Path(path) for path in extra_dirs if path]
        self._process: Optional[subprocess.Popen] = None

    def set_project_dir(self, project_dir: Optional[Path]) -> None:
        self._project_dir = Path(project_dir) if project_dir else None

    # ------------------------------------------------------------------
    def _candidate_dirs(self) -> List[Path]:
        dirs: List[Path] = []
        if self._project_dir is not None:
            dirs.append(self._project_dir / RERUN_DIR_NAME)
        dirs.extend(self._extra_dirs)
        # 源码运行 / macOS 时：BoosterRobotManager/rerun-0.20.3
        dirs.append(constants.APP_ROOT.parent / RERUN_DIR_NAME)
        # 打包后的 Windows 目录：可执行文件所在目录/rerun-0.20.3
        dirs.append(constants.APP_ROOT / RERUN_DIR_NAME)
        dirs.append(Path.cwd() / RERUN_DIR_NAME)

        unique: List[Path] = []
        seen = set()
        for directory in dirs:
            try:
                resolved = directory.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            if resolved.is_dir():
                unique.append(resolved)
        return unique

    @staticmethod
    def _find_launch_target(directory: Path) -> Optional[Path]:
        names = ["rerun.exe", "rerun"] if sys.platform == "win32" else ["rerun", "rerun.exe"]
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return candidate
        # macOS 本地封装的 .app
        for app in sorted(directory.glob("Rerun*.app")):
            if app.is_dir():
                return app
        return None

    @staticmethod
    def _port_open(port: int = RERUN_GRPC_PORT) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.2)
        try:
            return sock.connect_ex(("127.0.0.1", port)) == 0
        except OSError:
            return False
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def is_running(self) -> bool:
        if self._process is not None and self._process.poll() is None:
            return True
        return self._port_open()

    # ------------------------------------------------------------------
    def ensure_started(self, log: Callable[[str], None]) -> bool:
        """若查看器未运行则启动；失败只记日志，不阻塞 start.sh。"""
        if self.is_running():
            log("rerun 查看器已在运行（gRPC 9876），跳过自动打开。")
            return True

        for directory in self._candidate_dirs():
            target = self._find_launch_target(directory)
            if target is None:
                continue
            try:
                if target.suffix.lower() == ".app":
                    subprocess.Popen(
                        ["open", str(target)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                else:
                    self._process = subprocess.Popen(
                        [str(target)],
                        cwd=str(directory),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
            except OSError as exc:
                log(f"启动 rerun 查看器失败（{target}）：{exc}")
                continue
            log(f"已自动打开 rerun 查看器：{target}")
            return True

        log(
            f"未找到 {RERUN_DIR_NAME}/rerun（已查找工程目录和 BRM 目录），"
            "跳过自动打开 rerun。"
        )
        return False
