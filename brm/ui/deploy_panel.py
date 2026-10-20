"""部署操作面板（R3 / R7 / R8）。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.proc_control import ProcStatus


class DeployPanel(QWidget):
    """右侧部署 / 服务控制面板。"""

    choose_zip_requested = Signal()
    choose_folder_requested = Signal()
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
        self._zipping = False
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
        self._choose_button = QPushButton("选择上传内容 ▾")
        self._choose_button.setToolTip("点击后主动选择：上传 zip 文件，或上传文件夹（自动打包）")
        self._choose_button.clicked.connect(self._show_choose_menu)
        zip_row.addWidget(self._zip_edit, 1)
        zip_row.addWidget(self._choose_button)
        layout.addLayout(zip_row)

        self._choose_menu = QMenu(self)
        zip_action = self._choose_menu.addAction("选择 zip 文件…")
        zip_action.triggered.connect(lambda _checked=False: self.choose_zip_requested.emit())
        folder_action = self._choose_menu.addAction("选择文件夹（自动打包为 zip）…")
        folder_action.triggered.connect(lambda _checked=False: self.choose_folder_requested.emit())

        hint = QLabel(
            "点击「选择上传内容」后可主动选择上传 zip 或文件夹（文件夹会先自动打包）。"
            "也支持把 zip / 文件夹直接拖到窗口。"
        )
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

        # 编译进度（上传完成后进入编译阶段时显示）
        self._compile_box = QWidget()
        compile_layout = QVBoxLayout(self._compile_box)
        compile_layout.setContentsMargins(0, 0, 0, 0)
        compile_layout.setSpacing(4)
        compile_header = QHBoxLayout()
        compile_title = QLabel("编译进度")
        compile_title.setObjectName("MutedLabel")
        self._compile_label = QLabel("等待编译")
        self._compile_label.setObjectName("MutedLabel")
        self._compile_label.setWordWrap(True)
        compile_header.addWidget(compile_title)
        compile_header.addStretch(1)
        compile_header.addWidget(self._compile_label, 1)
        self._compile_bar = QProgressBar()
        self._compile_bar.setRange(0, 100)
        self._compile_bar.setValue(0)
        self._compile_bar.setTextVisible(False)
        compile_layout.addLayout(compile_header)
        compile_layout.addWidget(self._compile_bar)
        self._compile_box.setVisible(False)
        layout.addWidget(self._compile_box)

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

    def reset_compile(self) -> None:
        self._compile_box.setVisible(False)
        self._compile_bar.setRange(0, 100)
        self._compile_bar.setValue(0)
        self._compile_label.setText("等待编译")
        self._compile_label.setStyleSheet("color: #8b929c;")

    def set_compile_progress(self, percent: int, text: str) -> None:
        """更新编译进度；percent < 0 时显示不确定进度条。"""
        self._compile_box.setVisible(True)
        if percent < 0:
            self._compile_bar.setRange(0, 0)
        else:
            self._compile_bar.setRange(0, 100)
            self._compile_bar.setValue(max(0, min(100, int(percent))))
        self._compile_label.setText(text or "编译中…")
        failed = any(word in (text or "") for word in ("失败", "中断", "取消", "错误"))
        self._compile_label.setStyleSheet(
            "color: #ff8a8a;" if failed else "color: #8b929c;"
        )

    def set_stage_text(self, text: str) -> None:
        self._progress_label.setText(text)

    def set_project(self, project_dir: str, project_name: str = "") -> None:
        self._project_dir = project_dir or ""
        if project_dir:
            suffix = f"（项目名：{project_name}）" if project_name else ""
            self._project_label.setText(f"{project_dir}{suffix}")
        else:
            self._project_label.setText("尚未部署")
        self.reset_compile()
        self._status = None
        self._render_status()
        self._update_buttons()

    @property
    def project_dir(self) -> str:
        return self._project_dir

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        self._update_buttons()

    def _show_choose_menu(self) -> None:
        """在按钮下方弹出「zip / 文件夹」选择菜单。"""
        position = self._choose_button.mapToGlobal(
            QPoint(0, self._choose_button.height())
        )
        self._choose_menu.popup(position)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_buttons()

    def set_zipping(self, zipping: bool) -> None:
        """本地压缩文件夹期间禁用相关按钮。"""
        self._zipping = bool(zipping)
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
        idle = not self._busy and not self._zipping
        ready = self._connected and idle
        self._choose_button.setEnabled(idle)
        self._deploy_button.setEnabled(bool(self._zip_path) and ready)
        self._browse_project_button.setEnabled(ready)
        has_project = bool(self._project_dir)
        running = bool(self._status is not None and self._status.running)
        self._start_button.setEnabled(ready and has_project and not running)
        self._stop_button.setEnabled(ready and has_project and running)
        self._refresh_button.setEnabled(ready and has_project)
        self._log_button.setEnabled(ready and has_project)
        self._zip_edit.setEnabled(idle)
        self._progress.setEnabled(True)

    def _emit_deploy(self) -> None:
        if self._zip_path is not None:
            self.deploy_requested.emit(str(self._zip_path))
