"""远端文件浏览面板与文本预览对话框（R2）。"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.sftp_client import RemoteEntry


class TextPreviewDialog(QDialog):
    """文本文件预览（最多前 200 KB）。"""

    def __init__(self, parent, path: str, text: str, truncated: bool) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"预览 - {path}")
        self.resize(860, 640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        header = QLabel(path)
        header.setObjectName("ProjectPath")
        header.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(header)

        if truncated:
            note = QLabel("文件较大，仅显示前 200 KB 内容；如需完整内容请下载到本地查看。")
            note.setObjectName("MutedLabel")
            layout.addWidget(note)

        viewer = QPlainTextEdit()
        viewer.setReadOnly(True)
        viewer.setPlainText(text)
        viewer.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(11)
        viewer.setFont(mono)
        layout.addWidget(viewer, 1)

        close_button = QPushButton("关闭")
        close_button.setObjectName("FlatButton")
        close_button.clicked.connect(self.accept)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)


class FileBrowserPanel(QWidget):
    """远端目录浏览：地址栏、列表、预览 / 下载 / 删除。"""

    navigate_requested = Signal(str)
    preview_requested = Signal(str)
    download_requested = Signal(str)
    delete_requested = Signal(str, bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._entries: List[RemoteEntry] = []
        self._path = ""
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # -- 地址栏 ------------------------------------------------------
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        self._up_button = QPushButton("↑ 上一级")
        self._up_button.setObjectName("FlatButton")
        self._up_button.clicked.connect(self._go_up)

        self._refresh_button = QPushButton("刷新")
        self._refresh_button.setObjectName("FlatButton")
        self._refresh_button.clicked.connect(lambda: self.navigate_requested.emit(self._path))

        self._path_edit = QLineEdit()
        self._path_edit.setObjectName("PathEdit")
        self._path_edit.setPlaceholderText("/home/booster/")
        self._path_edit.returnPressed.connect(self._jump_to_edit)

        self._jump_button = QPushButton("跳转")
        self._jump_button.setObjectName("FlatButton")
        self._jump_button.clicked.connect(self._jump_to_edit)

        toolbar.addWidget(self._up_button)
        toolbar.addWidget(self._refresh_button)
        toolbar.addWidget(self._path_edit, 1)
        toolbar.addWidget(self._jump_button)
        layout.addLayout(toolbar)

        # -- 文件表格 ----------------------------------------------------
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["名称", "权限", "大小", "修改时间"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.doubleClicked.connect(self._on_double_click)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._table, 1)

        # -- 操作按钮 ----------------------------------------------------
        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self._preview_button = QPushButton("预览")
        self._preview_button.clicked.connect(self._emit_preview)
        self._download_button = QPushButton("下载到本地")
        self._download_button.clicked.connect(self._emit_download)
        self._delete_button = QPushButton("删除")
        self._delete_button.setObjectName("DangerButton")
        self._delete_button.clicked.connect(self._emit_delete)

        self._hint_label = QLabel("双击目录进入，双击文件预览")
        self._hint_label.setObjectName("MutedLabel")

        action_row.addWidget(self._preview_button)
        action_row.addWidget(self._download_button)
        action_row.addWidget(self._delete_button)
        action_row.addStretch(1)
        action_row.addWidget(self._hint_label)
        layout.addLayout(action_row)

        self._on_selection_changed()

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    @property
    def current_path(self) -> str:
        return self._path

    def current_entry(self) -> Optional[RemoteEntry]:
        row = self._table.currentRow()
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    def set_path(self, path: str) -> None:
        self._path = path
        self._path_edit.setText(path)
        self._up_button.setEnabled(bool(path) and path.rstrip("/") != "/")

    def set_entries(self, entries: List[RemoteEntry]) -> None:
        self._entries = list(entries)
        self._table.setRowCount(0)
        style = QApplication.style()
        dir_icon = style.standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        file_icon = style.standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        self._table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            name_item = QTableWidgetItem(entry.name)
            name_item.setIcon(dir_icon if entry.is_dir else file_icon)
            name_item.setData(Qt.ItemDataRole.UserRole, entry.path)
            self._table.setItem(row, 0, name_item)

            permission_item = QTableWidgetItem(entry.mode_text)
            permission_item.setForeground(Qt.GlobalColor.gray)
            self._table.setItem(row, 1, permission_item)

            size_item = QTableWidgetItem(entry.size_text)
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, 2, size_item)

            time_item = QTableWidgetItem(entry.mtime_text)
            self._table.setItem(row, 3, time_item)
        self._set_loading(False)
        self._on_selection_changed()

    def set_loading(self, loading: bool) -> None:
        self._set_loading(loading)

    def _set_loading(self, loading: bool) -> None:
        self._loading = loading
        self._hint_label.setText("加载中…" if loading else "双击目录进入，双击文件预览")

    def clear(self) -> None:
        self._entries = []
        self._table.setRowCount(0)
        self._path_edit.clear()
        self._on_selection_changed()

    def set_connected(self, connected: bool) -> None:
        for widget in (
            self._up_button,
            self._refresh_button,
            self._jump_button,
            self._path_edit,
            self._table,
        ):
            widget.setEnabled(connected)
        if not connected:
            self._preview_button.setEnabled(False)
            self._download_button.setEnabled(False)
            self._delete_button.setEnabled(False)

    # ------------------------------------------------------------------
    # 内部逻辑
    # ------------------------------------------------------------------
    def _on_selection_changed(self) -> None:
        has_selection = self.current_entry() is not None
        enabled = has_selection and not self._loading and self._table.isEnabled()
        self._preview_button.setEnabled(enabled)
        self._download_button.setEnabled(enabled)
        self._delete_button.setEnabled(enabled)
        entry = self.current_entry()
        if entry is not None:
            self._preview_button.setEnabled(enabled and not entry.is_dir)
            size_text = entry.size_text if not entry.is_dir else "目录"
            self._hint_label.setText(f"{entry.name} · {entry.mode_text} · {size_text}")
        else:
            self._hint_label.setText("双击目录进入，双击文件预览")

    def _go_up(self) -> None:
        path = self._path.rstrip("/")
        if not path or path == "/":
            return
        parent = path.rsplit("/", 1)[0] or "/"
        self.navigate_requested.emit(parent)

    def _jump_to_edit(self) -> None:
        path = self._path_edit.text().strip()
        if not path:
            return
        if not path.startswith("/"):
            path = "/" + path
        self.navigate_requested.emit(path)

    def _on_double_click(self, index) -> None:
        entry = self.current_entry()
        if entry is None:
            return
        if entry.is_dir:
            self.navigate_requested.emit(entry.path)
        else:
            self.preview_requested.emit(entry.path)

    def _emit_preview(self) -> None:
        entry = self.current_entry()
        if entry is not None and not entry.is_dir:
            self.preview_requested.emit(entry.path)

    def _emit_download(self) -> None:
        entry = self.current_entry()
        if entry is not None:
            self.download_requested.emit(entry.path)

    def _emit_delete(self) -> None:
        entry = self.current_entry()
        if entry is not None:
            self.delete_requested.emit(entry.path, entry.is_dir)
