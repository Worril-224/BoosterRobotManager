"""QThread 工作线程：所有网络 / 耗时操作都放在这里，避免阻塞界面。

每个 Worker 只做一件事，结果通过 Qt 信号回主线程；异常统一转换成
:class:`BrmError`，界面据此做分级提示。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QThread, Signal

from ..core.deployer import DeployResult, Deployer
from ..core.errors import BrmError
from ..core.local_zip import create_zip_from_folder
from ..core.logbook import DeployLog
from ..core.proc_control import ProcControl
from ..core.robots import Robot
from ..core.scripts import find_script
from ..core.session_manager import SessionManager
from ..core.sftp_client import RemoteEntry


class BaseWorker(QThread):
    """所有工作线程的基类：统一异常转换与取消标记。"""

    log = Signal(str)
    failed = Signal(object)  # BrmError
    succeeded = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cancel_event = threading.Event()

    # ------------------------------------------------------------------
    def cancel(self) -> None:
        self._cancel_event.set()
        self.requestInterruption()

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def run(self) -> None:  # noqa: D401 - QThread 入口
        try:
            result = self.execute()
        except BrmError as exc:
            self.failed.emit(exc)
        except Exception as exc:  # noqa: BLE001 - 兜底，绝不把堆栈直接展示给用户
            self.failed.emit(
                BrmError(
                    "发生未预期的错误。",
                    hint="请重试；若持续出现，请检查机器人状态与网络。",
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
        else:
            self.succeeded.emit(result)

    def execute(self):
        raise NotImplementedError


class ConnectWorker(BaseWorker):
    """连接机器人（自动使用已保存密码）。"""

    def __init__(
        self,
        manager: SessionManager,
        robot_name: str,
        password_override: Optional[str] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self.robot_name = robot_name
        self.password_override = password_override

    def execute(self) -> Robot:
        return self.manager.connect(
            self.robot_name, password_override=self.password_override, log=self.log.emit
        )


class TestConnectionWorker(BaseWorker):
    """用候选密码测试连接（不改变当前会话）。"""

    def __init__(self, manager: SessionManager, robot: Robot, password: Optional[str], parent=None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.robot = robot
        self.password = password

    def execute(self) -> None:
        self.manager.test_connection(self.robot, self.password, log=self.log.emit)
        return None


class ListDirWorker(BaseWorker):
    def __init__(self, manager: SessionManager, path: str, parent=None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.path = path

    def execute(self) -> List[RemoteEntry]:
        return self.manager.sftp().listdir(self.path)


class PreviewWorker(BaseWorker):
    def __init__(self, manager: SessionManager, path: str, max_bytes: int, parent=None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.path = path
        self.max_bytes = max_bytes

    def execute(self):
        text, truncated = self.manager.sftp().read_text(self.path, self.max_bytes)
        return self.path, text, truncated


class DownloadWorker(BaseWorker):
    progress = Signal(int)

    def __init__(self, manager: SessionManager, remote_path: str, local_path: str, parent=None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.remote_path = remote_path
        self.local_path = local_path

    def execute(self):
        def on_progress(done: int, total: int) -> None:
            if total:
                self.progress.emit(int(done * 100 / total))

        size = self.manager.sftp().download(self.remote_path, self.local_path, on_progress)
        return self.remote_path, self.local_path, size


class DeleteWorker(BaseWorker):
    def __init__(self, manager: SessionManager, path: str, is_dir: bool, parent=None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.path = path
        self.is_dir = is_dir

    def execute(self) -> str:
        sftp = self.manager.sftp()
        if self.is_dir:
            sftp.remove_tree(self.path)
        else:
            sftp.remove_file(self.path)
        return self.path


class ZipFolderWorker(BaseWorker):
    """把本地文件夹压缩成 zip（选择文件夹部署时使用）。"""

    progress = Signal(int)
    zip_created = Signal(object)

    def __init__(self, source_dir: Path, dest_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.source_dir = Path(source_dir)
        self.dest_dir = Path(dest_dir)

    def execute(self):
        result = create_zip_from_folder(
            self.source_dir,
            self.dest_dir,
            progress=self.progress.emit,
            cancel_event=self._cancel_event,
        )
        self.zip_created.emit(result)
        return result


class DeployWorker(BaseWorker):
    """上传 + 部署流水线。"""

    progress = Signal(int)
    deploy_finished = Signal(object)  # DeployResult

    def __init__(
        self,
        session,
        sftp,
        robot_name: str,
        zip_path: Path,
        workspace_dir: str,
        log_dir: Optional[Path] = None,
        secrets: tuple = (),
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.sftp = sftp
        self.robot_name = robot_name
        self.zip_path = Path(zip_path)
        self.workspace_dir = workspace_dir
        self.log_dir = log_dir
        self.secrets = secrets

    def execute(self) -> DeployResult:
        deploy_log = DeployLog(
            self.robot_name,
            log_dir=self.log_dir,
            echo=self.log.emit,
            secrets=self.secrets,
        )
        try:
            deploy_log.info(f"开始部署：{self.zip_path.name} → {self.workspace_dir}")
            deployer = Deployer(
                self.session,
                self.sftp,
                deploy_log,
                robot_name=self.robot_name,
                progress=self.progress.emit,
                cancel_event=self._cancel_event,
            )
            result = deployer.run(self.zip_path, self.workspace_dir)
            deploy_log.info(f"本次部署日志已保存：{deploy_log.path}")
            self.deploy_finished.emit(result)
            return result
        finally:
            deploy_log.close()


class ProcWorker(BaseWorker):
    """start.sh 启动 / 停止 / 状态 / 日志。"""

    def __init__(
        self,
        session,
        workspace_dir: str,
        action: str,
        project_name: str = "",
        project_dir: str = "",
        start_script: str = "",
        password: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.workspace_dir = workspace_dir
        self.action = action
        self.project_name = project_name
        self.project_dir = project_dir
        self.start_script = start_script
        self.password = password

    def execute(self):
        control = ProcControl(self.session, self.workspace_dir, log=self.log.emit)
        if self.action == "status":
            return control.status(self.project_name, self.project_dir)
        if self.action == "start":
            return control.start(
                self.project_name,
                self.project_dir,
                self.start_script,
                password=self.password,
            )
        if self.action == "stop":
            return control.stop(
                self.project_name,
                self.project_dir,
                password=self.password,
            )
        if self.action == "tail":
            return control.tail_log(self.project_name)
        raise BrmError(f"未知的服务操作：{self.action}")


class FindScriptWorker(BaseWorker):
    """在已有项目目录中查找 start.sh（用于“选择已有项目”）。"""

    def __init__(self, session, sftp, project_dir: str, script_name: str = "start.sh", parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.sftp = sftp
        self.project_dir = project_dir
        self.script_name = script_name

    def execute(self) -> str:
        return find_script(
            self.session, self.sftp, self.project_dir, self.script_name, log=self.log.emit
        )
