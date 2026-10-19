"""会话管理器：维护“当前机器人 + 常驻 SSH 连接”。

界面层只和本类交互，保证：
* 同一时刻只有一条 SSH 连接（``SSHClient`` 常驻复用，不每次操作重连）；
* 会话连接、SFTP、命令执行统一加锁，避免多线程并发操作同一通道导致异常。
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from .errors import BrmError, SessionNotConnectedError
from .robots import Robot, RobotStore
from .sftp_client import SFTPClient
from .ssh_client import CommandResult, SSHSession


class SessionManager:
    def __init__(self, store: RobotStore) -> None:
        self.store = store
        self._session: Optional[SSHSession] = None
        self._sftp: Optional[SFTPClient] = None
        self._robot_name: Optional[str] = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    @property
    def robot_name(self) -> Optional[str]:
        return self._robot_name

    @property
    def connected(self) -> bool:
        session = self._session
        return bool(session is not None and session.connected)

    def current_robot(self) -> Optional[Robot]:
        if self._robot_name is None:
            return None
        return self.store.get_robot(self._robot_name)

    # ------------------------------------------------------------------
    def _build_session(self, robot: Robot, password: Optional[str]) -> SSHSession:
        return SSHSession(
            host=robot.host,
            port=robot.port,
            username=robot.username,
            password=password,
            key_path=robot.key_path if robot.auth_type == "key" else None,
            connect_timeout=self.store.settings.connect_timeout,
            command_timeout=self.store.settings.command_timeout,
        )

    def connect(
        self,
        robot_name: str,
        password_override: Optional[str] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> Robot:
        """连接指定机器人。密码优先使用传入值，否则使用已保存密码（默认复用）。"""
        robot = self.store.get_robot(robot_name)
        if robot is None:
            raise BrmError(f"未找到机器人：{robot_name}")

        with self._lock:
            if (
                self._session is not None
                and self._robot_name == robot_name
                and self._session.connected
            ):
                return robot

            self.disconnect_locked()

            password = password_override
            if password is None:
                password = self.store.resolve_password(robot)
            if robot.auth_type == "password" and not password:
                raise BrmError(
                    "尚未设置机器人密码，请先设置。",
                    hint="点击右上角「⚙ 密码设置」设置默认密码，或在机器人编辑对话框单独填写。",
                )

            if log is not None:
                log(f"正在连接 {robot.name}（{robot.username}@{robot.host}:{robot.port}）…")

            session = self._build_session(robot, password)
            session.connect()
            self._session = session
            self._sftp = SFTPClient(session)
            self._robot_name = robot_name
            if log is not None:
                log(f"已连接 {robot.name}（{robot.host}）")
            return robot

    def disconnect(self) -> None:
        with self._lock:
            self.disconnect_locked()

    def disconnect_locked(self) -> None:
        if self._sftp is not None:
            self._sftp.close()
            self._sftp = None
        if self._session is not None:
            self._session.close()
            self._session = None
        self._robot_name = None

    # ------------------------------------------------------------------
    def session(self) -> SSHSession:
        with self._lock:
            if self._session is None or not self._session.connected:
                raise SessionNotConnectedError(
                    "尚未连接机器人。", hint="请先在左侧点击机器人建立连接。"
                )
            return self._session

    def sftp(self) -> SFTPClient:
        with self._lock:
            if self._sftp is None:
                raise SessionNotConnectedError(
                    "尚未连接机器人。", hint="请先在左侧点击机器人建立连接。"
                )
            return self._sftp

    def exec_command(self, command: str, timeout: Optional[float] = None) -> CommandResult:
        return self.session().exec_command(command, timeout=timeout)

    def exec_stream(
        self,
        command: str,
        on_line: Callable[[str], None],
        timeout: Optional[float] = None,
    ) -> CommandResult:
        return self.session().exec_stream(command, on_line, timeout=timeout)

    # ------------------------------------------------------------------
    def test_connection(
        self,
        robot: Robot,
        password: Optional[str],
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        """用候选密码做一次独立连接测试（不影响当前常驻连接）。"""
        if robot.auth_type == "password" and not password:
            raise BrmError(
                "请先输入要测试的密码。",
                hint="密码留空时无法测试。",
            )
        session = self._build_session(robot, password)
        try:
            session.connect()
            if log is not None:
                log(f"已成功登录 {robot.username}@{robot.host}:{robot.port}")
        finally:
            session.close()
