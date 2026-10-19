"""密码设置 / 重新输入对话框（R0）。"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from .. import constants


class PasswordDialog(QDialog):
    """统一的密码输入框：密文显示 + 显示/隐藏切换 + 占位说明。"""

    def __init__(
        self,
        parent=None,
        *,
        title: str = "设置机器人密码",
        message: str = "",
        note: str = "",
        initial: str = "",
        ok_text: str = "保存",
        placeholder: str = constants.PASSWORD_PLACEHOLDER,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        if message:
            message_label = QLabel(message)
            message_label.setWordWrap(True)
            message_label.setStyleSheet("font-size: 14px; font-weight: 600;")
            layout.addWidget(message_label)

        self._edit = QLineEdit()
        self._edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._edit.setPlaceholderText(placeholder)
        self._edit.setText(initial)
        self._edit.setClearButtonEnabled(True)
        layout.addWidget(self._edit)

        show_row = QHBoxLayout()
        self._show_check = QCheckBox("显示密码")
        self._show_check.toggled.connect(self._on_toggle_show)
        show_row.addWidget(self._show_check)
        show_row.addStretch(1)
        layout.addLayout(show_row)

        if note:
            note_label = QLabel(note)
            note_label.setObjectName("MutedLabel")
            note_label.setWordWrap(True)
            layout.addWidget(note_label)

        layout.addSpacing(4)
        buttons = QDialogButtonBox()
        self._ok_button = buttons.addButton(ok_text, QDialogButtonBox.ButtonRole.AcceptRole)
        self._ok_button.setObjectName("PrimaryButton")
        cancel_button = buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        cancel_button.setObjectName("FlatButton")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._edit.returnPressed.connect(self._on_accept)
        self._edit.setFocus(Qt.FocusReason.OtherFocusReason)

    # ------------------------------------------------------------------
    def _on_toggle_show(self, checked: bool) -> None:
        self._edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )

    def _on_accept(self) -> None:
        if not self._edit.text():
            QMessageBox.warning(self, "密码不能为空", "请输入密码后再保存。")
            self._edit.setFocus()
            return
        self.accept()

    # ------------------------------------------------------------------
    def password(self) -> str:
        return self._edit.text()

    def set_password(self, text: str) -> None:
        self._edit.setText(text)
        self._edit.setFocus()
        self._edit.selectAll()

    @classmethod
    def ask(
        cls,
        parent,
        *,
        title: str,
        message: str,
        note: str = "",
        initial: str = "",
        ok_text: str = "保存",
    ) -> Optional[str]:
        """便捷方法：返回输入的密码，取消时返回 ``None``。"""
        dialog = cls(
            parent,
            title=title,
            message=message,
            note=note,
            initial=initial,
            ok_text=ok_text,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.password()
        return None
