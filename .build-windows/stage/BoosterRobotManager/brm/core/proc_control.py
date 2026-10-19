"""start.sh 运行控制（R8）：启动 / 停止 / 状态 / 日志。

* 启动：``setsid`` + ``nohup`` 后台运行，PID 与日志写入 Workspace；
* 停止：``kill -TERM -<pid>`` 杀进程组，5 秒后仍在则 ``kill -KILL -<pid>``；
  PID 文件缺失时用 ``pkill -f <项目目录>/scripts/start.sh`` 兜底；
* 状态：PID 文件存在且 ``kill -0 <pid>`` 成功 => 运行中。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

from .. import constants
from .errors import (
    AlreadyRunningError,
    BrmError,
    CommandTimeoutError,
    RemoteCommandError,
    SessionNotConnectedError,
)
from .remote_env import env_setup_command
from .util import normalize_remote_dir, posix_join, quote


@dataclass
class ProcStatus:
    """服务状态。"""

    project_name: str
    running: bool
    pid: Optional[int] = None
    message: str = "未运行"

    @property
    def text(self) -> str:
        return self.message

    @property
    def dot(self) -> str:
        return "●"


class ProcControl:
    def __init__(
        self,
        session,
        workspace_dir: str,
        log: Optional[Callable[[str], None]] = None,
        tail_lines: int = constants.START_LOG_TAIL_LINES,
    ) -> None:
        self.session = session
        self.workspace_dir = normalize_remote_dir(workspace_dir)
        self.log = log or (lambda message: None)
        self.tail_lines = int(tail_lines)

    # ------------------------------------------------------------------
    # 路径约定
    # ------------------------------------------------------------------
    def pid_file(self, project_name: str) -> str:
        return posix_join(self.workspace_dir, f"{project_name}.start.pid")

    def log_file(self, project_name: str) -> str:
        return posix_join(self.workspace_dir, f"{project_name}.start.log")

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    def _count_project_processes(self, project_dir: str) -> int:
        """统计工作目录为项目目录的进程数（用于识别 start.sh 拉起后自身退出的服务）。

        start.sh 往往只是启动 vision / brain / game_controller 等后台节点后退出，
        PID 文件并不能代表服务是否在运行，因此用 /proc/<pid>/cwd 判断。
        """
        command = (
            "cnt=0; "
            "for p in /proc/[0-9]*; do "
            '[ -L "$p/cwd" ] || continue; '
            't=$(readlink "$p/cwd" 2>/dev/null) || continue; '
            f'[ "$t" = {quote(project_dir)} ] || continue; '
            'c=$(cat "$p/comm" 2>/dev/null); '
            'case "$c" in bash|sh|dash|zsh|fish) continue;; esac; '
            'cmd=$(tr "\\0" " " < "$p/cmdline" 2>/dev/null); '
            'case "$cmd" in *ros2cli.daemon*|*ros2-daemon*) continue;; esac; '
            "cnt=$((cnt+1)); "
            'done; echo "COUNT:$cnt"'
        )
        result = self.session.exec_command(command)
        if not result.ok:
            return 0
        for line in (result.stdout or "").splitlines():
            if line.startswith("COUNT:"):
                digits = line.split(":", 1)[1].strip()
                if digits.isdigit():
                    return int(digits)
        return 0

    def status(self, project_name: str, project_dir: Optional[str] = None) -> ProcStatus:
        pid_file = self.pid_file(project_name)
        command = (
            f"if [ -f {quote(pid_file)} ]; then "
            f"pid=$(cat {quote(pid_file)} 2>/dev/null | tr -d '[:space:]'); "
            'if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then '
            'echo "RUNNING:$pid"; else echo "STALE:$pid"; fi; '
            'else echo "NOPID"; fi'
        )
        result = self.session.exec_command(command)
        if not result.ok:
            raise RemoteCommandError(
                "查询服务状态失败。",
                command=command,
                exit_code=result.exit_code,
                output=result.output,
            )

        text = (result.stdout or "").strip().splitlines()
        line = text[-1] if text else "NOPID"
        if line.startswith("RUNNING:"):
            pid = self._parse_pid(line.split(":", 1)[1])
            return ProcStatus(
                project_name=project_name,
                running=True,
                pid=pid,
                message=f"运行中（PID {pid}）" if pid else "运行中",
            )
        if line.startswith("STALE:"):
            pid = self._parse_pid(line.split(":", 1)[1])
            # PID 记录已失效：start.sh 可能是一次性脚本，改用项目进程判断
            if project_dir:
                process_count = self._count_project_processes(project_dir)
                if process_count > 0:
                    return ProcStatus(
                        project_name=project_name,
                        running=True,
                        pid=None,
                        message=f"运行中（检测到 {process_count} 个项目进程）",
                    )
            return ProcStatus(
                project_name=project_name,
                running=False,
                pid=pid,
                message=f"未运行（PID {pid} 记录已失效）",
            )
        if project_dir:
            process_count = self._count_project_processes(project_dir)
            if process_count > 0:
                return ProcStatus(
                    project_name=project_name,
                    running=True,
                    pid=None,
                    message=f"运行中（检测到 {process_count} 个项目进程）",
                )
        return ProcStatus(project_name=project_name, running=False, message="未运行")

    @staticmethod
    def _parse_pid(text: str) -> Optional[int]:
        digits = "".join(ch for ch in (text or "") if ch.isdigit())
        return int(digits) if digits else None

    def is_alive(self, pid: int) -> bool:
        result = self.session.exec_command(f"kill -0 {int(pid)} 2>/dev/null")
        return result.ok

    # ------------------------------------------------------------------
    # 启动
    # ------------------------------------------------------------------
    def _ensure_script_permissions(self, project_dir: str) -> None:
        """启动前确保工程内 start.sh / build.sh / stop.sh 具备执行权限。

        有些 zip 没有携带可执行位，start.sh 里又会直接执行 ``./scripts/stop.sh``；
        这里在启动前由程序自动补权限，不需要人工登录机器人执行 chmod。
        """
        conditions = " -o ".join(
            f"-name {quote(name)}" for name in ("start.sh", "build.sh", "stop.sh")
        )
        command = (
            f"find {quote(project_dir)} -type f \\( {conditions} \\) "
            f"-not -path '*/__MACOSX/*' -exec chmod +x {{}} + 2>/dev/null; true"
        )
        result = self.session.exec_command(command)
        if result.ok:
            self.log("已确保 start.sh / build.sh / stop.sh 具备执行权限")
        else:
            self.log("提示：自动补充脚本执行权限失败（脚本可能不存在，可忽略）")

    def _build_password_injection(self, password: str) -> tuple[str, str]:
        """构造「不写文件」的密码注入片段。

        做法：在远端 shell 里定义并导出 ``sudo`` / ``systemctl`` 两个 **shell 函数**，
        让 start.sh / stop.sh 里的同名命令优先命中函数：

        * ``sudo``      → 用 ``printf 密码 | sudo -S`` 从标准输入读密码；
        * ``systemctl`` → 带 ``--user`` 原样执行，系统级调用转成 ``sudo -S systemctl``。

        函数与密码只存在于本次 SSH 会话的进程环境中，**不会在机器人上创建或修改
        任何辅助脚本文件**；脚本结束后立即 unset。

        返回 ``(shell 前置片段, 需要写入远端 stdin 的密码文本)``。
        """
        count = max(1, int(constants.START_PASSWORD_INPUT_COUNT))
        read_statements = "; ".join(
            f"IFS= read -r __BRM_P{index}" for index in range(1, count + 1)
        )
        setup = (
            f"{read_statements}; "
            'BRM_SUDO_PASSWORD="$__BRM_P1"; '
            "sudo() { printf '%s\\n' \"$BRM_SUDO_PASSWORD\" | "
            '/usr/bin/sudo -S -p "" "$@"; }; '
            "systemctl() { for __brm_arg in \"$@\"; do "
            'if [ "$__brm_arg" = "--user" ]; then /usr/bin/systemctl "$@"; return; fi; '
            "done; printf '%s\\n' \"$BRM_SUDO_PASSWORD\" | "
            '/usr/bin/sudo -S -p "" /usr/bin/systemctl "$@"; }; '
            "export BRM_SUDO_PASSWORD; export -f sudo systemctl; "
        )
        input_text = "".join(f"{password}\n" for _ in range(count))
        return setup, input_text

    def _password_cleanup(self) -> str:
        return (
            "unset __BRM_P1 __BRM_P2; unset BRM_SUDO_PASSWORD; "
            "unset -f sudo systemctl; "
        )

    def start(
        self,
        project_name: str,
        project_dir: str,
        start_script: str,
        wait_seconds: float = 8.0,
        password: str = "",
    ) -> ProcStatus:
        """启动服务。

        ``password`` 不为空时：会在远端 shell 中注入 sudo / systemctl 函数处理密码，
        并把 ``START_PASSWORD_INPUT_COUNT`` 份密码写入 start.sh 的标准输入；
        整个过程不写机器人文件，密码也不会出现在命令行参数或日志中。
        """
        current = self.status(project_name, project_dir)
        if current.running:
            detail = f"PID {current.pid}" if current.pid else current.message
            raise AlreadyRunningError(
                f"服务已在运行（{detail}），未重复启动。",
                hint="如需重启，请先点击「停止」，再点击「启动」。",
            )

        self._ensure_script_permissions(project_dir)

        pid_file = self.pid_file(project_name)
        log_file = self.log_file(project_name)
        inner = (
            f"echo $$ > {quote(pid_file)}; "
            f"{env_setup_command()}exec bash {quote(start_script)}"
        )
        # 内层命令用双引号包裹，需转义 shell 特殊字符；$$ 要保留给内层 shell 展开
        inner_quoted = (
            inner.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("$", "\\$")
            .replace("`", "\\`")
        )

        count = max(1, int(constants.START_PASSWORD_INPUT_COUNT))
        if password:
            # 密码只存在于本次 SSH 会话的进程环境中，不写机器人文件；
            # 同时把 count 份密码通过管道写入 start.sh 的标准输入，
            # 供脚本自身 read 密码使用。
            self.log(
                f"正在向 start.sh 注入密码处理（sudo / systemctl，共 {count} 遍，"
                "日志中显示为 ******，不写入机器人文件）"
            )
            setup, input_text = self._build_password_injection(password)
            printf_args = " ".join(
                f'"$__BRM_P{index}"' for index in range(1, count + 1)
            )
            printf_format = "".join("%s\\n" for _ in range(count))
            command = (
                f"{setup}"
                f"cd {quote(project_dir)} && "
                f"(printf '{printf_format}' {printf_args} | "
                f"nohup setsid bash -c \"{inner_quoted}\" >> {quote(log_file)} 2>&1 &) "
                f">/dev/null 2>&1; "
                f"{self._password_cleanup()}"
            )
            result = self.session.exec_command_with_input(command, input_text)
        else:
            # 用子 shell 把后台启动包起来并重定向整个子 shell 的输出：
            # 否则后台任务会继承 SSH 通道的 stdout，导致 exec_command 一直等到进程退出。
            command = (
                f"cd {quote(project_dir)} && "
                f"(nohup setsid bash -c \"{inner_quoted}\" >> {quote(log_file)} 2>&1 &) "
                f">/dev/null 2>&1"
            )
            self.log("执行启动命令（setsid + nohup 后台运行）…")
            result = self.session.exec_command(command)

        if not result.ok:
            raise RemoteCommandError(
                "启动命令执行失败。",
                command="setsid + nohup 启动 start.sh",
                exit_code=result.exit_code,
                output=result.output,
                hint="请检查项目目录与 start.sh 是否存在、是否有执行权限。",
            )

        deadline = time.monotonic() + max(1.0, float(wait_seconds))
        while time.monotonic() < deadline:
            time.sleep(0.4)
            status = self.status(project_name, project_dir)
            if status.running:
                self.log(f"服务已启动：{status.message}")
                detail = self._ps_detail(status.pid)
                if detail:
                    self.log(f"远端进程：{detail}")
                return status

        tail = self.tail_log(project_name, lines=40).strip()
        raise BrmError(
            "启动失败：start.sh 进程未能保持运行。",
            hint="请查看启动日志（start.log）中的报错信息后重试。",
            detail=tail or "（start.log 暂无内容）",
        )

    def _ps_detail(self, pid: Optional[int]) -> str:
        if not pid:
            return ""
        try:
            result = self.session.exec_command(f"ps -p {int(pid)} -o pid=,args= 2>/dev/null")
        except BrmError:
            return ""
        return (result.stdout or "").strip()

    # ------------------------------------------------------------------
    # 停止
    # ------------------------------------------------------------------
    STOP_SCRIPT_NAMES = ("scripts/stop.sh", "script/stop.sh")

    def _locate_stop_script(self, project_dir: str) -> Optional[str]:
        """定位项目的停止脚本（scripts/stop.sh 优先，兼容 script/ 与递归唯一）。"""
        for relative in self.STOP_SCRIPT_NAMES:
            candidate = posix_join(project_dir, relative)
            result = self.session.exec_command(f"test -f {quote(candidate)}")
            if result.ok:
                return candidate

        result = self.session.exec_command(
            f"find {quote(project_dir)} -type f -name stop.sh 2>/dev/null"
        )
        paths = [
            line.strip()
            for line in (result.stdout or "").splitlines()
            if line.strip() and "/__MACOSX/" not in line
        ]
        if len(paths) == 1:
            return paths[0]
        if len(paths) > 1:
            self.log(
                "找到多个 stop.sh，无法确定使用哪一个，将改用 PID 方式停止："
                + "、".join(paths)
            )
        return None

    def _stop_by_script(
        self,
        project_name: str,
        project_dir: str,
        stop_script: str,
        password: str,
    ) -> ProcStatus:
        """优先通过项目自带的 stop.sh 停止服务（ROS 多节点工程通常需要这样停）。"""
        count = max(1, int(constants.START_PASSWORD_INPUT_COUNT))
        self.log(f"检测到项目停止脚本：{stop_script}")
        self.log("正在通过 stop.sh 停止服务…")

        if password:
            self.log(
                f"正在向 stop.sh 注入密码处理（sudo / systemctl，共 {count} 遍，"
                "日志中显示为 ******，不写入机器人文件）"
            )
            setup, input_text = self._build_password_injection(password)
            printf_args = " ".join(
                f'"$__BRM_P{index}"' for index in range(1, count + 1)
            )
            printf_format = "".join("%s\\n" for _ in range(count))
            command = (
                f"{env_setup_command()}"
                f"{setup}"
                f"cd {quote(project_dir)} && "
                f"(printf '{printf_format}' {printf_args} | bash {quote(stop_script)}); "
                f"{self._password_cleanup()}"
            )
            result = self.session.exec_command_with_input(command, input_text)
        else:
            command = (
                f"{env_setup_command()} "
                f"cd {quote(project_dir)} && bash {quote(stop_script)}"
            )
            result = self.session.exec_command(command)

        for line in (result.output or "").splitlines():
            if line.strip():
                self.log(f"  {line}")

        # 停止脚本执行完后清理 PID 记录，避免状态一直显示“记录已失效”
        self.session.exec_command(f"rm -f {quote(self.pid_file(project_name))}")
        if result.exit_code != 0:
            self.log(
                f"stop.sh 退出码 {result.exit_code}（部分进程可能本来就不存在，可忽略）"
            )
        self.log("已执行 stop.sh")

        # 给后台进程一点退出时间，然后按项目进程真实判断是否停干净
        time.sleep(1.0)
        final = self.status(project_name, project_dir)
        if final.running:
            self.log(f"stop.sh 已执行，但仍检测到进程在运行：{final.message}")
            return final
        self.log("已通过 stop.sh 停止，未检测到项目进程")
        return ProcStatus(
            project_name=project_name,
            running=False,
            message="已通过 stop.sh 停止",
        )

    def stop(
        self,
        project_name: str,
        project_dir: Optional[str] = None,
        wait_seconds: float = 5.0,
        password: str = "",
    ) -> ProcStatus:
        # 工程自带 stop.sh 时优先用它：start.sh 往往只是拉起多个 ROS 节点后退出，
        # PID 文件无法代表真实的服务进程，只有 stop.sh 知道该停哪些程序。
        if project_dir:
            stop_script = self._locate_stop_script(project_dir)
            if stop_script:
                return self._stop_by_script(project_name, project_dir, stop_script, password)

        pid_file = self.pid_file(project_name)
        status = self.status(project_name, project_dir)
        pid = status.pid

        if not status.running or pid is None:
            # PID 文件缺失 / 已失效 / 只检测到项目进程：清理残留记录，并用 pkill 兜底
            if pid is not None:
                self.session.exec_command(f"rm -f {quote(pid_file)}")
            self._pkill_fallback(project_name, project_dir)
            final = self.status(project_name, project_dir)
            if final.running:
                raise BrmError(
                    "停止失败：仍检测到项目进程在运行。",
                    hint=(
                        "该工程没有可用的 stop.sh，BRM 无法判断需要结束哪些节点；"
                        "请补充 scripts/stop.sh，或在机器人上手动停止。"
                    ),
                )
            self.log("服务当前未运行")
            return final

        assert pid is not None
        self.log(f"正在停止服务（PID {pid}）…")
        result = self.session.exec_command(f"kill -TERM -{int(pid)} 2>/dev/null")
        if not result.ok:
            result = self.session.exec_command(f"kill -TERM {int(pid)} 2>/dev/null")
            if not result.ok:
                self.log("kill -TERM 未生效，进程可能已退出")

        deadline = time.monotonic() + max(1.0, float(wait_seconds))
        alive = True
        while time.monotonic() < deadline:
            time.sleep(0.5)
            if not self.is_alive(pid):
                alive = False
                break
        if alive:
            self.log("进程 5 秒内未退出，改用 kill -KILL 强制结束")
            self.session.exec_command(f"kill -KILL -{int(pid)} 2>/dev/null")
            self.session.exec_command(f"kill -KILL {int(pid)} 2>/dev/null")
            time.sleep(0.5)
            alive = self.is_alive(pid)

        if alive:
            raise BrmError(
                f"停止失败：PID {pid} 仍然存活。",
                hint="该进程可能处于不可中断状态，请在机器人上手动处理。",
            )

        self.session.exec_command(f"rm -f {quote(pid_file)}")
        self.log("服务已停止")
        return self.status(project_name)

    def _pkill_fallback(self, project_name: str, project_dir: Optional[str]) -> None:
        if not project_dir:
            return
        marker = posix_join(project_dir, "script", "start.sh")
        command = f"pkill -f {quote(marker)} 2>/dev/null"
        result = self.session.exec_command(command)
        if result.exit_code == 0:
            self.log(f"PID 文件缺失，已通过 pkill 结束匹配进程：{marker}")
        elif result.exit_code == 1:
            self.log(f"PID 文件缺失，pkill 未找到匹配进程：{marker}")
        else:
            self.log(f"pkill 兜底执行失败（退出码 {result.exit_code}）：{result.output.strip()}")

    # ------------------------------------------------------------------
    # 启动日志
    # ------------------------------------------------------------------
    def tail_log(self, project_name: str, lines: Optional[int] = None) -> str:
        log_file = self.log_file(project_name)
        count = int(lines if lines is not None else self.tail_lines)
        command = (
            f"if [ -f {quote(log_file)} ]; then tail -n {count} {quote(log_file)}; "
            "else echo '__BRM_NO_LOG__'; fi"
        )
        try:
            result = self.session.exec_command(command)
        except CommandTimeoutError:
            raise
        except SessionNotConnectedError:
            raise
        if not result.ok:
            raise RemoteCommandError(
                "读取启动日志失败。",
                command="tail start.log",
                exit_code=result.exit_code,
                output=result.output,
            )
        text = result.stdout or ""
        if "__BRM_NO_LOG__" in text:
            return ""
        return text
