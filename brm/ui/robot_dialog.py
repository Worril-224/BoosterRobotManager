"""机器人新增 / 编辑对话框（R1）。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from .. import constants
from ..core.robots import Robot


class RobotDialog(QDialog):
    """新增 / 编辑机器人。

    密码框规则（R0）：
    * 留空 = 使用全局默认密码（推荐）；
    * 单独填写 = 仅该台机器人覆盖；
    * 一键「清空密码、改用默认密码」。
    """

    def __init__(self, parent=None, robot: Optional[Robot] = None) -> None:
        super().__init__(parent)
        self._original = robot
        self._editing = robot is not None
        self.setWindowTitle("编辑机器人" if self._editing else "新增机器人")
        self.setModal(True)
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        title = QLabel("编辑机器人" if self._editing else "新增机器人")
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(title)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(10)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("例如 107（仅用于界面显示）")
        form.addRow("名称：", self._name_edit)

        self._host_edit = QLineEdit()
        self._host_edit.setPlaceholderText("例如 192.168.1.107")
        form.addRow("IP / 主机：", self._host_edit)

        self._port_spin = QSpinBox()
        self._port_spin.setRange(1, 65535)
        self._port_spin.setValue(constants.DEFAULT_PORT)
        form.addRow("SSH 端口：", self._port_spin)

        self._user_edit = QLineEdit()
        self._user_edit.setPlaceholderText(constants.DEFAULT_USERNAME)
        form.addRow("用户名：", self._user_edit)

        self._auth_combo = QComboBox()
        self._auth_combo.addItem("密码登录", "password")
        self._auth_combo.addItem("私钥文件登录", "key")
        self._auth_combo.currentIndexChanged.connect(self._on_auth_changed)
        form.addRow("认证方式：", self._auth_combo)

        password_row = QHBoxLayout()
        self._password_edit = QLineEdit()
        self._password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._password_edit.setPlaceholderText("留空 = 使用默认密码（推荐）")
        self._password_edit.setClearButtonEnabled(True)
        self._show_check = QCheckBox("显示")
        self._show_check.toggled.connect(self._on_toggle_show)
        password_row.addWidget(self._password_edit, 1)
        password_row.addWidget(self._show_check)
        form.addRow("密码：", password_row)

        self._password_note = QLabel("")
        self._password_note.setObjectName("MutedLabel")
        self._password_note.setWordWrap(True)
        form.addRow("", self._password_note)

        key_row = QHBoxLayout()
        self._key_edit = QLineEdit()
        self._key_edit.setPlaceholderText("例如 ~/.ssh/id_ed25519")
        browse_button = QPushButton("浏览…")
        browse_button.setObjectName("FlatButton")
        browse_button.clicked.connect(self._on_browse_key)
        key_row.addWidget(self._key_edit, 1)
        key_row.addWidget(browse_button)
        form.addRow("私钥文件：", key_row)

        self._workspace_edit = QLineEdit()
        self._workspace_edit.setPlaceholderText(constants.DEFAULT_REMOTE_WORKSPACE)
        form.addRow("上传目录：", self._workspace_edit)

        layout.addLayout(form)

        hint = QLabel(
            "说明：密码留空时自动使用「⚙ 密码设置」中的默认密码；"
            "单独填写则只覆盖这一台机器人。"
        )
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        clear_row = QHBoxLayout()
        self._clear_button = QPushButton("清空密码、改用默认密码")
        self._clear_button.setObjectName("FlatButton")
        self._clear_button.clicked.connect(self._on_clear_password)
        clear_row.addWidget(self._clear_button)
        clear_row.addStretch(1)
        layout.addLayout(clear_row)

        buttons = QDialogButtonBox()
        ok_button = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        ok_button.setObjectName("PrimaryButton")
        cancel_button = buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        cancel_button.setObjectName("FlatButton")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if robot is not None:
            self._load(robot)
        else:
            self._user_edit.setText(constants.DEFAULT_USERNAME)
            self._workspace_edit.setText(constants.DEFAULT_REMOTE_WORKSPACE)
        self._on_auth_changed()
        self._refresh_password_note()

    # ------------------------------------------------------------------
    def _load(self, robot: Robot) -> None:
        self._name_edit.setText(robot.name)
        self._host_edit.setText(robot.host)
        self._port_spin.setValue(robot.port)
        self._user_edit.setText(robot.username)
        index = self._auth_combo.findData(robot.auth_type)
        self._auth_combo.setCurrentIndex(index if index >= 0 else 0)
        if robot.has_own_password():
            self._password_edit.setText(robot.own_password())
        self._key_edit.setText(robot.key_path)
        self._workspace_edit.setText(robot.workspace_dir or constants.DEFAULT_REMOTE_WORKSPACE)
        self._clear_button.setVisible(robot.has_own_password())

    # ------------------------------------------------------------------
    def _on_auth_changed(self) -> None:
        is_key = self._auth_combo.currentData() == "key"
        self._password_edit.setEnabled(not is_key)
        self._show_check.setEnabled(not is_key)
        self._clear_button.setEnabled(not is_key)
        self._key_edit.setEnabled(is_key)
        self._refresh_password_note()

    def _on_toggle_show(self, checked: bool) -> None:
        self._password_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )

    def _on_browse_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择私钥文件", str(Path.home() / ".ssh")
        )
        if path:
            self._key_edit.setText(path)

    def _on_clear_password(self) -> None:
        self._password_edit.clear()
        self._password_note.setText("已清空单台密码，将使用默认密码。")
        self._clear_button.setVisible(False)

    def _refresh_password_note(self) -> None:
        if self._auth_combo.currentData() == "key":
            self._password_note.setText("当前使用私钥登录，密码设置不生效。")
            return
        if self._password_edit.text():
            self._password_note.setText("当前：单独设置密码（仅该台机器人生效）。")
        else:
            self._password_note.setText("当前：使用全局默认密码。")

    # ------------------------------------------------------------------
    def _on_accept(self) -> None:
        name = self._name_edit.text().strip()
        host = self._host_edit.text().strip()
        workspace = self._workspace_edit.text().strip() or constants.DEFAULT_REMOTE_WORKSPACE

        if not name:
            QMessageBox.warning(self, "名称不能为空", "请填写机器人名称，例如 107。")
            self._name_edit.setFocus()
            return
        if not host:
            QMessageBox.warning(self, "IP 不能为空", "请填写机器人 IP 或主机名。")
            self._host_edit.setFocus()
            return
        if self._auth_combo.currentData() == "key" and not self._key_edit.text().strip():
            QMessageBox.warning(self, "私钥不能为空", "私钥登录方式需要选择私钥文件。")
            self._key_edit.setFocus()
            return
        if not workspace.startswith("/"):
            QMessageBox.warning(self, "上传目录无效", "上传目录必须是绝对路径，例如 /home/booster/Workspace/。")
            self._workspace_edit.setFocus()
            return
        self.accept()

    # ------------------------------------------------------------------
    def build_robot(self) -> Robot:
        """根据界面内容构造 Robot（密码按“留空 = 用默认”的规则处理）。"""
        auth_type = str(self._auth_combo.currentData())
        robot = Robot(
            name=self._name_edit.text().strip(),
            host=self._host_edit.text().strip(),
            port=int(self._port_spin.value()),
            username=self._user_edit.text().strip() or constants.DEFAULT_USERNAME,
            auth_type=auth_type,
            password_b64="",
            key_path=self._key_edit.text().strip(),
            workspace_dir=self._workspace_edit.text().strip() or constants.DEFAULT_REMOTE_WORKSPACE,
        )
        password = self._password_edit.text()
        if auth_type == "password" and password:
            robot.set_own_password(password)
        return robot

    def original_name(self) -> Optional[str]:
        return self._original.name if self._original is not None else None
