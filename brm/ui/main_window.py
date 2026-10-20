"""主窗口：机器人列表 + 文件浏览 + 部署 / 服务控制 + 运行日志。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional, Set

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QTextCharFormat
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME_CN, __version__, constants
from ..core.deployer import DeployResult
from ..core.local_zip import (
    LocalZipResult,
    cleanup_temp_archives,
    describe_result,
)
from ..core.errors import AuthFailedError, BrmError, DeployCancelledError
from ..core.proc_control import ProcStatus
from ..core.robots import Robot, RobotStore
from ..core.session_manager import SessionManager
from ..core.util import normalize_remote_dir, strip_trailing_slash
from ..core.zipguard import inspect_zip
from .deploy_panel import DeployPanel
from .file_browser import FileBrowserPanel, TextPreviewDialog
from .password_dialog import PasswordDialog
from .project_picker import ProjectPickerDialog
from .robot_dialog import RobotDialog
from .service_log_dialog import ServiceLogDialog
from .settings_dialog import SettingsDialog
from .theme import APP_QSS
from .workers import (
    BaseWorker,
    ConnectWorker,
    DeleteWorker,
    DeployWorker,
    DownloadWorker,
    FindScriptWorker,
    ListDirWorker,
    PreviewWorker,
    ProcWorker,
    ZipFolderWorker,
)


class RobotItemWidget(QWidget):
    """机器人列表项：状态圆点 + 名称 + IP / 密码来源。"""

    def __init__(self, robot: Robot, connected: bool, password_source: str, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 6, 4)
        layout.setSpacing(8)

        dot = QLabel("●")
        dot.setFixedWidth(14)
        dot.setStyleSheet(
            f"color: {'#7ec699' if connected else '#6b7280'}; font-size: 14px;"
        )

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)
        name_label = QLabel(robot.name)
        name_label.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {'#7ec699' if connected else '#e6eaf0'};"
        )
        detail_label = QLabel(f"{robot.host}:{robot.port} · {password_source}")
        detail_label.setStyleSheet("font-size: 11px; color: #8b929c;")
        text_layout.addWidget(name_label)
        text_layout.addWidget(detail_label)

        layout.addWidget(dot)
        layout.addLayout(text_layout, 1)


@dataclass
class ProjectContext:
    """当前操作的项目（来自部署结果或手动选择）。"""

    project_dir: str
    project_name: str
    start_script: str = ""


class MainWindow(QMainWindow):
    log_line = Signal(str)

    def __init__(self, store: RobotStore) -> None:
        super().__init__()
        self.store = store
        self.manager = SessionManager(store)

        self._workers: Set[BaseWorker] = set()
        self._connect_worker: Optional[BaseWorker] = None
        self._deploy_worker: Optional[BaseWorker] = None
        self._zip_worker: Optional[BaseWorker] = None
        self._proc_worker: Optional[BaseWorker] = None
        self._nav_worker: Optional[BaseWorker] = None
        self._tail_worker: Optional[BaseWorker] = None

        self._active_robot: Optional[str] = None
        self._project: Optional[ProjectContext] = None
        self._status: Optional[ProcStatus] = None
        self._busy = False
        self._proc_busy = False
        self._connecting = False
        self._nav_seq = 0
        self._pending_deploy: Optional[Path] = None
        self._tail_dialog: Optional[ServiceLogDialog] = None
        self._temp_zips: Set[Path] = set()
        self._closing = False

        self._build_ui()
        self.setStyleSheet(APP_QSS)
        self.log_line.connect(self._append_log)

        self._refresh_robot_list()
        self._refresh_password_state()
        self._apply_state()
        removed = cleanup_temp_archives(constants.TEMP_DIR, older_than_seconds=24 * 3600)
        if removed:
            self._append_log(f"已清理 {removed} 个过期临时打包文件。")
        self._append_log(
            "Booster 机器人管理器已启动。请先在左侧选择机器人，"
            "程序会自动使用已保存的密码登录。"
        )

    # ------------------------------------------------------------------
    # 界面搭建
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.setWindowTitle(f"{APP_NAME_CN} · Booster Robot Manager")
        self.resize(1380, 880)
        self.setMinimumSize(1120, 700)
        self.setAcceptDrops(True)

        central = QWidget()
        central.setObjectName("Root")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 8)
        root.setSpacing(8)

        root.addLayout(self._build_topbar())

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.addWidget(self._build_sidebar())

        right_splitter = QSplitter(Qt.Orientation.Vertical)
        top_splitter = QSplitter(Qt.Orientation.Horizontal)

        self.file_panel = FileBrowserPanel()
        self.deploy_panel = DeployPanel()
        top_splitter.addWidget(self.file_panel)
        top_splitter.addWidget(self.deploy_panel)
        top_splitter.setStretchFactor(0, 3)
        top_splitter.setStretchFactor(1, 2)
        top_splitter.setSizes([620, 440])

        right_splitter.addWidget(top_splitter)
        right_splitter.addWidget(self._build_log_panel())
        right_splitter.setStretchFactor(0, 3)
        right_splitter.setStretchFactor(1, 2)
        right_splitter.setSizes([540, 280])

        main_splitter.addWidget(right_splitter)
        main_splitter.setStretchFactor(0, 0)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setSizes([280, 1080])
        root.addWidget(main_splitter, 1)

        self.statusBar().showMessage("就绪")
        self._build_menu()

        # -- 信号连接 ---------------------------------------------------
        self.file_panel.navigate_requested.connect(self._navigate)
        self.file_panel.preview_requested.connect(self._preview_file)
        self.file_panel.download_requested.connect(self._download_file)
        self.file_panel.delete_requested.connect(self._delete_remote)

        self.deploy_panel.choose_zip_requested.connect(self._choose_zip)
        self.deploy_panel.choose_folder_requested.connect(self._choose_folder)
        self.deploy_panel.deploy_requested.connect(self._deploy)
        self.deploy_panel.start_requested.connect(lambda: self._proc_action("start"))
        self.deploy_panel.stop_requested.connect(lambda: self._proc_action("stop"))
        self.deploy_panel.status_requested.connect(lambda: self._proc_action("status"))
        self.deploy_panel.view_log_requested.connect(self._view_start_log)
        self.deploy_panel.browse_project_requested.connect(self._browse_project)

    def _build_topbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(10)

        title = QLabel("Booster 机器人管理器")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #f0f3f7;")
        bar.addWidget(title)

        self._conn_label = QLabel("未连接")
        self._conn_label.setObjectName("MutedLabel")
        bar.addWidget(self._conn_label)
        bar.addStretch(1)

        self._settings_button = QPushButton("⚙ 密码设置")
        self._settings_button.setObjectName("PrimaryButton")
        self._settings_button.setToolTip("设置默认密码 / 测试连接 / 连接参数")
        self._settings_button.clicked.connect(self._open_settings)
        bar.addWidget(self._settings_button)
        return bar

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setMinimumWidth(260)
        sidebar.setMaximumWidth(340)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("机器人")
        title.setObjectName("SidebarTitle")
        self._robot_count_label = QLabel("0 台")
        self._robot_count_label.setObjectName("MutedLabel")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._robot_count_label)
        layout.addLayout(header)

        self._robot_list = QListWidget()
        self._robot_list.setSpacing(2)
        self._robot_list.itemClicked.connect(self._on_robot_clicked)
        self._robot_list.itemSelectionChanged.connect(self._update_sidebar_buttons)
        layout.addWidget(self._robot_list, 1)

        self._password_hint = QLabel("⚠ 尚未设置机器人密码，请先设置")
        self._password_hint.setObjectName("PasswordHint")
        self._password_hint.setWordWrap(True)
        layout.addWidget(self._password_hint)

        button_row = QHBoxLayout()
        button_row.setSpacing(6)
        self._add_robot_button = QPushButton("＋ 新增")
        self._add_robot_button.clicked.connect(self._add_robot)
        self._edit_robot_button = QPushButton("编辑")
        self._edit_robot_button.clicked.connect(self._edit_robot)
        self._delete_robot_button = QPushButton("删除")
        self._delete_robot_button.setObjectName("DangerButton")
        self._delete_robot_button.clicked.connect(self._delete_robot)
        button_row.addWidget(self._add_robot_button)
        button_row.addWidget(self._edit_robot_button)
        button_row.addWidget(self._delete_robot_button)
        layout.addLayout(button_row)

        self._disconnect_button = QPushButton("断开连接")
        self._disconnect_button.setObjectName("FlatButton")
        self._disconnect_button.clicked.connect(self._disconnect_clicked)
        layout.addWidget(self._disconnect_button)

        self._password_state_label = QLabel("默认密码：未设置")
        self._password_state_label.setObjectName("MutedLabel")
        self._password_state_label.setWordWrap(True)
        layout.addWidget(self._password_state_label)

        return sidebar

    def _build_log_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("运行日志")
        title.setStyleSheet("font-weight: 600; color: #c7cede;")
        self._stage_label = QLabel("")
        self._stage_label.setObjectName("StageLabel")
        clear_button = QPushButton("清空日志")
        clear_button.setObjectName("FlatButton")
        clear_button.clicked.connect(self._clear_log)
        header.addWidget(title)
        header.addWidget(self._stage_label)
        header.addStretch(1)
        header.addWidget(clear_button)
        layout.addLayout(header)

        self._log_view = QPlainTextEdit()
        self._log_view.setObjectName("LogView")
        self._log_view.setReadOnly(True)
        self._log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._log_view.setMaximumBlockCount(50000)
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(11)
        self._log_view.setFont(mono)
        layout.addWidget(self._log_view, 1)
        return panel

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("文件")
        settings_action = QAction("设置 / 密码设置…", self)
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)
        file_menu.addSeparator()
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        help_menu = self.menuBar().addMenu("帮助")
        about_action = QAction("关于", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    # ------------------------------------------------------------------
    # 日志
    # ------------------------------------------------------------------
    def _append_log(self, text: str) -> None:
        """追加一行日志；错误 / 警告 / 阶段分隔线用颜色高亮（复制时仍是纯文本）。"""
        fmt = QTextCharFormat()
        if "[错误]" in text or text.startswith(("部署失败", "编译失败", "连接失败", "启动失败", "停止失败")):
            fmt.setForeground(QColor("#ff8a8a"))
        elif "[警告]" in text:
            fmt.setForeground(QColor("#ffb454"))
        elif "────────" in text or "════════" in text:
            fmt.setForeground(QColor("#8ab4f8"))
        self._log_view.setCurrentCharFormat(fmt)
        self._log_view.appendPlainText(text)
        self._log_view.setCurrentCharFormat(QTextCharFormat())
        scrollbar = self._log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _clear_log(self) -> None:
        self._log_view.clear()
        self._append_log("日志已清空。")

    def _log_separator(self, title: str) -> None:
        self._append_log("")
        self._append_log(f"════════ {title} ════════")

    # ------------------------------------------------------------------
    # 机器人列表
    # ------------------------------------------------------------------
    def _refresh_robot_list(self, select_name: Optional[str] = None) -> None:
        if select_name is None:
            select_name = self._selected_robot_name()
        if select_name is None and self.store.robots:
            select_name = self.store.robots[0].name

        connected_name = self.manager.robot_name if self.manager.connected else None
        self._robot_list.blockSignals(True)
        self._robot_list.clear()
        selected_item = None
        for robot in self.store.robots:
            connected = robot.name == connected_name
            source = self.store.password_source(robot)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, robot.name)
            item.setSizeHint(QSize(0, 56))
            item.setToolTip(
                f"{robot.name}\nIP：{robot.host}:{robot.port}\n"
                f"用户名：{robot.username}\n密码来源：{source}\n"
                f"上传目录：{robot.workspace_dir}"
            )
            self._robot_list.addItem(item)
            self._robot_list.setItemWidget(item, RobotItemWidget(robot, connected, source))
            if robot.name == select_name:
                selected_item = item
        if selected_item is not None:
            self._robot_list.setCurrentItem(selected_item)
        self._robot_list.blockSignals(False)

        self._robot_count_label.setText(f"{len(self.store.robots)} 台")
        self._update_sidebar_buttons()

    def _selected_robot_name(self) -> Optional[str]:
        item = self._robot_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _refresh_password_state(self) -> None:
        has_password = self.store.has_any_password()
        self._password_hint.setVisible(not has_password)
        if self.store.settings.has_default_password():
            self._password_state_label.setText("默认密码：已设置")
            self._password_state_label.setStyleSheet("color: #7ec699;")
        else:
            self._password_state_label.setText("默认密码：未设置")
            self._password_state_label.setStyleSheet("color: #ffb454;")

    def _update_sidebar_buttons(self) -> None:
        busy = self._busy or self._proc_busy or self._connecting
        has_selection = self._selected_robot_name() is not None
        self._robot_list.setEnabled(not busy)
        self._add_robot_button.setEnabled(not busy)
        self._edit_robot_button.setEnabled(not busy and has_selection)
        self._delete_robot_button.setEnabled(not busy and has_selection)
        self._disconnect_button.setEnabled(self.manager.connected and not busy)
        self._settings_button.setEnabled(not busy)

    # ------------------------------------------------------------------
    # 整体状态
    # ------------------------------------------------------------------
    def _apply_state(self) -> None:
        connected = self.manager.connected
        busy = self._busy or self._proc_busy or self._connecting

        if connected:
            robot = self.manager.current_robot()
            name = robot.name if robot else (self.manager.robot_name or "")
            host = f"（{robot.host}）" if robot else ""
            self._conn_label.setText(f"● 已连接：{name}{host}")
            self._conn_label.setStyleSheet("color: #7ec699; font-weight: 600;")
        else:
            self._conn_label.setText("未连接")
            self._conn_label.setStyleSheet("color: #8b929c;")

        self.file_panel.set_connected(connected)
        self.file_panel.setEnabled(connected and not busy)
        self.deploy_panel.set_connected(connected)
        self.deploy_panel.set_busy(busy)

        if not connected:
            self.file_panel.clear()
            self.deploy_panel.set_status(None)

        self._update_sidebar_buttons()

    def _set_connecting(self, connecting: bool) -> None:
        self._connecting = connecting
        if connecting:
            self.statusBar().showMessage("正在连接机器人…")
        self._apply_state()

    # ------------------------------------------------------------------
    # 连接 / 断开
    # ------------------------------------------------------------------
    def _on_robot_clicked(self, item: QListWidgetItem) -> None:
        name = item.data(Qt.ItemDataRole.UserRole)
        if name:
            self._connect_robot(str(name))

    def _connect_robot(
        self,
        name: str,
        password_override: Optional[str] = None,
        retry: bool = False,
    ) -> None:
        if self._busy or self._proc_busy or self._connecting:
            return
        robot = self.store.get_robot(name)
        if robot is None:
            return

        if (
            password_override is None
            and self.manager.connected
            and self.manager.robot_name == name
        ):
            self._refresh_robot_list(select_name=name)
            return

        if password_override is None and not self._ensure_password(robot):
            return

        if self.manager.connected:
            self._disconnect_current(verbose=False)

        self._log_separator(f"连接机器人 {name}")
        if retry:
            self._append_log("正在使用新密码重试连接…")
        self._set_connecting(True)

        worker = ConnectWorker(self.manager, name, password_override)
        self._connect_worker = worker
        self._track(worker)
        worker.log.connect(self.log_line)
        worker.succeeded.connect(
            lambda result: self._on_connect_success(name, retry, password_override)
        )
        worker.failed.connect(
            lambda error: self._on_connect_failed(name, error, retry)
        )
        worker.finished.connect(lambda w=worker: self._on_connect_finished(w))
        worker.start()

    def _on_connect_finished(self, worker: BaseWorker) -> None:
        if self._connect_worker is worker:
            self._connect_worker = None
            self._set_connecting(False)

    def _on_connect_success(
        self, name: str, retry: bool, password_override: Optional[str]
    ) -> None:
        if retry and password_override:
            try:
                self.store.set_robot_password(name, password_override)
            except BrmError as exc:
                self._append_log(f"保存新密码失败：{exc.message}")
            else:
                self._append_log("新密码已保存为该机器人的密码（日志中显示为 ******）")

        switched = self._active_robot != name
        self._active_robot = name
        if switched:
            self._project = None
            self._status = None
            self.deploy_panel.set_project("")
            self.deploy_panel.set_status(None)

        robot = self.store.get_robot(name)
        self._refresh_robot_list(select_name=name)
        self._refresh_password_state()
        self._apply_state()
        if robot is not None:
            self._append_log(f"连接成功：{robot.username}@{robot.host}:{robot.port}")
        self.statusBar().showMessage("连接成功", 4000)
        self._navigate(self.store.settings.remote_home_dir)

    def _on_connect_failed(self, name: str, error: BrmError, retry: bool) -> None:
        # 允许在失败对话框里直接重试：先解除“连接中”锁定
        self._connecting = False
        self._refresh_robot_list(select_name=name)
        self._apply_state()
        self._append_log(f"连接失败：{error.title} - {error.message}")

        if isinstance(error, AuthFailedError):
            if retry:
                QMessageBox.critical(
                    self,
                    "密码不正确",
                    "密码不正确，或账号 / 权限不足。\n\n"
                    "原密码保持不变。可在右上角「⚙ 密码设置」中修改密码后重新连接。",
                )
                return
            password = PasswordDialog.ask(
                self,
                title="密码不正确，是否重新输入？",
                message="密码不正确，是否重新输入？",
                note=(
                    "连接成功后，新密码会保存为该机器人的密码；原密码保持不变。\n"
                    "只有认证失败才会出现此提示，连接超时 / 网络故障不会误报为密码错误。"
                ),
                ok_text="重试连接",
            )
            if password is None:
                self._append_log("已取消重新输入密码。")
                return
            self._connect_robot(name, password_override=password, retry=True)
            return

        QMessageBox.critical(self, error.title, error.user_message())

    def _ensure_password(self, robot: Robot) -> bool:
        if robot.auth_type == "key":
            return True
        if self.store.resolve_password(robot):
            return True

        self._append_log("尚未设置机器人密码，请先设置。")
        password = PasswordDialog.ask(
            self,
            title="设置机器人密码",
            message="尚未设置机器人密码，请先设置。",
            note=(
                "密码会保存在本机 config/robots.json（Base64 混淆，非加密，仅适用于可信本地环境），"
                "之后所有连接默认复用，不再反复询问。"
            ),
            ok_text="保存并连接",
        )
        if password is None:
            self._append_log("已取消：未设置密码，无法连接。")
            return False
        self.store.set_default_password(password)
        self._refresh_password_state()
        self._refresh_robot_list(select_name=robot.name)
        self._append_log("已保存默认密码（日志中显示为 ******）")
        return True

    def _disconnect_clicked(self) -> None:
        if self._busy or self._proc_busy:
            return
        self._disconnect_current(verbose=True)

    def _disconnect_current(self, verbose: bool = True) -> None:
        name = self.manager.robot_name
        was_connected = self.manager.connected or name is not None
        self.manager.disconnect()
        self._active_robot = None
        self._status = None
        self.deploy_panel.set_status(None)
        self._apply_state()
        self._refresh_robot_list()
        if verbose and was_connected:
            self._append_log(f"已断开连接：{name or ''}".rstrip("："))
        if was_connected:
            self.statusBar().showMessage("已断开连接", 3000)

    # ------------------------------------------------------------------
    # 设置 / 机器人增删改
    # ------------------------------------------------------------------
    def _open_settings(self) -> None:
        if self._busy or self._proc_busy:
            return
        dialog = SettingsDialog(self.store, self.manager, self)
        dialog.saved.connect(self._on_settings_saved)
        dialog.exec()
        self._refresh_password_state()
        self._refresh_robot_list(select_name=self._selected_robot_name())

    def _on_settings_saved(self) -> None:
        self._refresh_password_state()
        self._refresh_robot_list(select_name=self._selected_robot_name())
        self.statusBar().showMessage("设置已保存", 4000)

    def _add_robot(self) -> None:
        dialog = RobotDialog(self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        robot = dialog.build_robot()
        try:
            self.store.add_robot(robot)
        except BrmError as exc:
            QMessageBox.warning(self, exc.title, exc.user_message())
            return
        self._refresh_robot_list(select_name=robot.name)
        self._append_log(f"已新增机器人：{robot.name}（{robot.host}:{robot.port}）")

    def _edit_robot(self) -> None:
        name = self._selected_robot_name()
        robot = self.store.get_robot(name) if name else None
        if robot is None:
            return
        dialog = RobotDialog(self, robot)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        updated = dialog.build_robot()
        try:
            self.store.update_robot(robot.name, updated)
        except BrmError as exc:
            QMessageBox.warning(self, exc.title, exc.user_message())
            return
        if self.manager.robot_name == robot.name:
            self._disconnect_current(verbose=False)
            self._append_log("机器人配置已修改，请重新点击连接。")
        self._refresh_robot_list(select_name=updated.name)
        self._refresh_password_state()
        self._append_log(f"已保存机器人配置：{updated.name}")

    def _delete_robot(self) -> None:
        name = self._selected_robot_name()
        robot = self.store.get_robot(name) if name else None
        if robot is None:
            return
        answer = QMessageBox.question(
            self,
            "删除机器人",
            f"确定删除机器人「{robot.name}（{robot.host}）」吗？\n\n此操作只影响本地配置。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.manager.robot_name == robot.name:
            self._disconnect_current(verbose=False)
        try:
            self.store.remove_robot(robot.name)
        except BrmError as exc:
            QMessageBox.warning(self, exc.title, exc.user_message())
            return
        self._refresh_robot_list()
        self._refresh_password_state()
        self._append_log(f"已删除机器人：{robot.name}")

    # ------------------------------------------------------------------
    # 文件浏览
    # ------------------------------------------------------------------
    def _navigate(self, path: str) -> None:
        if not self.manager.connected:
            return
        target = (path or "").strip() or self.store.settings.remote_home_dir
        if not target.startswith("/"):
            target = "/" + target
        self._nav_seq += 1
        seq = self._nav_seq

        self.file_panel.set_path(target)
        self.file_panel.set_loading(True)

        if self._nav_worker is not None and self._nav_worker.isRunning():
            self._nav_worker.cancel()

        worker = ListDirWorker(self.manager, target)
        self._nav_worker = worker
        self._track(worker)
        worker.succeeded.connect(lambda entries, s=seq, p=target: self._on_dir_loaded(s, p, entries))
        worker.failed.connect(lambda error, s=seq: self._on_dir_failed(s, error))
        worker.finished.connect(lambda w=worker: self._on_nav_finished(w))
        worker.start()

    def _on_dir_loaded(self, seq: int, path: str, entries) -> None:
        if seq != self._nav_seq:
            return
        self.file_panel.set_entries(entries)
        self.file_panel.set_path(path)
        self.statusBar().showMessage(f"{path}（{len(entries)} 项）", 4000)

    def _on_dir_failed(self, seq: int, error: BrmError) -> None:
        if seq != self._nav_seq:
            return
        self.file_panel.set_loading(False)
        self._append_log(f"读取目录失败：{error.title} - {error.message}")
        if not self.manager.connected:
            return
        QMessageBox.warning(self, error.title, error.user_message())

    def _on_nav_finished(self, worker: BaseWorker) -> None:
        if self._nav_worker is worker:
            self._nav_worker = None

    def _preview_file(self, path: str) -> None:
        if not self.manager.connected:
            return
        worker = PreviewWorker(self.manager, path, constants.MAX_PREVIEW_BYTES)
        self._track(worker)
        worker.succeeded.connect(lambda result: self._on_preview_loaded(result))
        worker.failed.connect(lambda error: self._on_file_error(error))
        worker.start()

    def _on_preview_loaded(self, result) -> None:
        path, text, truncated = result
        dialog = TextPreviewDialog(self, path, text, truncated)
        dialog.exec()

    def _download_file(self, path: str) -> None:
        if not self.manager.connected:
            return
        default_name = PurePosixPath(path).name or "download.bin"
        local_path, _ = QFileDialog.getSaveFileName(
            self, "下载到本地", str(Path.home() / default_name)
        )
        if not local_path:
            return
        worker = DownloadWorker(self.manager, path, local_path)
        self._track(worker)
        worker.progress.connect(
            lambda percent: self.statusBar().showMessage(f"下载中… {percent}%")
        )
        worker.succeeded.connect(lambda result: self._on_downloaded(result))
        worker.failed.connect(lambda error: self._on_file_error(error))
        worker.start()

    def _on_downloaded(self, result) -> None:
        _remote, local_path, size = result
        from ..core.util import human_size

        self._append_log(f"下载完成：{local_path}（{human_size(size)}）")
        self.statusBar().showMessage("下载完成", 4000)

    def _delete_remote(self, path: str, is_dir: bool) -> None:
        if not self.manager.connected:
            return
        try:
            home_dir = normalize_remote_dir(self.store.settings.remote_home_dir)
        except BrmError as exc:
            QMessageBox.warning(self, exc.title, exc.user_message())
            return

        if strip_trailing_slash(path) == strip_trailing_slash(home_dir):
            QMessageBox.warning(self, "不允许删除", "不允许删除文件浏览根目录。")
            return
        if not path.startswith(home_dir):
            QMessageBox.warning(
                self,
                "不允许删除",
                f"只允许删除文件浏览根目录（{home_dir}）下的内容。",
            )
            return

        robot = self.manager.current_robot()
        workspace = normalize_remote_dir(
            robot.workspace_dir if robot else constants.DEFAULT_REMOTE_WORKSPACE
        )
        extra = ""
        if not path.startswith(workspace):
            extra = "\n\n注意：该路径不在 Workspace 内，删除后无法恢复。"
        kind = "目录" if is_dir else "文件"
        answer = QMessageBox.question(
            self,
            "确认删除",
            f"确定删除{kind}吗？\n\n{path}{extra}\n\n删除后无法恢复。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        worker = DeleteWorker(self.manager, path, is_dir)
        self._track(worker)
        worker.succeeded.connect(lambda deleted: self._on_deleted(deleted))
        worker.failed.connect(lambda error: self._on_file_error(error))
        worker.start()

    def _on_deleted(self, path: str) -> None:
        self._append_log(f"已删除：{path}")
        self.statusBar().showMessage("删除完成", 4000)
        self._navigate(self.file_panel.current_path)

    def _on_file_error(self, error: BrmError) -> None:
        self._append_log(f"文件操作失败：{error.title} - {error.message}")
        if not self.manager.connected:
            return
        QMessageBox.warning(self, error.title, error.user_message())

    # ------------------------------------------------------------------
    # 部署
    # ------------------------------------------------------------------
    def _choose_zip(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择要部署的 zip 文件",
            str(Path.home()),
            "ZIP 压缩包 (*.zip);;所有文件 (*)",
        )
        if path:
            self._set_zip(Path(path))

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "选择要打包并部署的文件夹", str(Path.home())
        )
        if folder:
            self._zip_folder(Path(folder))

    def _zip_folder(self, folder: Path) -> None:
        """选择的是文件夹时，先在工作线程里压缩成 zip，再交给部署流程。"""
        if self._busy or self._proc_busy:
            return
        if self._zip_worker is not None and self._zip_worker.isRunning():
            return
        if not folder.is_dir():
            QMessageBox.warning(self, "不是文件夹", f"路径不是文件夹：\n{folder}")
            return

        self._append_log(f"正在压缩文件夹：{folder}")
        self.deploy_panel.set_zipping(True)
        self.deploy_panel.set_progress(0, "正在压缩…")

        worker = ZipFolderWorker(folder, constants.TEMP_DIR)
        self._zip_worker = worker
        self._track(worker)
        worker.log.connect(self.log_line)
        worker.progress.connect(self._on_zip_progress)
        worker.zip_created.connect(self._on_zip_created)
        worker.failed.connect(self._on_zip_failed)
        worker.finished.connect(lambda w=worker: self._on_zip_worker_finished(w))
        worker.start()

    def _on_zip_progress(self, percent: int) -> None:
        self.deploy_panel.set_progress(percent, f"正在压缩 {percent}%")

    def _on_zip_created(self, result: LocalZipResult) -> None:
        self._temp_zips.add(result.path)
        for old in list(self._temp_zips):
            if old != result.path:
                try:
                    old.unlink()
                except OSError:
                    pass
                self._temp_zips.discard(old)
        self._append_log(describe_result(result))
        self._set_zip(result.path, origin_dir=result.source_dir)
        if self.deploy_panel.zip_path == result.path:
            self.deploy_panel.set_progress(100, "压缩完成，可以部署")

    def _on_zip_failed(self, error: BrmError) -> None:
        self.deploy_panel.set_progress(0, "压缩失败")
        self._append_log(f"压缩失败：{error.title} - {error.message}")
        if self._closing:
            return
        QMessageBox.warning(self, error.title, error.user_message())

    def _on_zip_worker_finished(self, worker: BaseWorker) -> None:
        if self._zip_worker is worker:
            self._zip_worker = None
        self.deploy_panel.set_zipping(False)

    def _set_zip(self, path: Path, origin_dir: Optional[Path] = None) -> None:
        try:
            inspection = inspect_zip(path)
        except BrmError as exc:
            self.deploy_panel.set_zip(None)
            self._append_log(f"文件校验失败：{exc.message}")
            if not self._closing:
                QMessageBox.warning(self, exc.title, exc.user_message())
            return
        self.deploy_panel.set_zip(path)
        from ..core.util import human_size

        if origin_dir is not None:
            self._append_log(
                f"已选择自动打包的 zip：{path.name}（来源文件夹：{origin_dir}，"
                f"{human_size(inspection.size)}，{inspection.entry_count} 个条目）"
            )
        else:
            self._append_log(
                f"已选择 zip：{path.name}（{human_size(inspection.size)}，"
                f"{inspection.entry_count} 个条目）"
            )
        self.statusBar().showMessage(f"已选择：{path.name}", 4000)

    def _deploy(self, zip_path: str) -> None:
        if self._busy or self._proc_busy:
            return
        if not self.manager.connected:
            QMessageBox.warning(self, "未连接", "请先在左侧选择机器人并等待连接成功。")
            return
        path = Path(zip_path)
        try:
            inspect_zip(path)
        except BrmError as exc:
            QMessageBox.warning(self, exc.title, exc.user_message())
            return

        if self._status is not None and self._status.running:
            answer = QMessageBox.question(
                self,
                "服务正在运行",
                f"检测到服务正在运行（{self._status.text}）。\n\n"
                "部署会清理远端同名工程目录，建议先停止服务。是否先停止服务再部署？",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._pending_deploy = path
                self._append_log("部署前先停止正在运行的服务…")
                self._proc_action("stop", on_success=self._start_pending_deploy)
                return
            self._append_log("警告：服务仍在运行，继续部署可能覆盖正在使用的工程文件。")

        self._start_deploy(path)

    def _start_pending_deploy(self) -> None:
        path, self._pending_deploy = self._pending_deploy, None
        if path is not None:
            self._start_deploy(path)

    def _start_deploy(self, path: Path) -> None:
        if self._busy or not self.manager.connected:
            return
        robot = self.manager.current_robot()
        if robot is None:
            return

        self._busy = True
        self._apply_state()
        self.deploy_panel.set_progress(0, "准备部署…")
        self.deploy_panel.reset_compile()

        password = self.store.resolve_password(robot) or ""
        worker = DeployWorker(
            self.manager.session(),
            self.manager.sftp(),
            robot.name,
            path,
            robot.workspace_dir,
            secrets=(password,),
        )
        self._deploy_worker = worker
        self._track(worker)
        worker.log.connect(self.log_line)
        worker.progress.connect(self._on_deploy_progress)
        worker.compile_progress.connect(self.deploy_panel.set_compile_progress)
        worker.deploy_finished.connect(self._on_deploy_finished)
        worker.failed.connect(self._on_deploy_failed)
        worker.finished.connect(lambda w=worker: self._on_deploy_worker_finished(w))
        self._append_log(f"开始部署：{path.name}")
        self.statusBar().showMessage("正在部署…")
        worker.start()

    def _on_deploy_progress(self, percent: int) -> None:
        if percent < 100:
            self.deploy_panel.set_progress(percent, f"上传中 {percent}%")
        else:
            self.deploy_panel.set_progress(100, "上传完成")

    def _on_deploy_finished(self, result: DeployResult) -> None:
        self._project = ProjectContext(
            project_dir=result.project_dir,
            project_name=result.project_name,
            start_script=result.start_script,
        )
        self.deploy_panel.set_project(result.project_dir, result.project_name)
        self.deploy_panel.set_progress(100, "部署完成")
        self._status = None
        self.deploy_panel.set_status(None)
        self._append_log(f"部署成功：项目目录 {result.project_dir}")
        self.statusBar().showMessage("部署完成", 5000)
        self._proc_action("status")

    def _on_deploy_failed(self, error: BrmError) -> None:
        self._append_log(f"部署失败：{error.title} - {error.message}")
        self.deploy_panel.set_progress(0, "部署失败")
        if isinstance(error, DeployCancelledError):
            QMessageBox.information(self, "部署已取消", error.user_message())
        else:
            QMessageBox.critical(self, error.title, error.user_message())

    def _on_deploy_worker_finished(self, worker: BaseWorker) -> None:
        if self._deploy_worker is worker:
            self._deploy_worker = None
        self._busy = False
        self._apply_state()

    # ------------------------------------------------------------------
    # 服务控制
    # ------------------------------------------------------------------
    def _proc_action(self, action: str, on_success=None) -> None:
        if action != "status" and (not self.manager.connected):
            QMessageBox.warning(self, "未连接", "请先连接机器人。")
            return
        if not self.manager.connected:
            return

        context = self._project
        if context is None:
            self._append_log("尚未部署工程或选择已有项目，无法执行服务操作。")
            if action != "status":
                QMessageBox.information(
                    self,
                    "请先选择项目",
                    "请先完成一次部署，或点击部署面板中的「选择 Workspace 里的已有项目…」。",
                )
            return

        if action == "start" and not context.start_script:
            QMessageBox.warning(
                self,
                "未找到 start.sh",
                "当前项目未记录 start.sh。请重新部署，或重新选择项目目录。",
            )
            return

        if self._proc_worker is not None and self._proc_worker.isRunning():
            self.statusBar().showMessage("上一个服务操作尚未结束，请稍候…", 4000)
            return

        robot = self.manager.current_robot()
        if robot is None:
            return
        self._proc_busy = True
        self._apply_state()

        # 启动 / 停止时把该机器人使用的密码（默认密码或单台覆盖密码）自动
        # 写入脚本的标准输入，供需要连续输入密码的脚本（含 sudo）使用。
        password = ""
        if action in ("start", "stop"):
            password = self.store.resolve_password(robot) or ""

        worker = ProcWorker(
            self.manager.session(),
            robot.workspace_dir,
            action,
            project_name=context.project_name,
            project_dir=context.project_dir,
            start_script=context.start_script,
            password=password,
        )
        self._proc_worker = worker
        self._track(worker)
        worker.log.connect(self.log_line)
        worker.succeeded.connect(
            lambda result: self._on_proc_success(action, result, on_success)
        )
        worker.failed.connect(lambda error: self._on_proc_failed(action, error))
        worker.finished.connect(lambda w=worker: self._on_proc_finished(w))
        worker.start()

    def _on_proc_success(self, action: str, result, on_success) -> None:
        if action == "tail":
            if self._tail_dialog is not None:
                self._tail_dialog.set_text(result if isinstance(result, str) else "")
            return

        if isinstance(result, ProcStatus):
            self._status = result
            self.deploy_panel.set_status(result)
            self._append_log(f"服务状态：{result.text}")
        if on_success is not None:
            on_success()

    def _on_proc_failed(self, action: str, error: BrmError) -> None:
        self._append_log(f"服务操作失败（{action}）：{error.title} - {error.message}")
        if action == "tail":
            if self._tail_dialog is not None:
                self._tail_dialog.set_error(error.user_message())
            return
        if action == "status":
            self.deploy_panel.set_status_error(error.title)
            return
        QMessageBox.warning(self, error.title, error.user_message())
        if action == "start":
            self._proc_action("status")

    def _on_proc_finished(self, worker: BaseWorker) -> None:
        if self._proc_worker is worker:
            self._proc_worker = None
        self._proc_busy = False
        self._apply_state()

    # ------------------------------------------------------------------
    # 已有项目选择
    # ------------------------------------------------------------------
    def _browse_project(self) -> None:
        if not self.manager.connected or self._busy:
            return
        robot = self.manager.current_robot()
        if robot is None:
            return
        try:
            workspace = normalize_remote_dir(robot.workspace_dir)
        except BrmError as exc:
            QMessageBox.warning(self, exc.title, exc.user_message())
            return

        worker = ListDirWorker(self.manager, workspace)
        self._track(worker)
        worker.succeeded.connect(lambda entries: self._on_workspace_loaded(workspace, entries))
        worker.failed.connect(lambda error: self._on_file_error(error))
        worker.start()

    def _on_workspace_loaded(self, workspace: str, entries) -> None:
        dialog = ProjectPickerDialog(self, workspace, entries)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        entry = dialog.selected_entry()
        if entry is None:
            return

        worker = FindScriptWorker(self.manager.session(), self.manager.sftp(), entry.path, "start.sh")
        self._track(worker)
        worker.succeeded.connect(
            lambda script: self._on_project_selected(entry.path, entry.name, script)
        )
        worker.failed.connect(
            lambda error: self._on_project_selected(entry.path, entry.name, "", error)
        )
        worker.start()

    def _on_project_selected(
        self,
        project_dir: str,
        project_name: str,
        start_script: str,
        error: Optional[BrmError] = None,
    ) -> None:
        self._project = ProjectContext(
            project_dir=project_dir,
            project_name=project_name,
            start_script=start_script,
        )
        self.deploy_panel.set_project(project_dir, project_name)
        self._status = None
        self.deploy_panel.set_status(None)
        self._append_log(f"已选择项目：{project_dir}")
        if error is not None:
            self._append_log(f"提示：{error.title} - {error.message}")
            QMessageBox.warning(
                self,
                "未找到 start.sh",
                "项目已选中，但未找到 start.sh，启动功能不可用。\n\n"
                f"{error.user_message()}",
            )
        self._proc_action("status")

    # ------------------------------------------------------------------
    # 启动日志查看
    # ------------------------------------------------------------------
    def _view_start_log(self) -> None:
        context = self._project
        if context is None or not self.manager.connected:
            return
        dialog = ServiceLogDialog(self, context.project_name)
        self._tail_dialog = dialog
        dialog.refresh_requested.connect(lambda: self._refresh_start_log(dialog))
        dialog.finished.connect(lambda _result, d=dialog: self._on_tail_dialog_closed(d))
        dialog.show()
        self._refresh_start_log(dialog)

    def _on_tail_dialog_closed(self, dialog: ServiceLogDialog) -> None:
        if self._tail_dialog is dialog:
            self._tail_dialog = None

    def _refresh_start_log(self, dialog: ServiceLogDialog) -> None:
        if not self.manager.connected:
            dialog.set_error("未连接机器人。")
            return
        context = self._project
        if context is None:
            dialog.set_error("尚未选择项目。")
            return
        if self._tail_worker is not None and self._tail_worker.isRunning():
            return

        robot = self.manager.current_robot()
        if robot is None:
            dialog.set_error("未连接机器人。")
            return
        dialog.set_loading(True)

        worker = ProcWorker(
            self.manager.session(),
            robot.workspace_dir,
            "tail",
            project_name=context.project_name,
        )
        self._tail_worker = worker
        self._track(worker)
        worker.succeeded.connect(
            lambda text, d=dialog: self._on_tail_loaded(d, text)
        )
        worker.failed.connect(lambda error, d=dialog: self._on_tail_failed(d, error))
        worker.finished.connect(lambda w=worker: self._on_tail_finished(w))
        worker.start()

    def _on_tail_loaded(self, dialog: ServiceLogDialog, text: str) -> None:
        if dialog is self._tail_dialog:
            dialog.set_text(text if isinstance(text, str) else "")

    def _on_tail_failed(self, dialog: ServiceLogDialog, error: BrmError) -> None:
        if dialog is self._tail_dialog:
            dialog.set_error(error.user_message())

    def _on_tail_finished(self, worker: BaseWorker) -> None:
        if self._tail_worker is worker:
            self._tail_worker = None

    # ------------------------------------------------------------------
    # 拖拽支持
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_dir():
                self._append_log(f"拖入文件夹，准备自动打包：{path}")
                self._zip_folder(path)
                event.acceptProposedAction()
                return
            self._set_zip(path)
            event.acceptProposedAction()
            return
        event.ignore()

    # ------------------------------------------------------------------
    # 其他
    # ------------------------------------------------------------------
    def _track(self, worker: BaseWorker) -> None:
        self._workers.add(worker)
        worker.finished.connect(lambda w=worker: self._untrack(w))

    def _untrack(self, worker: BaseWorker) -> None:
        self._workers.discard(worker)
        worker.deleteLater()

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "关于",
            f"<h3>Booster 机器人管理器</h3>"
            f"<p>版本：{__version__}</p>"
            "<p>PySide6 + paramiko 实现的 Booster 机器人远程部署工具。</p>"
            "<p>功能：多机器人管理、密码一次设置长期复用、远端文件浏览、"
            "zip 上传部署、编译、start.sh 启停控制。</p>",
        )

    def closeEvent(self, event) -> None:
        self._closing = True
        if self._deploy_worker is not None and self._deploy_worker.isRunning():
            answer = QMessageBox.question(
                self,
                "部署仍在进行",
                "部署仍在进行，确定要退出吗？\n\n"
                "退出将取消部署；远端已经执行过的步骤不会回滚。",
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._closing = False
                event.ignore()
                return

        for worker in list(self._workers):
            worker.cancel()
        for worker in list(self._workers):
            if worker.isRunning() and not worker.wait(3000):
                worker.terminate()
                worker.wait(500)

        for path in list(self._temp_zips):
            try:
                path.unlink()
            except OSError:
                pass
        self._temp_zips.clear()
        cleanup_temp_archives(constants.TEMP_DIR, older_than_seconds=24 * 3600)
        self.manager.disconnect()
        super().closeEvent(event)
