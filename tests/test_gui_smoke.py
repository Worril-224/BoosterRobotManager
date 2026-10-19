"""GUI 冒烟测试（offscreen）：首次提示、密码状态、连接后的文件浏览、部署与服务控制。

通过把 ``SessionManager`` 的会话替换为本地 FakeRemote，可以在没有真实机器人
的情况下验证界面与核心流程的联动。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from brm.core.robots import RobotStore
from brm.core.sftp_client import SFTPClient
from brm.ui.main_window import MainWindow
from tests.fake_remote import FakeRemote
from tests.helpers import build_demo_project_zip


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def wait_until(predicate, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    QApplication.processEvents()
    return bool(predicate())


def make_window(qapp, tmp_path: Path):
    store = RobotStore(tmp_path / "config" / "robots.json")
    store.load()
    window = MainWindow(store)
    window.show()
    qapp.processEvents()
    return store, window


def attach_fake_remote(window: MainWindow, remote: FakeRemote) -> None:
    """把窗口的会话替换成本地 FakeRemote（模拟一台已连接机器人）。"""
    window.manager._session = remote
    window.manager._sftp = SFTPClient(remote)
    window.manager._robot_name = "107"


def test_first_run_shows_password_hint(qapp, tmp_path: Path) -> None:
    store, window = make_window(qapp, tmp_path)
    try:
        assert window._robot_list.count() == 1
        assert window._password_hint.isVisible()
        assert "尚未设置机器人密码" in window._password_hint.text()
        assert "未设置" in window._password_state_label.text()
        assert not window.deploy_panel._deploy_button.isEnabled()
    finally:
        window.close()


def test_password_saved_hides_hint(qapp, tmp_path: Path) -> None:
    store, window = make_window(qapp, tmp_path)
    try:
        store.set_default_password("demo-password")
        window._refresh_password_state()
        qapp.processEvents()
        assert not window._password_hint.isVisible()
        assert window._password_state_label.text() == "默认密码：已设置"
    finally:
        window.close()


def test_connect_and_browse_remote_files(qapp, tmp_path: Path) -> None:
    store, window = make_window(qapp, tmp_path)
    remote = FakeRemote(tmp_path / "remote")
    remote.connected = True
    remote.write_text("/home/booster/example.txt", "hello booster")
    attach_fake_remote(window, remote)
    try:
        window._on_connect_success("107", False, None)
        assert "已连接" in window._conn_label.text()
        assert wait_until(lambda: window.file_panel._table.rowCount() > 0, 5)
        names = [
            window.file_panel._table.item(row, 0).text()
            for row in range(window.file_panel._table.rowCount())
        ]
        assert "Workspace" in names
        assert "example.txt" in names
        assert window.file_panel.current_path == "/home/booster/"
    finally:
        window.manager._session = None
        window.manager._sftp = None
        window.close()


def test_gui_deploy_and_service_control(qapp, tmp_path: Path) -> None:
    store, window = make_window(qapp, tmp_path)
    remote = FakeRemote(tmp_path / "remote")
    remote.connected = True
    attach_fake_remote(window, remote)
    archive = build_demo_project_zip(tmp_path / "demo.zip")
    try:
        window._on_connect_success("107", False, None)
        window._set_zip(archive)
        assert window.deploy_panel.zip_path == archive

        window._start_deploy(archive)
        assert wait_until(lambda: not window._busy, 30), "部署超时"
        assert window._project is not None
        assert window._project.project_dir == "/home/booster/Workspace/demo"
        assert wait_until(lambda: window._proc_worker is None, 10)

        window._proc_action("start")
        assert wait_until(
            lambda: window._status is not None and window._status.running, 20
        ), "启动超时"
        assert wait_until(lambda: window._proc_worker is None, 10)
        assert remote.exists("/home/booster/Workspace/demo.start.pid")

        window._proc_action("stop")
        assert wait_until(
            lambda: window._status is not None and not window._status.running, 20
        ), "停止超时"
        assert wait_until(lambda: window._proc_worker is None, 10)
        assert not remote.exists("/home/booster/Workspace/demo.start.pid")
    finally:
        try:
            from brm.core.proc_control import ProcControl

            if remote.connected and window._project is not None:
                ProcControl(remote, "/home/booster/Workspace/").stop(
                    "demo", window._project.project_dir, wait_seconds=3
                )
        except Exception:
            pass
        wait_until(lambda: window._proc_worker is None, 10)
        window.manager._session = None
        window.manager._sftp = None
        window.close()


def test_file_browser_entries_sorted(qapp, tmp_path: Path) -> None:
    store, window = make_window(qapp, tmp_path)
    remote = FakeRemote(tmp_path / "remote")
    remote.connected = True
    remote.write_text("/home/booster/z.txt", "1")
    remote.write_text("/home/booster/a.txt", "2")
    remote.write_text("/home/booster/Workspace/inner.txt", "3")
    attach_fake_remote(window, remote)
    try:
        window._on_connect_success("107", False, None)
        assert wait_until(lambda: window.file_panel._table.rowCount() == 3, 5)
        first = window.file_panel._table.item(0, 0).text()
        assert first == "Workspace"  # 目录排在文件前面
    finally:
        window.manager._session = None
        window.manager._sftp = None
        window.close()

# ---------------------------------------------------------------------------
# 设置 / 密码设置对话框（R0）
# ---------------------------------------------------------------------------
def _patch_fake_ssh_session(monkeypatch, accept_password: str) -> None:
    """让 SessionManager 用假会话验证“测试连接”逻辑。"""
    from brm.core import session_manager as session_manager_module
    from brm.core.errors import AuthFailedError

    class FakeSSH:
        def __init__(self, host, port=22, username="booster", *, password=None, key_path=None, connect_timeout=10, command_timeout=600, allow_agent=False, look_for_keys=False):
            self.password = password

        def connect(self):
            if self.password != accept_password:
                raise AuthFailedError("机器人拒绝了账号 / 密码认证。")

        def close(self):
            pass

        def open_sftp(self):
            raise NotImplementedError

    monkeypatch.setattr(session_manager_module, "SSHSession", FakeSSH)


def test_settings_dialog_saves_password_and_tests_connection(qapp, tmp_path: Path, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    from brm.ui.settings_dialog import SettingsDialog

    store, window = make_window(qapp, tmp_path)
    _patch_fake_ssh_session(monkeypatch, "right-password")
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    dialog = SettingsDialog(store, window.manager, window)
    try:
        assert dialog._password_edit.text() == ""
        dialog._password_edit.setText("right-password")
        dialog._on_test_connection()
        assert wait_until(
            lambda: "密码校验通过" in dialog._test_result_label.text(), 10
        ), dialog._test_result_label.text()

        # 保存后立即写入配置（无需重启）
        dialog._on_save()
        assert store.settings.default_password() == "right-password"
        assert store.settings.has_default_password()
        assert "已设置默认密码" in dialog._password_state_label.text()
    finally:
        wait_until(lambda: dialog._test_worker is None, 10)
        dialog.close()
        window.close()


def test_settings_dialog_reports_wrong_password(qapp, tmp_path: Path, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    from brm.ui.settings_dialog import SettingsDialog

    store, window = make_window(qapp, tmp_path)
    _patch_fake_ssh_session(monkeypatch, "right-password")
    # 认证失败时只弹出“是否重新输入”的确认框，这里自动选“否”，避免阻塞测试
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No
    )
    dialog = SettingsDialog(store, window.manager, window)
    try:
        dialog._password_edit.setText("wrong-password")
        dialog._on_test_connection()
        assert wait_until(
            lambda: "密码校验失败" in dialog._test_result_label.text(), 10
        ), dialog._test_result_label.text()
        # 测试失败不会保存密码
        assert not store.settings.has_default_password()
    finally:
        wait_until(lambda: dialog._test_worker is None, 10)
        dialog.close()
        window.close()


def test_settings_dialog_rejects_empty_password(qapp, tmp_path: Path, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    from brm.ui.settings_dialog import SettingsDialog

    store, window = make_window(qapp, tmp_path)
    calls = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: calls.append(a) or QMessageBox.StandardButton.Ok
    )
    dialog = SettingsDialog(store, window.manager, window)
    try:
        dialog._password_edit.setText("")
        dialog._on_save()
        assert calls, "空密码应弹出警告"
        assert not store.settings.has_default_password()
    finally:
        dialog.close()
        window.close()

# ---------------------------------------------------------------------------
# 机器人编辑 / 项目选择 / 日志查看等对话框逻辑
# ---------------------------------------------------------------------------
def test_robot_dialog_password_rules(qapp, tmp_path: Path) -> None:
    from brm.ui.robot_dialog import RobotDialog

    dialog = RobotDialog()
    try:
        dialog._name_edit.setText("120")
        dialog._host_edit.setText("192.168.1.120")
        # 密码留空 = 使用默认密码
        robot = dialog.build_robot()
        assert robot.password_b64 == ""
        # 单独填写 = 仅该台覆盖
        dialog._password_edit.setText("only-this-robot")
        robot = dialog.build_robot()
        assert robot.own_password() == "only-this-robot"
        # 一键清空密码、改用默认密码
        dialog._on_clear_password()
        assert dialog.build_robot().password_b64 == ""
    finally:
        dialog.close()


def test_robot_dialog_key_auth(qapp) -> None:
    from brm.ui.robot_dialog import RobotDialog

    dialog = RobotDialog()
    try:
        dialog._name_edit.setText("121")
        dialog._host_edit.setText("192.168.1.121")
        dialog._auth_combo.setCurrentIndex(dialog._auth_combo.findData("key"))
        dialog._key_edit.setText("/tmp/id_ed25519")
        robot = dialog.build_robot()
        assert robot.auth_type == "key"
        assert robot.key_path.endswith("id_ed25519")
        assert robot.password_b64 == ""
    finally:
        dialog.close()


def test_robot_dialog_validation(qapp, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    from brm.ui.robot_dialog import RobotDialog

    calls = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: calls.append(a) or QMessageBox.StandardButton.Ok
    )
    dialog = RobotDialog()
    try:
        dialog._host_edit.setText("192.168.1.1")
        dialog._on_accept()  # 名称为空
        assert calls, "空名称应弹出警告"
        dialog._name_edit.setText("x")
        dialog._host_edit.clear()
        dialog._on_accept()  # IP 为空
        assert len(calls) >= 2
    finally:
        dialog.close()


def test_project_picker_dialog(qapp) -> None:
    from brm.core.sftp_client import RemoteEntry
    from brm.ui.project_picker import ProjectPickerDialog

    entries = [
        RemoteEntry("demo", "/home/booster/Workspace/demo", True),
        RemoteEntry("demo.zip", "/home/booster/Workspace/demo.zip", False),
    ]
    dialog = ProjectPickerDialog(None, "/home/booster/Workspace/", entries)
    try:
        assert dialog._list.count() == 1  # 只列出目录
        dialog._list.setCurrentRow(0)
        dialog._on_accept()
        assert dialog.selected_entry() is not None
        assert dialog.selected_entry().name == "demo"
    finally:
        dialog.close()


def test_service_log_dialog(qapp) -> None:
    from brm.ui.service_log_dialog import ServiceLogDialog

    dialog = ServiceLogDialog(None, "demo")
    try:
        dialog.set_text("demo service running")
        assert "demo service running" in dialog._view.toPlainText()
        dialog.set_error("读取失败")
        assert "读取失败" in dialog._view.toPlainText()
    finally:
        dialog.close()


def test_text_preview_dialog(qapp) -> None:
    from brm.ui.file_browser import TextPreviewDialog

    dialog = TextPreviewDialog(None, "/home/booster/a.txt", "hello booster", True)
    try:
        assert "预览" in dialog.windowTitle()
    finally:
        dialog.close()
