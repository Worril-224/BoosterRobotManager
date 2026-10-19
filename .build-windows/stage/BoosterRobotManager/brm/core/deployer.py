"""部署流水线（R3 → R7）。

流程：本地校验 → 远端同名清理 → 上传 → 远端类型校验 → 解压 → 定位项目目录
→ 编译 → 授权 start.sh。

所有耗时步骤都通过注入的 :class:`DeployLog` 实时输出，磁盘路径一律经
``shlex.quote`` 转义，删除前统一走 :func:`util.assert_safe_delete_path`。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .. import constants
from .errors import (
    BrmError,
    CommandTimeoutError,
    CompileFailedError,
    DeployCancelledError,
    FileValidationError,
    MissingRemoteCommandError,
    PermissionDeniedRemoteError,
    RemoteCommandError,
    ScriptNotFoundError,
    classify_remote_failure,
)
from .logbook import DeployLog
from .remote_env import env_setup_command
from .scripts import find_script
from .sftp_client import SFTPClient
from .util import (
    assert_safe_delete_path,
    human_size,
    normalize_remote_dir,
    posix_join,
    quote,
)
from .zipguard import ZipInspection, inspect_zip


@dataclass
class DeployResult:
    """部署成功后的关键产物。"""

    robot_name: str
    zip_path: Path
    remote_zip: str
    project_dir: str
    project_name: str
    build_script: str
    start_script: str
    inspection: Optional[ZipInspection] = None


class Deployer:
    """把一次 zip 部署拆成可观察的 9 个阶段。"""

    TOTAL_STAGES = 9

    def __init__(
        self,
        session,
        sftp: SFTPClient,
        log: DeployLog,
        *,
        robot_name: str,
        progress: Optional[Callable[[int], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        self.session = session
        self.sftp = sftp
        self.log = log
        self.robot_name = robot_name
        self.progress = progress
        self.cancel_event = cancel_event or threading.Event()

    # ------------------------------------------------------------------
    # 小工具
    # ------------------------------------------------------------------
    def _check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise DeployCancelledError("部署已取消。", hint="已保留远端当前状态，可重新发起部署。")

    def _set_progress(self, value: int) -> None:
        if self.progress is not None:
            try:
                self.progress(max(0, min(100, int(value))))
            except Exception:
                pass

    def _run(self, command: str, *, what: str, timeout: Optional[float] = None):
        """执行短命令；退出码非 0 时给出分级错误。"""
        result = self.session.exec_command(command, timeout=timeout)
        if not result.ok:
            mapped = classify_remote_failure(result.output, result.exit_code)
            message = f"{what}失败（退出码 {result.exit_code}）。"
            if mapped is not None:
                mapped.message = f"{what}失败：{mapped.message}"
                raise mapped
            raise RemoteCommandError(
                message,
                command=command,
                exit_code=result.exit_code,
                output=result.output,
            )
        return result

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def run(self, zip_path: Path, workspace_dir: str) -> DeployResult:
        zip_path = Path(zip_path)
        workspace = normalize_remote_dir(workspace_dir)

        # ---- 阶段 1：本地 zip 校验（失败时不做任何远端操作）-------------
        self.log.stage(f"阶段 1/{self.TOTAL_STAGES}：校验本地压缩包")
        self._check_cancel()
        inspection = self._stage_validate_local(zip_path)
        self._set_progress(0)

        zip_name = zip_path.name
        remote_zip = posix_join(workspace, zip_name)
        staging_dir = posix_join(workspace, zip_path.stem)
        single_top = inspection.project_subdir

        # zip 内只有唯一顶层目录时直接解压到 Workspace/（与 R6 的命令一致），
        # 项目目录取该顶层目录，避免出现 xxx/xxx 双重嵌套；
        # 否则先解压到独立的 Workspace/<zip名>/ 目录，避免污染 Workspace。
        if single_top:
            extract_dest = workspace
            candidate_project = posix_join(workspace, single_top)
            cleanup_dirs = [staging_dir, candidate_project]
        else:
            extract_dest = staging_dir
            candidate_project = staging_dir
            cleanup_dirs = [staging_dir]

        # ---- 阶段 2：远端同名检测与清理（R4）--------------------------
        self.log.stage(f"阶段 2/{self.TOTAL_STAGES}：检查并清理远端同名文件 / 目录")
        self._check_cancel()
        self._stage_cleanup_remote(workspace, remote_zip, cleanup_dirs)

        # ---- 阶段 3：上传（R3）----------------------------------------
        self.log.stage(f"阶段 3/{self.TOTAL_STAGES}：上传 zip 到远端")
        self._check_cancel()
        self._stage_upload(zip_path, remote_zip)
        self._set_progress(100)

        # ---- 阶段 4：远端类型校验（R5 第 3 条）------------------------
        self.log.stage(f"阶段 4/{self.TOTAL_STAGES}：远端文件类型校验")
        self._check_cancel()
        self._stage_remote_validate(remote_zip, zip_path)

        # ---- 阶段 5：解压（R6）----------------------------------------
        self.log.stage(f"阶段 5/{self.TOTAL_STAGES}：解压到 Workspace")
        self._check_cancel()
        self._stage_extract(workspace, remote_zip, extract_dest)

        # ---- 阶段 6：定位项目目录（R6）--------------------------------
        self.log.stage(f"阶段 6/{self.TOTAL_STAGES}：定位项目目录")
        self._check_cancel()
        project_dir = self._stage_locate_project(extract_dest, candidate_project, single_top)
        project_name = project_dir.rstrip("/").rsplit("/", 1)[-1]

        # ---- 阶段 7：编译（R7）----------------------------------------
        self.log.stage(f"阶段 7/{self.TOTAL_STAGES}：编译项目")
        self._check_cancel()
        build_script = self._stage_build(project_dir)

        # ---- 阶段 8：授权 start.sh（R8）------------------------------
        self.log.stage(f"阶段 8/{self.TOTAL_STAGES}：查找并授权 start.sh")
        self._check_cancel()
        start_script = self._stage_chmod_start(project_dir)

        # ---- 阶段 9：完成 ---------------------------------------------
        self.log.stage(f"阶段 9/{self.TOTAL_STAGES}：部署完成")
        self.log.info(f"项目目录：{project_dir}")
        self.log.info(f"编译脚本：{build_script}")
        self.log.info(f"启动脚本：{start_script} （已具备执行权限）")
        self.log.info("可以点击「启动」运行服务，或查看 start.log 确认运行状态。")

        return DeployResult(
            robot_name=self.robot_name,
            zip_path=zip_path,
            remote_zip=remote_zip,
            project_dir=project_dir,
            project_name=project_name,
            build_script=build_script,
            start_script=start_script,
            inspection=inspection,
        )

    # ------------------------------------------------------------------
    # 各阶段实现
    # ------------------------------------------------------------------
    def _stage_validate_local(self, zip_path: Path) -> ZipInspection:
        self.log.info(f"待部署文件：{zip_path}")
        inspection = inspect_zip(zip_path)
        self.log.info(
            f"校验通过：{zip_path.name}（{human_size(inspection.size)}，"
            f"{inspection.entry_count} 个条目）"
        )
        if inspection.project_subdir:
            self.log.info(f"zip 内只有一个顶层目录「{inspection.project_subdir}」，将直接作为项目目录。")
        elif inspection.top_level_dirs:
            self.log.info(
                "zip 内包含多个顶层目录或根级文件，项目目录将使用 zip 文件名："
                f"{zip_path.stem}/"
            )
        return inspection

    def _stage_cleanup_remote(self, workspace: str, remote_zip: str, cleanup_dirs) -> None:
        self.sftp.mkdir_p(workspace)
        targets = []
        for target in [remote_zip, *cleanup_dirs]:
            if target not in targets:
                targets.append(target)

        for target in targets:
            assert_safe_delete_path(target, workspace)
            entry = self.sftp.stat(target)
            if entry is None:
                self.log.info(f"远端不存在同名对象：{target}")
                continue
            if entry.is_dir:
                command = f"rm -rf -- {quote(target)}"
                self._run(command, what=f"删除远端目录 {target} ")
                self.log.info(f"已删除远端同名目录：{target}")
            else:
                command = f"rm -f -- {quote(target)}"
                self._run(command, what=f"删除远端文件 {target} ")
                self.log.info(f"已删除远端同名文件：{target}")
            if self.sftp.exists(target):
                raise RemoteCommandError(
                    f"删除远端同名对象失败：{target}",
                    command=command,
                    hint="请检查文件是否被其他进程占用，或 booster 账号是否有权限。",
                )

    def _stage_upload(self, zip_path: Path, remote_zip: str) -> None:
        local_size = zip_path.stat().st_size
        self.log.info(f"开始上传：{zip_path.name} → {remote_zip}（{human_size(local_size)}）")
        last_reported = {"percent": -1}

        def on_progress(transferred: int, total: int) -> None:
            percent = int(transferred * 100 / total) if total > 0 else 0
            if percent != last_reported["percent"]:
                last_reported["percent"] = percent
                self._set_progress(percent)

        self.sftp.upload(str(zip_path), remote_zip, on_progress)
        self._set_progress(100)

        remote_entry = self.sftp.stat(remote_zip)
        if remote_entry is None:
            raise BrmError("上传后远端未找到文件，请重试。")
        if remote_entry.size != local_size:
            raise BrmError(
                "上传后的远端文件大小与本地不一致，可能传输中断。",
                hint=f"本地 {local_size} 字节，远端 {remote_entry.size} 字节。",
            )
        self.log.info(f"上传完成：远端大小 {human_size(remote_entry.size)}")

    def _stage_remote_validate(self, remote_zip: str, zip_path: Path) -> None:
        remote_name = remote_zip.rsplit("/", 1)[-1]
        if not remote_name.lower().endswith(".zip"):
            try:
                self._run(f"rm -f -- {quote(remote_zip)}", what="清理非法文件")
            finally:
                pass
            raise FileValidationError(
                f"远端校验失败：只支持 .zip 压缩包，当前文件：{remote_name}",
                hint="文件已被清理，请重新选择合法的 zip 包。",
            )
        header = self.sftp.read_bytes(remote_zip, 4)
        if header not in constants.ZIP_MAGIC:
            try:
                self._run(f"rm -f -- {quote(remote_zip)}", what="清理非法文件")
            finally:
                pass
            raise FileValidationError(
                f"远端校验失败：{remote_name} 不是有效的 zip 文件。",
                hint="文件已被清理，请重新打包后再试。",
            )
        self.log.info(f"远端校验通过：{remote_name} 是合法 zip 文件")

    def _stage_extract(self, workspace: str, remote_zip: str, extract_dest: str) -> None:
        # unzip 依赖检测
        result = self.session.exec_command("command -v unzip")
        if not result.ok:
            raise MissingRemoteCommandError(
                "远端未安装 unzip，无法解压。",
                hint="请在机器人上执行：sudo apt install -y unzip",
            )
        self.log.info(f"远端 unzip：{result.stdout.strip() or 'unzip'}")

        self.sftp.mkdir_p(extract_dest)
        command = (
            f"cd {quote(workspace)} && unzip -o {quote(remote_zip)} "
            f"-d {quote(extract_dest)} -x '__MACOSX/*' '.DS_Store'"
        )
        self.log.info(f"执行解压：unzip -o {remote_zip} -d {extract_dest}")
        result = self.session.exec_command(command)
        for line in (result.stdout or "").splitlines():
            if line.strip():
                self.log.raw(f"  {line}")
        if not result.ok:
            mapped = classify_remote_failure(result.output, result.exit_code)
            if mapped is not None:
                raise mapped
            raise RemoteCommandError(
                f"解压失败（退出码 {result.exit_code}）。",
                command=command,
                exit_code=result.exit_code,
                output=result.output,
                hint="请确认 zip 包完整、远端磁盘空间充足。",
            )
        self.log.info("解压完成")

    def _stage_locate_project(
        self, extract_dest: str, candidate_project: str, single_top: Optional[str]
    ) -> str:
        if single_top:
            entry = self.sftp.stat(candidate_project)
            if entry is None or not entry.is_dir:
                raise BrmError(
                    f"解压后未找到项目目录：{candidate_project}",
                    hint="请检查 zip 包内部的目录结构。",
                )
            self.log.info(f"识别到唯一顶层目录「{single_top}」，项目目录不再重复嵌套。")
            self.log.info(f"项目目录：{candidate_project}")
            return candidate_project

        entries = self.sftp.listdir(extract_dest)
        names = [entry.name for entry in entries if entry.name not in ("__MACOSX", ".DS_Store")]
        meaningful = [
            entry for entry in entries
            if entry.name not in ("__MACOSX", ".DS_Store")
        ]
        project_dir = extract_dest
        if len(meaningful) == 1 and meaningful[0].is_dir:
            project_dir = meaningful[0].path
            self.log.info(f"解压结果只有一个目录「{names[0]}」，作为项目目录。")
        entry = self.sftp.stat(project_dir)
        if entry is None or not entry.is_dir:
            raise BrmError(
                f"解压后未找到项目目录：{project_dir}",
                hint="请检查 zip 包内部的目录结构。",
            )
        self.log.info(f"项目目录：{project_dir}")
        return project_dir

    # -- 脚本查找 -------------------------------------------------------
    def _stage_build(self, project_dir: str) -> str:
        build_script = find_script(self.session, self.sftp, project_dir, "build.sh", log=self.log.info)
        self.log.info(f"编译脚本：{build_script}")

        self._run(f"chmod +x {quote(build_script)}", what="为 build.sh 添加执行权限")
        self.log.info("已赋予 build.sh 执行权限")

        # 编译阶段就为 stop.sh 授权：start.sh 往往会调用 ./scripts/stop.sh，
        # 若压缩包里没有携带可执行位，会导致「Permission denied」。
        self._grant_optional_script_permission(project_dir, "stop.sh")

        self.log.info("开始编译（实时输出如下）")

        command = (
            f"{env_setup_command()} cd {quote(project_dir)} && bash {quote(build_script)}"
        )
        self.log.raw("──────── 编译输出开始 ────────")

        def on_line(line: str) -> None:
            if self.cancel_event.is_set():
                raise DeployCancelledError(
                    "部署已取消。",
                    hint="远端编译进程可能仍在运行，请稍后在机器人上确认。",
                )
            self.log.raw(line)

        try:
            result = self.session.exec_stream(
                command, on_line, timeout=self._command_timeout()
            )
        except CommandTimeoutError:
            raise
        except DeployCancelledError:
            raise
        except BrmError:
            raise
        self.log.raw("──────── 编译输出结束 ────────")
        self.log.info(f"编译退出码：{result.exit_code}")

        if result.exit_code != 0:
            tail = "\n".join((result.stdout or "").strip().splitlines()[-30:])
            raise CompileFailedError(
                f"编译失败（退出码 {result.exit_code}）。",
                hint="请根据上方编译输出定位错误后重新部署。",
                detail=tail,
            )
        self.log.info("编译成功")
        return build_script

    def _grant_optional_script_permission(self, project_dir: str, script_name: str) -> None:
        """可选脚本授权：找到就 chmod +x 并校验，找不到只提示、不中断部署。"""
        try:
            script_path = find_script(
                self.session, self.sftp, project_dir, script_name, log=self.log.info
            )
        except ScriptNotFoundError:
            self.log.info(f"未找到 {script_name}，跳过执行权限授权。")
            return

        self._run(
            f"chmod +x {quote(script_path)}",
            what=f"为 {script_name} 添加执行权限",
        )
        result = self.session.exec_command(f"test -x {quote(script_path)}")
        if not result.ok:
            raise PermissionDeniedRemoteError(
                f"无法为 {script_name} 添加执行权限：{script_path}",
                hint="请检查 booster 账号对该文件的权限。",
            )
        self.log.info(f"已赋予 {script_name} 执行权限（x）：{script_path}")

    def _stage_chmod_start(self, project_dir: str) -> str:
        start_script = find_script(self.session, self.sftp, project_dir, "start.sh", log=self.log.info)
        self.log.info(f"启动脚本：{start_script}")

        self._run(f"chmod +x {quote(start_script)}", what="为 start.sh 添加执行权限")
        result = self.session.exec_command(f"test -x {quote(start_script)}")
        if not result.ok:
            raise PermissionDeniedRemoteError(
                f"无法为 start.sh 添加执行权限：{start_script}",
                hint="请检查 booster 账号对该文件的权限。",
            )
        self.log.info("已赋予 start.sh 执行权限（x）")
        return start_script

    # ------------------------------------------------------------------
    def _command_timeout(self) -> float:
        return float(getattr(self.session, "command_timeout", constants.DEFAULT_COMMAND_TIMEOUT))
