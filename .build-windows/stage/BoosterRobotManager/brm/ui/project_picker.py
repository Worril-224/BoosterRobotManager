"""从 Workspace 中选择已有项目的对话框。"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from ..core.sftp_client import RemoteEntry


class ProjectPickerDialog(QDialog):
    """列出 Workspace 下的目录，供“启动 / 停止”选择已有工程。"""

    def __init__(self, parent, workspace: str, entries: List[RemoteEntry]) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择已有项目")
        self.setModal(True)
        self.resize(560, 440)
        self._selected: Optional[RemoteEntry] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        title = QLabel(f"Workspace：{workspace}")
        title.setObjectName("ProjectPath")
        title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(title)

        hint = QLabel("选择一个项目目录（应包含 scripts/start.sh 或唯一的 start.sh）。")
        hint.setObjectName("MutedLabel")
        layout.addWidget(hint)

        self._list = QListWidget()
        for entry in entries:
            if not entry.is_dir:
                continue
            item = QListWidgetItem(entry.name)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self._list.addItem(item)
        self._list.itemDoubleClicked.connect(lambda _item: self._on_accept())
        layout.addWidget(self._list, 1)

        buttons = QDialogButtonBox()
        ok_button = buttons.addButton("确定", QDialogButtonBox.ButtonRole.AcceptRole)
        ok_button.setObjectName("PrimaryButton")
        cancel_button = buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        cancel_button.setObjectName("FlatButton")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def _on_accept(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        self._selected = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def selected_entry(self) -> Optional[RemoteEntry]:
        return self._selected
