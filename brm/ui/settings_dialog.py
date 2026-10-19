"""全局设置对话框（R0）：默认密码、连接参数、测试连接。"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from .. import constants
from ..core.errors import AuthFailedError
from ..core.session_manager import SessionManager
from ..ui.workers import TestConnectionWorker


class SettingsDialog(QDialog):
    """⚙ 设置 / 密码设置。"""

    saved = Signal()

    def __init__(self, store, manager: SessionManager, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.manager = manager
        self._test_worker = None

        self.setWindowTitle("设置")
        self.setModal(True)
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(14)

        title = QLabel("设置")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(title)

        layout.addWidget(self._build_password_group())
        layout.addWidget(self._build_connection_group())
        layout.addWidget(self._build_robot_password_group())
        layout.addStretch(1)

        buttons = QDialogButtonBox()
        save_button = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        save_button.setObjectName("PrimaryButton")
        close_button = buttons.addButton("关闭", QDialogButtonBox.ButtonRole.RejectRole)
        close_button.setObjectName("FlatButton")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load_values()

    # ------------------------------------------------------------------
    def _build_password_group(self) -> QGroupBox:
        group = QGroupBox("默认密码（所有机器人共用，设置一次长期复用）")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        self._password_state_label = QLabel("")
        layout.addWidget(self._password_state_label)

        row = QHBoxLayout()
        self._password_edit = QLineEdit()
        self._password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._password_edit.setPlaceholderText(constants.PASSWORD_PLACEHOLDER)
        self._password_edit.setClearButtonEnabled(True)
        self._show_check = QCheckBox("显示密码")
        self._show_check.toggled.connect(self._on_toggle_show)
        row.addWidget(self._password_edit, 1)
        row.addWidget(self._show_check)
        layout.addLayout(row)

        note = QLabel(
            "密码以 Base64 混淆保存在本机 config/robots.json 中（非加密，仅适用于可信本地环境）。"
            "修改后立即生效，无需重启。"
        )
        note.setObjectName("MutedLabel")
        note.setWordWrap(True)
        layout.addWidget(note)

        test_row = QHBoxLayout()
        target_title = QLabel("测试对象：")
        target_title.setObjectName("MutedLabel")
        self._target_combo = QComboBox()
        self._test_button = QPushButton("测试连接")
        self._test_button.clicked.connect(self._on_test_connection)
        test_row.addWidget(target_title)
        test_row.addWidget(self._target_combo, 1)
        test_row.addWidget(self._test_button)
        layout.addLayout(test_row)

        self._test_result_label = QLabel("")
        self._test_result_label.setWordWrap(True)
        layout.addWidget(self._test_result_label)

        clear_row = QHBoxLayout()
        self._clear_overrides_button = QPushButton("清除所有单台密码覆盖（统一使用默认密码）")
        self._clear_overrides_button.setObjectName("FlatButton")
        self._clear_overrides_button.clicked.connect(self._on_clear_overrides)
        clear_row.addWidget(self._clear_overrides_button)
        clear_row.addStretch(1)
        layout.addLayout(clear_row)
        return group

    def _build_connection_group(self) -> QGroupBox:
        group = QGroupBox("连接与浏览参数")
        form = QFormLayout(group)
        form.setSpacing(8)

        self._connect_timeout_spin = QSpinBox()
        self._connect_timeout_spin.setRange(3, 120)
        self._connect_timeout_spin.setSuffix(" 秒")
        form.addRow("连接超时：", self._connect_timeout_spin)

        self._command_timeout_spin = QSpinBox()
        self._command_timeout_spin.setRange(30, 7200)
        self._command_timeout_spin.setSuffix(" 秒")
        form.addRow("命令执行超时：", self._command_timeout_spin)

        self._home_edit = QLineEdit()
        self._home_edit.setPlaceholderText(constants.DEFAULT_REMOTE_HOME)
        form.addRow("文件浏览根目录：", self._home_edit)

        return group

    def _build_robot_password_group(self) -> QGroupBox:
        group = QGroupBox("单台密码状态")
        layout = QVBoxLayout(group)
        self._robot_password_label = QLabel("")
        self._robot_password_label.setWordWrap(True)
        layout.addWidget(self._robot_password_label)
        return group

    # ------------------------------------------------------------------
    def _load_values(self) -> None:
        settings = self.store.settings
        self._password_edit.setText(settings.default_password())
        self._connect_timeout_spin.setValue(int(settings.connect_timeout))
        self._command_timeout_spin.setValue(int(settings.command_timeout))
        self._home_edit.setText(settings.remote_home_dir)

        self._target_combo.clear()
        for robot in self.store.robots:
            self._target_combo.addItem(f"{robot.name}（{robot.host}）", robot.name)

        self._refresh_state_labels()

    def _refresh_state_labels(self) -> None:
        if self.store.settings.has_default_password():
            self._password_state_label.setText("当前状态：已设置默认密码")
            self._password_state_label.setStyleSheet("color: #7ec699; font-weight: 600;")
        else:
            self._password_state_label.setText("当前状态：尚未设置默认密码")
            self._password_state_label.setStyleSheet("color: #ffb454; font-weight: 600;")

        lines = []
        for robot in self.store.robots:
            lines.append(f"· {robot.name}：{self.store.password_source(robot)}")
        self._robot_password_label.setText("\n".join(lines) if lines else "暂无机器人")

    # ------------------------------------------------------------------
    def _on_toggle_show(self, checked: bool) -> None:
        self._password_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )

    # ------------------------------------------------------------------
    def _on_save(self) -> None:
        password = self._password_edit.text()
        if not password:
            QMessageBox.warning(
                self,
                "默认密码为空",
                "默认密码不能为空。若该机器人使用私钥登录，请在其编辑对话框中选择「私钥文件登录」。",
            )
            self._password_edit.setFocus()
            return

        home = self._home_edit.text().strip() or constants.DEFAULT_REMOTE_HOME
        if not home.startswith("/"):
            QMessageBox.warning(self, "根目录无效", "文件浏览根目录必须是绝对路径，例如 /home/booster/。")
            return

        self.store.settings.set_default_password(password)
        self.store.settings.connect_timeout = int(self._connect_timeout_spin.value())
        self.store.settings.command_timeout = int(self._command_timeout_spin.value())
        self.store.settings.remote_home_dir = home.rstrip("/") + "/"
        self.store.save()

        self._refresh_state_labels()
        self.saved.emit()
        QMessageBox.information(self, "已保存", "设置已保存，密码修改立即生效（后续连接使用新密码）。")

    def _on_clear_overrides(self) -> None:
        overrides = [robot.name for robot in self.store.robots if robot.has_own_password()]
        if not overrides:
            QMessageBox.information(self, "无需清除", "当前没有任何单台密码覆盖。")
            return
        answer = QMessageBox.question(
            self,
            "确认清除",
            "将清除以下机器人的单台密码，改为统一使用默认密码：\n\n"
            + "、".join(overrides)
            + "\n\n是否继续？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.store.clear_all_robot_passwords()
        self._refresh_state_labels()
        self.saved.emit()

    # ------------------------------------------------------------------
    def _on_test_connection(self) -> None:
        if self._test_worker is not None and self._test_worker.isRunning():
            return
        name = self._target_combo.currentData()
        robot = self.store.get_robot(str(name)) if name else None
        if robot is None:
            QMessageBox.warning(self, "没有可测试的机器人", "请先在左侧新增一台机器人。")
            return
        if robot.auth_type == "key":
            QMessageBox.information(
                self,
                "私钥登录",
                f"{robot.name} 使用私钥登录，密码测试不适用。请直接连接验证。",
            )
            return

        password = self._password_edit.text()
        if not password:
            QMessageBox.warning(self, "密码为空", "请先输入要测试的密码。")
            self._password_edit.setFocus()
            return

        self._test_result_label.setText("正在测试连接…")
        self._test_result_label.setStyleSheet("color: #9aa3af;")
        self._test_button.setEnabled(False)

        worker = TestConnectionWorker(self.manager, robot, password, parent=self)
        self._test_worker = worker
        worker.log.connect(self._on_test_log)
        worker.succeeded.connect(self._on_test_success)
        worker.failed.connect(self._on_test_failed)
        worker.finished.connect(self._on_test_finished)
        worker.start()

    def _on_test_log(self, text: str) -> None:
        # 测试连接过程不打扰主界面，仅展示最终结果
        pass

    def _on_test_success(self, _result) -> None:
        self._test_result_label.setText("密码校验通过")
        self._test_result_label.setStyleSheet("color: #7ec699; font-weight: 600;")

    def _on_test_failed(self, error) -> None:
        if isinstance(error, AuthFailedError):
            self._test_result_label.setText(f"密码校验失败：{error.message}")
            self._test_result_label.setStyleSheet("color: #ff8a8a; font-weight: 600;")
            answer = QMessageBox.question(
                self,
                "密码不正确，是否重新输入？",
                "密码不正确，是否重新输入？\n\n输入后可直接再次点击「测试连接」验证。",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._password_edit.setFocus()
                self._password_edit.selectAll()
        else:
            self._test_result_label.setText(f"连接失败：{error.title} - {error.message}")
            self._test_result_label.setStyleSheet("color: #ff8a8a; font-weight: 600;")
            QMessageBox.warning(self, error.title, error.user_message())

    def _on_test_finished(self) -> None:
        self._test_button.setEnabled(True)
        worker, self._test_worker = self._test_worker, None
        if worker is not None:
            worker.deleteLater()

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        if self._test_worker is not None and self._test_worker.isRunning():
            QMessageBox.information(self, "请稍候", "正在测试连接，请等待测试结束后再关闭设置窗口。")
            event.ignore()
            return
        super().closeEvent(event)
