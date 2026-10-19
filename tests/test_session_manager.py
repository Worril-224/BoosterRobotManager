"""会话管理器的密码复用 / 重试语义测试（R0）。

通过替换 ``SSHSession`` 为假实现，验证：
* 连接默认使用已保存的密码，不需要每次询问；
* 密码错误时抛出 AuthFailedError（界面据此弹“重新输入”）；
* 调用方传入候选密码重试时可成功，且之后从配置中读取到新密码。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brm.core import session_manager as session_manager_module
from brm.core.errors import AuthFailedError, BrmError
from brm.core.robots import RobotStore
from brm.core.session_manager import SessionManager


class FakeSSHSession:
    """记录密码的假 SSH 会话。"""

    instances = []

    def __init__(
        self,
        host: str,
        port: int = 22,
        username: str = "booster",
        *,
        password=None,
        key_path=None,
        connect_timeout: int = 10,
        command_timeout: int = 600,
        allow_agent: bool = False,
        look_for_keys: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.key_path = key_path
        self.command_timeout = command_timeout
        self.connected = False
        FakeSSHSession.instances.append(self)

    def connect(self) -> None:
        if self.password != "right-password":
            raise AuthFailedError("机器人拒绝了账号 / 密码认证。")
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def open_sftp(self):  # pragma: no cover - 本测试不需要
        raise NotImplementedError


@pytest.fixture(autouse=True)
def patch_session(monkeypatch):
    FakeSSHSession.instances = []
    monkeypatch.setattr(session_manager_module, "SSHSession", FakeSSHSession)
    yield


def make_manager(tmp_path: Path) -> tuple[RobotStore, SessionManager]:
    store = RobotStore(tmp_path / "robots.json")
    store.load()
    return store, SessionManager(store)


def test_no_password_raises_readable_error(tmp_path: Path) -> None:
    store, manager = make_manager(tmp_path)
    with pytest.raises(BrmError) as excinfo:
        manager.connect("107")
    assert "尚未设置机器人密码" in excinfo.value.message


def test_uses_saved_password_without_asking(tmp_path: Path) -> None:
    store, manager = make_manager(tmp_path)
    store.set_default_password("right-password")
    robot = manager.connect("107")
    assert manager.connected
    assert manager.robot_name == "107"
    assert FakeSSHSession.instances[-1].password == "right-password"
    assert robot.host == "192.168.1.107"


def test_wrong_password_raises_auth_error(tmp_path: Path) -> None:
    store, manager = make_manager(tmp_path)
    store.set_default_password("wrong-password")
    with pytest.raises(AuthFailedError):
        manager.connect("107")
    assert not manager.connected


def test_retry_with_override_then_save(tmp_path: Path) -> None:
    store, manager = make_manager(tmp_path)
    store.set_default_password("wrong-password")
    with pytest.raises(AuthFailedError):
        manager.connect("107")

    # 界面层行为：捕获认证失败后弹框输入新密码，再用 override 重试
    manager.connect("107", password_override="right-password")
    assert manager.connected

    # 重试成功后保存为该机器人的密码，后续连接继续复用
    store.set_robot_password("107", "right-password")
    manager.disconnect()
    manager.connect("107")
    assert manager.connected
    assert FakeSSHSession.instances[-1].password == "right-password"


def test_disconnect_clears_session(tmp_path: Path) -> None:
    store, manager = make_manager(tmp_path)
    store.set_default_password("right-password")
    manager.connect("107")
    assert manager.connected
    manager.disconnect()
    assert not manager.connected
    assert manager.robot_name is None
