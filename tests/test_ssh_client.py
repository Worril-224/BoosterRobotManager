"""SSH 会话封装的单元测试（用假 paramiko 对象，不涉及真实网络）。"""

from __future__ import annotations

import socket
from typing import List

import pytest

from brm.core.errors import CommandTimeoutError, SessionNotConnectedError
from brm.core.ssh_client import SSHSession


# ---------------------------------------------------------------------------
# 假 paramiko 对象
# ---------------------------------------------------------------------------
class FakeChannel:
    def __init__(self, chunks: List[bytes], exit_code: int = 0, always_timeout: bool = False) -> None:
        self._chunks = list(chunks)
        self._exit_code = exit_code
        self._always_timeout = always_timeout
        self.closed = False
        self.timeout = None
        self.combine_stderr = False
        self.write_shutdown = False

    def set_combine_stderr(self, flag: bool) -> None:
        self.combine_stderr = flag

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def exec_command(self, command: str) -> None:
        return None

    def recv(self, _size: int) -> bytes:
        if self._always_timeout:
            raise socket.timeout("simulated timeout")
        if self._chunks:
            return self._chunks.pop(0)
        return b""

    def exit_status_ready(self) -> bool:
        if self._always_timeout:
            return False
        return not self._chunks

    def recv_ready(self) -> bool:
        return bool(self._chunks)

    def recv_exit_status(self) -> int:
        return self._exit_code

    def shutdown_write(self) -> None:
        self.write_shutdown = True

    def close(self) -> None:
        self.closed = True


class FakeStdin:
    """记录写入远端标准输入的内容。"""

    def __init__(self, channel: FakeChannel) -> None:
        self.channel = channel
        self.written = b""

    def write(self, data) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.written += data

    def flush(self) -> None:
        return None


class FakeStream:
    def __init__(self, data: bytes, channel: FakeChannel) -> None:
        self._data = data
        self.channel = channel

    def read(self) -> bytes:
        return self._data


class FakeTransport:
    def __init__(self, channel: FakeChannel, active: bool = True) -> None:
        self._channel = channel
        self._active = active

    def is_active(self) -> bool:
        return self._active

    def open_session(self, timeout: float = 10):
        return self._channel


class FakeClient:
    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        exit_code: int = 0,
        stream_chunks: List[bytes] | None = None,
        stream_exit_code: int = 0,
        stream_timeout: bool = False,
        active: bool = True,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self._exit_code = exit_code
        self._stream_chunks = stream_chunks or []
        self._stream_exit_code = stream_exit_code
        self._stream_timeout = stream_timeout
        self._active = active
        self.closed = False
        self.last_command = ""
        self.last_channel = None
        self.last_stdin = None

    def exec_command(self, command: str, timeout=None):
        self.last_command = command
        channel = FakeChannel([], exit_code=self._exit_code)
        self.last_channel = channel
        self.last_stdin = FakeStdin(channel)
        return self.last_stdin, FakeStream(self._stdout, channel), FakeStream(self._stderr, channel)

    def get_transport(self):
        channel = FakeChannel(
            self._stream_chunks,
            exit_code=self._stream_exit_code,
            always_timeout=self._stream_timeout,
        )
        self.stream_channel = channel
        return FakeTransport(channel, active=self._active)

    def close(self) -> None:
        self.closed = True


def make_session(client: FakeClient) -> SSHSession:
    session = SSHSession("192.168.1.107", 22, "booster", password="x")
    session._client = client
    return session


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------
def test_exec_command_returns_decoded_output() -> None:
    session = make_session(FakeClient(stdout="输出\n".encode(), stderr="警告\n".encode(), exit_code=2))
    result = session.exec_command("echo hi")
    assert result.exit_code == 2
    assert result.stdout == "输出\n"
    assert result.stderr == "警告\n"
    assert "输出" in result.output and "警告" in result.output
    assert not result.ok
    assert session.connected


def test_exec_command_requires_connection() -> None:
    session = SSHSession("192.168.1.107", 22, "booster", password="x")
    with pytest.raises(SessionNotConnectedError):
        session.exec_command("ls")


def test_inactive_transport_is_detected() -> None:
    session = make_session(FakeClient(active=False))
    assert not session.connected
    with pytest.raises(SessionNotConnectedError):
        session.exec_command("ls")


def test_exec_stream_emits_lines_in_order() -> None:
    client = FakeClient(stream_chunks=[b"build-line-1\n", b"build-line-2\n", b"tail"], stream_exit_code=0)
    session = make_session(client)
    lines: List[str] = []
    result = session.exec_stream("bash build.sh", lines.append)
    assert lines == ["build-line-1", "build-line-2", "tail"]
    assert result.exit_code == 0
    assert "build-line-1" in result.stdout
    assert client.stream_channel.combine_stderr is True
    assert client.stream_channel.closed is True


def test_exec_stream_reports_nonzero_exit_code() -> None:
    client = FakeClient(stream_chunks=[b"error!\n"], stream_exit_code=3)
    session = make_session(client)
    lines: List[str] = []
    result = session.exec_stream("bash build.sh", lines.append)
    assert result.exit_code == 3
    assert lines == ["error!"]


def test_exec_stream_timeout() -> None:
    client = FakeClient(stream_timeout=True)
    session = make_session(client)
    with pytest.raises(CommandTimeoutError):
        session.exec_stream("sleep 999", lambda _line: None, timeout=0.3)


def test_close_closes_client() -> None:
    client = FakeClient()
    session = make_session(client)
    session.close()
    assert client.closed
    assert not session.connected


def test_exec_command_with_input_writes_password_to_stdin() -> None:
    client = FakeClient(exit_code=0)
    session = make_session(client)
    result = session.exec_command_with_input("read p", "secret-password\nsecret-password\n")
    assert result.exit_code == 0
    assert client.last_stdin is not None
    assert client.last_stdin.written == b"secret-password\nsecret-password\n"
    assert client.last_channel is not None
    assert client.last_channel.write_shutdown is True
    # 密码不会出现在命令行参数里
    assert "secret-password" not in client.last_command
