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
import time
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from .. import constants

RERUN_DIR_NAME = "rerun-0.20.3"
RERUN_GRPC_PORT = 9876
RERUN_READY_TIMEOUT = 30.0


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
        bases: List[Path] = []
        if self._project_dir is not None:
            bases.append(self._project_dir)
        bases.extend(self._extra_dirs)
        # 源码运行 / macOS 时：BoosterRobotManager/rerun-0.20.3
        bases.append(constants.APP_ROOT.parent)
        # 打包后的 Windows 目录：可执行文件所在目录
        bases.append(constants.APP_ROOT)
        bases.append(Path.cwd())

        dirs: List[Path] = []
        for base in bases:
            # 子目录形式：rerun-0.20.3/rerun(.exe)
            dirs.append(base / RERUN_DIR_NAME)
            # 用户直接把 rerun-cli-*.exe 放到 BRM / 工程根目录的情况
            dirs.append(base)

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
        if directory.name == RERUN_DIR_NAME:
            names = (
                ["rerun.exe", "rerun"]
                if sys.platform == "win32"
                else ["rerun", "rerun.exe"]
            )
            for name in names:
                candidate = directory / name
                if candidate.is_file():
                    return candidate
            # macOS 本地封装的 .app
            for app in sorted(directory.glob("Rerun*.app")):
                if app.is_dir():
                    return app
            return None

        # 根目录下直接放置的 Windows rerun-cli-*.exe
        if sys.platform == "win32":
            patterns = [
                "rerun-cli-*windows*.exe",
                "rerun-cli-*.exe",
                "rerun*.exe",
            ]
            for pattern in patterns:
                for candidate in sorted(directory.glob(pattern)):
                    if candidate.is_file():
                        return candidate
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
        """查看器是否真正就绪（以 9876 端口监听为准，而不是进程还活着）。"""
        return self._port_open()

    # ------------------------------------------------------------------
    def ensure_started(
        self,
        log: Callable[[str], None],
        ready_timeout: float = RERUN_READY_TIMEOUT,
    ) -> Optional[bool]:
        """确保查看器就绪。

        返回：
        * ``True``：查看器已运行并监听 9876；
        * ``None``：本机没有找到 rerun（视为未使用 rerun，调用方可继续）；
        * ``False``：找到了 rerun 但启动失败/退出/等待就绪超时。

        该方法可能阻塞，必须放在工作线程中调用（ProcWorker 会在运行
        start.sh 之前调用它），避免卡住界面。
        """
        if self.is_running():
            log("rerun 查看器已在运行（gRPC 9876），无需等待。")
            return True

        if self._process is not None and self._process.poll() is None:
            log("检测到 rerun 进程仍在启动中，继续等待其监听 9876…")
        else:
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
                log(f"已启动 rerun 查看器：{target}，等待就绪…")
                break
            else:
                log(
                    f"未找到 {RERUN_DIR_NAME}/rerun 或 rerun-cli-*.exe"
                    "（已查找工程目录和 BRM 目录），跳过 rerun。"
                )
                return None

        deadline = time.monotonic() + max(0.5, ready_timeout)
        while time.monotonic() < deadline:
            if self._port_open():
                log("rerun 查看器已就绪（gRPC 9876）。")
                return True
            if self._process is not None and self._process.poll() is not None:
                code = self._process.returncode
                log(f"rerun 进程已退出（退出码 {code}），未能就绪。")
                return False
            time.sleep(0.2)

        log(
            f"等待 rerun 就绪超时（{ready_timeout:.0f} 秒），未检测到 9876 端口。"
        )
        return False
