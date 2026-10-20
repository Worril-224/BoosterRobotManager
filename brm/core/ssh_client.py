"""SSH 连接、命令执行与流式输出（R1 / R7）。

关键点：
* 常驻复用 :class:`paramiko.SSHClient`，上层所有操作共用一条连接；
* 连接异常统一经 :func:`errors.classify_connection_error` 分级，只有认证失败
  才会得到 ``AuthFailedError``；
* 流式执行使用 channel 的 ``set_combine_stderr`` 合并 stdout/stderr，边读边回调，
  不会等命令结束才一次性输出。
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import paramiko

from .. import constants
from .errors import (
    BrmError,
    CommandTimeoutError,
    SessionNotConnectedError,
    classify_connection_error,
)


@dataclass
class CommandResult:
    """一次远端命令的执行结果。"""

    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""

    @property
    def output(self) -> str:
        """stdout 与 stderr 合并后的文本（顺序不保证，仅用于判断失败原因）。"""
        parts = [part for part in (self.stdout, self.stderr) if part]
        return "\n".join(part.rstrip("\n") for part in parts)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def _decode(data: object) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)


class SSHSession:
    """一台机器人的 SSH 会话（连接成功后常驻复用）。"""

    def __init__(
        self,
        host: str,
        port: int = constants.DEFAULT_PORT,
        username: str = constants.DEFAULT_USERNAME,
        *,
        password: Optional[str] = None,
        key_path: Optional[str] = None,
        connect_timeout: int = constants.DEFAULT_CONNECT_TIMEOUT,
        command_timeout: int = constants.DEFAULT_COMMAND_TIMEOUT,
        allow_agent: bool = False,
        look_for_keys: bool = False,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.username = username
        self._password = password or ""
        self._key_path = key_path or ""
        self.connect_timeout = int(connect_timeout)
        self.command_timeout = int(command_timeout)
        self.allow_agent = allow_agent
        self.look_for_keys = look_for_keys

        self._client: Optional[paramiko.SSHClient] = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------
    @property
    def connected(self) -> bool:
        client = self._client
        if client is None:
            return False
        transport = client.get_transport()
        return bool(transport is not None and transport.is_active())

    def set_password(self, password: str) -> None:
        self._password = password or ""

    def connect(self) -> None:
        """建立连接；失败时抛出分级后的中文错误。"""
        with self._lock:
            self.close()

            if not self._password and not self._key_path:
                raise BrmError(
                    "尚未设置机器人密码，请先设置。",
                    hint="点击右上角「⚙ 密码设置」设置默认密码，或在机器人编辑对话框单独填写。",
                )
            if self._key_path:
                key_file = Path(self._key_path).expanduser()
                if not key_file.is_file():
                    raise BrmError(
                        f"私钥文件不存在：{self._key_path}",
                        hint="请重新选择私钥文件（通常是 id_rsa / id_ed25519）。",
                    )

            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:  # 有 known_hosts 时启用主机密钥校验，密钥变化会报 HostKeyError
                client.load_system_host_keys()
            except Exception:
                pass

            kwargs = {
                "hostname": self.host,
                "port": self.port,
                "username": self.username,
                "timeout": self.connect_timeout,
                "banner_timeout": max(self.connect_timeout, 5),
                "auth_timeout": max(self.connect_timeout, 5),
                "allow_agent": self.allow_agent,
                "look_for_keys": self.look_for_keys,
            }
            if self._password:
                kwargs["password"] = self._password
            if self._key_path:
                kwargs["key_filename"] = str(Path(self._key_path).expanduser())

            try:
                client.connect(**kwargs)
            except Exception as exc:  # noqa: BLE001 - 统一转换为分级错误
                try:
                    client.close()
                except Exception:
                    pass
                raise classify_connection_error(exc, self.host, self.port) from exc

            self._client = client

    def close(self) -> None:
        with self._lock:
            client, self._client = self._client, None
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    def _ensure_connected(self) -> paramiko.SSHClient:
        client = self._client
        if client is None:
            raise SessionNotConnectedError("尚未连接机器人。", hint="请先在左侧选择机器人并等待连接成功。")
        transport = client.get_transport()
        if transport is None or not transport.is_active():
            self._client = None
            raise SessionNotConnectedError(
                "与机器人的连接已断开。",
                hint="请点击机器人重新连接；若网络中断，请检查网线 / Wi-Fi。",
            )
        return client

    # ------------------------------------------------------------------
    # 命令执行
    # ------------------------------------------------------------------
    def exec_command(self, command: str, timeout: Optional[float] = None) -> CommandResult:
        """执行命令并返回完整输出（用于短命令，如 rm / chmod / cat）。"""
        with self._lock:
            client = self._ensure_connected()
            limit = float(timeout if timeout is not None else self.command_timeout)
            try:
                _stdin, stdout, stderr = client.exec_command(command, timeout=limit)
                out = _decode(stdout.read())
                err = _decode(stderr.read())
                exit_code = stdout.channel.recv_exit_status()
            except socket.timeout as exc:
                raise CommandTimeoutError(
                    f"命令执行超时（超过 {int(limit)} 秒）。",
                    hint="请检查机器人负载或网络状态，必要时在配置中调大命令超时时间。",
                    detail=command,
                ) from exc
            except BrmError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise classify_connection_error(exc, self.host, self.port) from exc
            return CommandResult(command=command, exit_code=exit_code, stdout=out, stderr=err)

    def exec_command_with_input(
        self,
        command: str,
        input_text: str,
        timeout: Optional[float] = None,
    ) -> CommandResult:
        """执行命令并在开始时向其标准输入写入内容（用于自动输入密码）。

        密码只通过加密的 SSH 通道写入远端进程的标准输入，**不会出现在命令行
        参数里**（远端 ``ps`` 看不到），也不会写入本地日志。
        """
        with self._lock:
            client = self._ensure_connected()
            limit = float(timeout if timeout is not None else self.command_timeout)
            try:
                stdin, stdout, stderr = client.exec_command(command, timeout=limit)
                try:
                    stdin.write(input_text)
                    stdin.flush()
                finally:
                    try:
                        stdin.channel.shutdown_write()
                    except Exception:
                        pass
                out = _decode(stdout.read())
                err = _decode(stderr.read())
                exit_code = stdout.channel.recv_exit_status()
            except socket.timeout as exc:
                raise CommandTimeoutError(
                    f"命令执行超时（超过 {int(limit)} 秒）。",
                    hint="请检查机器人负载或网络状态，必要时在配置中调大命令超时时间。",
                    detail=command,
                ) from exc
            except BrmError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise classify_connection_error(exc, self.host, self.port) from exc
            return CommandResult(command=command, exit_code=exit_code, stdout=out, stderr=err)

    def exec_stream(
        self,
        command: str,
        on_line: Callable[[str], None],
        timeout: Optional[float] = None,
    ) -> CommandResult:
        """执行命令并把合并后的 stdout/stderr 逐行回调（用于编译等长输出命令）。

        返回的 :class:`CommandResult` 中 ``stdout`` 为完整合并输出。
        """
        with self._lock:
            client = self._ensure_connected()
            limit = float(timeout if timeout is not None else self.command_timeout)
            deadline = time.monotonic() + limit

            try:
                transport = client.get_transport()
                if transport is None:
                    raise SessionNotConnectedError("SSH 通道不可用，请重新连接。")
                channel = transport.open_session(timeout=self.connect_timeout)
                channel.set_combine_stderr(True)
                channel.settimeout(0.5)
                channel.exec_command(command)
            except BrmError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise classify_connection_error(exc, self.host, self.port) from exc

            chunks = []
            pending = ""
            try:
                while True:
                    if time.monotonic() > deadline:
                        raise CommandTimeoutError(
                            f"命令执行超时（超过 {int(limit)} 秒）。",
                            hint="可在配置中调大命令超时时间，或检查机器人负载。",
                            detail=command,
                        )
                    try:
                        data = channel.recv(65536)
                    except socket.timeout:
                        if channel.exit_status_ready() and not channel.recv_ready():
                            break
                        continue
                    if not data:
                        break
                    text = data.decode("utf-8", errors="replace")
                    chunks.append(text)
                    pending += text
                    while "\n" in pending:
                        line, pending = pending.split("\n", 1)
                        self._safe_callback(on_line, line.rstrip("\r"))
                if pending:
                    self._safe_callback(on_line, pending.rstrip("\r"))
                exit_code = channel.recv_exit_status()
            except BrmError:
                try:
                    channel.close()
                except Exception:
                    pass
                raise
            except Exception as exc:  # noqa: BLE001
                try:
                    channel.close()
                except Exception:
                    pass
                raise classify_connection_error(exc, self.host, self.port) from exc
            finally:
                try:
                    channel.close()
                except Exception:
                    pass

            return CommandResult(command=command, exit_code=exit_code, stdout="".join(chunks))

    @staticmethod
    def _safe_callback(callback: Callable[[str], None], line: str) -> None:
        try:
            callback(line)
        except Exception:
            # 界面回调异常不应打断命令输出读取
            pass

    def open_stream_channel(self, command: str):
        """为 ``tail -f`` 等长命令打开一个独立 channel。

        与 :meth:`exec_stream` 不同，这里不在读取期间持有会话锁，
        因此可以在另一个线程里持续读取，同时不影响状态查询 / 启停等操作。
        调用方负责关闭返回的 channel。
        """
        with self._lock:
            client = self._ensure_connected()
            transport = client.get_transport()
            if transport is None:
                raise SessionNotConnectedError("SSH 通道不可用，请重新连接。")
            try:
                channel = transport.open_session(timeout=self.connect_timeout)
                channel.set_combine_stderr(True)
                channel.settimeout(0.5)
                channel.exec_command(command)
                return channel
            except BrmError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise classify_connection_error(exc, self.host, self.port) from exc

    # ------------------------------------------------------------------
    # SFTP
    # ------------------------------------------------------------------
    def open_sftp(self):
        """打开 SFTP 客户端（由 :class:`SFTPClient` 封装并缓存）。"""
        with self._lock:
            client = self._ensure_connected()
            try:
                return client.open_sftp()
            except Exception as exc:  # noqa: BLE001
                raise classify_connection_error(exc, self.host, self.port) from exc
