"""部署操作面板（R3 / R7 / R8）。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.proc_control import ProcStatus


class DeployPanel(QWidget):
    """右侧部署 / 服务控制面板。"""

    choose_zip_requested = Signal()
    deploy_requested = Signal(str)
    start_requested = Signal()
    stop_requested = Signal()
    status_requested = Signal()
    view_log_requested = Signal()
    browse_project_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._zip_path: Optional[Path] = None
        self._project_dir: str = ""
        self._connected = False
        self._busy = False
        self._status: Optional[ProcStatus] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        layout.addWidget(self._build_deploy_group())
        layout.addWidget(self._build_service_group())
        layout.addStretch(1)

    # ------------------------------------------------------------------
    def _build_deploy_group(self) -> QGroupBox:
        group = QGroupBox("部署（zip → 解压 → 编译 → 授权）")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        zip_row = QHBoxLayout()
        self._zip_edit = QLineEdit()
        self._zip_edit.setReadOnly(True)
        self._zip_edit.setPlaceholderText("尚未选择 zip 文件")
        self._choose_button = QPushButton("选择 zip…")
        self._choose_button.clicked.connect(self.choose_zip_requested.emit)
        zip_row.addWidget(self._zip_edit, 1)
        zip_row.addWidget(self._choose_button)
        layout.addLayout(zip_row)

        hint = QLabel("只接受 .zip；可直接把 zip 拖到窗口。上传前会自动清理远端同名文件 / 目录。")
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        progress_row = QHBoxLayout()
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress_label = QLabel("等待部署")
        self._progress_label.setObjectName("MutedLabel")
        progress_row.addWidget(self._progress, 1)
        progress_row.addWidget(self._progress_label)
        layout.addLayout(progress_row)

        self._deploy_button = QPushButton("② 上传并部署")
        self._deploy_button.setObjectName("PrimaryButton")
        self._deploy_button.clicked.connect(self._emit_deploy)
        layout.addWidget(self._deploy_button)

        project_row = QHBoxLayout()
        project_title = QLabel("项目目录：")
        project_title.setObjectName("MutedLabel")
        self._project_label = QLabel("尚未部署")
        self._project_label.setObjectName("ProjectPath")
        self._project_label.setWordWrap(True)
        self._project_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        project_row.addWidget(project_title)
        project_row.addWidget(self._project_label, 1)
        layout.addLayout(project_row)

        browse_row = QHBoxLayout()
        self._browse_project_button = QPushButton("选择 Workspace 里的已有项目…")
        self._browse_project_button.setObjectName("FlatButton")
        self._browse_project_button.clicked.connect(self.browse_project_requested.emit)
        browse_row.addWidget(self._browse_project_button)
        browse_row.addStretch(1)
        layout.addLayout(browse_row)

        return group

    def _build_service_group(self) -> QGroupBox:
        group = QGroupBox("服务控制（start.sh）")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        status_row = QHBoxLayout()
        self._dot_label = QLabel("●")
        self._dot_label.setStyleSheet("color: #6b7280; font-size: 16px;")
        self._status_label = QLabel("未连接")
        self._status_label.setObjectName("StatusStopped")
        self._refresh_button = QPushButton("刷新状态")
        self._refresh_button.setObjectName("FlatButton")
        self._refresh_button.clicked.connect(self.status_requested.emit)
        status_row.addWidget(self._dot_label)
        status_row.addWidget(self._status_label, 1)
        status_row.addWidget(self._refresh_button)
        layout.addLayout(status_row)

        button_row = QHBoxLayout()
        self._start_button = QPushButton("▶ 启动")
        self._start_button.setObjectName("PrimaryButton")
        self._start_button.clicked.connect(self.start_requested.emit)
        self._stop_button = QPushButton("■ 停止")
        self._stop_button.setObjectName("DangerButton")
        self._stop_button.clicked.connect(self.stop_requested.emit)
        self._log_button = QPushButton("查看启动日志")
        self._log_button.setObjectName("FlatButton")
        self._log_button.clicked.connect(self.view_log_requested.emit)
        button_row.addWidget(self._start_button)
        button_row.addWidget(self._stop_button)
        button_row.addWidget(self._log_button)
        layout.addLayout(button_row)

        return group

    # ------------------------------------------------------------------
    # 状态更新
    # ------------------------------------------------------------------
    def set_zip(self, path: Optional[Path]) -> None:
        self._zip_path = Path(path) if path else None
        self._zip_edit.setText(str(path) if path else "")
        self._progress.setValue(0)
        self._progress_label.setText("等待部署" if path else "等待部署")
        self._update_buttons()

    @property
    def zip_path(self) -> Optional[Path]:
        return self._zip_path

    def set_progress(self, percent: int, text: str = "") -> None:
        self._progress.setValue(max(0, min(100, int(percent))))
        self._progress_label.setText(text or f"{int(percent)}%")

    def set_stage_text(self, text: str) -> None:
        self._progress_label.setText(text)

    def set_project(self, project_dir: str, project_name: str = "") -> None:
        self._project_dir = project_dir or ""
        if project_dir:
            suffix = f"（项目名：{project_name}）" if project_name else ""
            self._project_label.setText(f"{project_dir}{suffix}")
        else:
            self._project_label.setText("尚未部署")
        self._status = None
        self._render_status()
        self._update_buttons()

    @property
    def project_dir(self) -> str:
        return self._project_dir

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        self._update_buttons()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_buttons()

    def set_status(self, status: Optional[ProcStatus]) -> None:
        self._status = status
        self._render_status()
        self._update_buttons()

    def set_status_error(self, message: str) -> None:
        self._status = None
        self._dot_label.setStyleSheet("color: #ff8a8a; font-size: 16px;")
        self._status_label.setText(message)
        self._status_label.setStyleSheet("color: #ff8a8a; font-weight: 600;")

    # ------------------------------------------------------------------
    def _render_status(self) -> None:
        if not self._connected:
            self._dot_label.setStyleSheet("color: #6b7280; font-size: 16px;")
            self._status_label.setText("未连接")
            self._status_label.setStyleSheet("color: #8b929c; font-weight: 600;")
            return
        if self._status is None:
            self._dot_label.setStyleSheet("color: #6b7280; font-size: 16px;")
            self._status_label.setText("未选择项目" if not self._project_dir else "状态未知")
            self._status_label.setStyleSheet("color: #8b929c; font-weight: 600;")
            return
        if self._status.running:
            self._dot_label.setStyleSheet("color: #7ec699; font-size: 16px;")
            self._status_label.setText(self._status.text)
            self._status_label.setStyleSheet("color: #7ec699; font-weight: 600;")
        else:
            self._dot_label.setStyleSheet("color: #6b7280; font-size: 16px;")
            self._status_label.setText(self._status.text)
            self._status_label.setStyleSheet("color: #8b929c; font-weight: 600;")

    def _update_buttons(self) -> None:
        ready = self._connected and not self._busy
        self._choose_button.setEnabled(not self._busy)
        self._deploy_button.setEnabled(bool(self._zip_path) and ready)
        self._browse_project_button.setEnabled(ready)
        has_project = bool(self._project_dir)
        running = bool(self._status is not None and self._status.running)
        self._start_button.setEnabled(ready and has_project and not running)
        self._stop_button.setEnabled(ready and has_project and running)
        self._refresh_button.setEnabled(ready and has_project)
        self._log_button.setEnabled(ready and has_project)
        self._zip_edit.setEnabled(not self._busy)
        self._progress.setEnabled(True)

    def _emit_deploy(self) -> None:
        if self._zip_path is not None:
            self.deploy_requested.emit(str(self._zip_path))
