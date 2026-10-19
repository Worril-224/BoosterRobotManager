"""start.log 查看对话框（R8：末尾 200 行 + 刷新按钮）。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


class ServiceLogDialog(QDialog):
    refresh_requested = Signal()

    def __init__(self, parent, project_name: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"启动日志 - {project_name}")
        self.resize(820, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        header = QLabel(f"{project_name}.start.log（末尾 200 行）")
        header.setObjectName("ProjectPath")
        header.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(header)

        self._view = QPlainTextEdit()
        self._view.setObjectName("LogView")
        self._view.setReadOnly(True)
        self._view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(11)
        self._view.setFont(mono)
        layout.addWidget(self._view, 1)

        row = QHBoxLayout()
        self._hint = QLabel("")
        self._hint.setObjectName("MutedLabel")
        self._refresh_button = QPushButton("刷新")
        self._refresh_button.clicked.connect(self.refresh_requested.emit)
        close_button = QPushButton("关闭")
        close_button.setObjectName("FlatButton")
        close_button.clicked.connect(self.accept)
        row.addWidget(self._hint, 1)
        row.addWidget(self._refresh_button)
        row.addWidget(close_button)
        layout.addLayout(row)

    # ------------------------------------------------------------------
    def set_text(self, text: str) -> None:
        self._view.setPlainText(text or "（暂无启动日志）")
        scrollbar = self._view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def set_loading(self, loading: bool) -> None:
        self._refresh_button.setEnabled(not loading)
        self._hint.setText("正在刷新…" if loading else "")

    def set_error(self, message: str) -> None:
        self._view.setPlainText(message)
        self._hint.setText("")
        self._refresh_button.setEnabled(True)
