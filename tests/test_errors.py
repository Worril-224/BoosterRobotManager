"""连接错误分级测试：只有认证失败才算“密码不正确”（R0 / R5）。"""

from __future__ import annotations

import errno
import socket

import paramiko

from brm.core.errors import (
    AuthFailedError,
    BrmError,
    CommandTimeoutError,
    ConnectTimeoutError,
    ConnectionRejectedError,
    DiskFullError,
    HostKeyError,
    NetworkUnreachableError,
    classify_connection_error,
    classify_remote_failure,
    looks_like_auth_failure,
)

HOST = "192.168.1.107"
PORT = 22


def test_authentication_exception_maps_to_auth_error() -> None:
    error = classify_connection_error(paramiko.AuthenticationException("auth failed"), HOST, PORT)
    assert isinstance(error, AuthFailedError)
    assert error.title == "密码不正确"


def test_bad_host_key_maps_to_host_key_error() -> None:
    exc = paramiko.BadHostKeyException(HOST, "got-key", "expected-key")
    error = classify_connection_error(exc, HOST, PORT)
    assert isinstance(error, HostKeyError)


def test_socket_timeout_maps_to_connect_timeout() -> None:
    error = classify_connection_error(socket.timeout("timed out"), HOST, PORT)
    assert isinstance(error, ConnectTimeoutError)
    assert not isinstance(error, AuthFailedError)


def test_connection_refused_maps_to_rejected() -> None:
    error = classify_connection_error(ConnectionRefusedError(61, "refused"), HOST, PORT)
    assert isinstance(error, ConnectionRejectedError)
    assert not isinstance(error, AuthFailedError)


def test_network_unreachable_errno() -> None:
    exc = OSError(errno.ENETUNREACH, "Network is unreachable")
    error = classify_connection_error(exc, HOST, PORT)
    assert isinstance(error, NetworkUnreachableError)
    assert not isinstance(error, AuthFailedError)


def test_etimedout_errno() -> None:
    exc = OSError(errno.ETIMEDOUT, "Operation timed out")
    error = classify_connection_error(exc, HOST, PORT)
    assert isinstance(error, ConnectTimeoutError)


def test_gaierror_maps_to_unreachable() -> None:
    error = classify_connection_error(socket.gaierror(-2, "Name or service not known"), "bad.host", PORT)
    assert isinstance(error, NetworkUnreachableError)


def test_banner_ssh_exception_is_not_auth_error() -> None:
    exc = paramiko.SSHException("Error reading SSH protocol banner")
    error = classify_connection_error(exc, HOST, PORT)
    assert isinstance(error, NetworkUnreachableError)
    assert not isinstance(error, AuthFailedError)


def test_auth_message_inside_ssh_exception() -> None:
    exc = paramiko.SSHException("Authentication failed.")
    error = classify_connection_error(exc, HOST, PORT)
    assert isinstance(error, AuthFailedError)


def test_no_valid_connections_maps_to_rejected() -> None:
    exc = paramiko.ssh_exception.NoValidConnectionsError(
        {("192.168.1.107", 22): ConnectionRefusedError(61, "Connection refused")}
    )
    error = classify_connection_error(exc, HOST, PORT)
    assert isinstance(error, ConnectionRejectedError)


def test_unknown_exception_is_generic_error() -> None:
    error = classify_connection_error(RuntimeError("boom"), HOST, PORT)
    assert isinstance(error, BrmError)
    assert not isinstance(error, AuthFailedError)
    assert "boom" in error.detail


def test_remote_failure_classification() -> None:
    assert isinstance(classify_remote_failure("No space left on device"), DiskFullError)
    assert isinstance(classify_remote_failure("unzip: command not found"), BrmError)
    assert classify_remote_failure("normal output") is None


def test_looks_like_auth_failure() -> None:
    assert looks_like_auth_failure("Permission denied (publickey,password).")
    assert not looks_like_auth_failure("Connection timed out")


def test_command_timeout_is_separate() -> None:
    error = CommandTimeoutError("命令执行超时")
    assert isinstance(error, BrmError)
    assert error.title == "命令执行超时"
