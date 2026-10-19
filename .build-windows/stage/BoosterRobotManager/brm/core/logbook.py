"""本地部署日志（R3 / 健壮性第 3 条）。

每次部署在 ``brm/logs/`` 下生成一份带时间戳的日志文件，内容与界面日志完全一致；
写入前统一做密码打码，避免敏感信息落盘。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Iterable, Optional

from .. import constants
from .util import clock_text, mask_secrets, sanitize_filename, timestamp_slug


class DeployLog:
    """同时向界面回调与本地文件写日志。"""

    def __init__(
        self,
        robot_name: str,
        log_dir: Optional[Path] = None,
        echo: Optional[Callable[[str], None]] = None,
        secrets: Iterable[str] = (),
    ) -> None:
        self.robot_name = robot_name
        self.echo = echo
        self._secrets = [secret for secret in secrets if secret]
        self._lock = threading.Lock()
        directory = Path(log_dir) if log_dir else constants.LOG_DIR
        directory.mkdir(parents=True, exist_ok=True)
        filename = f"deploy-{timestamp_slug()}-{sanitize_filename(robot_name)}.log"
        self.path = directory / filename
        self._handle = self.path.open("w", encoding="utf-8", newline="\n")
        self._closed = False

    # -- 密码打码 -----------------------------------------------------------
    def add_secret(self, secret: str) -> None:
        if secret and secret not in self._secrets:
            self._secrets.append(secret)

    def _mask(self, text: str) -> str:
        return mask_secrets(text, self._secrets)

    # -- 写入 ---------------------------------------------------------------
    def _write(self, text: str) -> None:
        masked = self._mask(text)
        with self._lock:
            if self._closed:
                return
            try:
                self._handle.write(masked + "\n")
                self._handle.flush()
            except OSError:
                pass
        if self.echo is not None:
            try:
                self.echo(masked)
            except Exception:  # 界面回调异常不能影响部署
                pass

    def info(self, message: str) -> None:
        self._write(f"[{clock_text()}] [信息] {message}")

    def warn(self, message: str) -> None:
        self._write(f"[{clock_text()}] [警告] {message}")

    def error(self, message: str) -> None:
        self._write(f"[{clock_text()}] [错误] {message}")

    def raw(self, message: str) -> None:
        """原样输出（用于编译 / 脚本的标准输出回显）。"""
        self._write(message)

    def stage(self, title: str) -> None:
        """阶段分隔线，让界面日志更易读。"""
        self._write("")
        self._write(f"──────── {title} ────────")

    # -- 收尾 ---------------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._handle.close()
            except OSError:
                pass

    def __enter__(self) -> "DeployLog":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
