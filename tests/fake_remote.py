"""本地模拟远端机器人（供集成测试 / 截图演示使用）。

用真实的 bash、unzip、chmod、kill 等命令在一个临时沙箱目录中执行 BRM 生成的
远端命令，并把 ``/home/booster`` 映射到沙箱目录，从而在没有真实机器人、没有
SSH 服务的情况下走通「上传 → 解压 → 编译 → 授权 → 启停」全流程。

注意：这是测试替身，不代表真实 SSH 行为；真实网络 / 认证相关逻辑由
``test_errors.py`` 等单元测试覆盖。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional

from brm.core.errors import AuthFailedError
from brm.core.ssh_client import CommandResult

REMOTE_HOME = "/home/booster"
REMOTE_WORKSPACE = "/home/booster/Workspace/"


# ---------------------------------------------------------------------------
# SFTP 替身
# ---------------------------------------------------------------------------
class _FakeChannel:
    def settimeout(self, _timeout: float) -> None:  # noqa: D401 - 模拟 paramiko
        return None


class FakeSFTPAttr:
    """模拟 paramiko.SFTPAttributes。"""

    def __init__(self, filename: str, stat_result: os.stat_result) -> None:
        self.filename = filename
        self.st_mode = stat_result.st_mode
        self.st_size = stat_result.st_size
        self.st_mtime = stat_result.st_mtime


class FakeSFTP:
    def __init__(self, remote: "FakeRemote") -> None:
        self.remote = remote
        self.root = remote.root

    # -- 路径映射 ------------------------------------------------------
    def _local(self, remote_path: str) -> Path:
        if not remote_path.startswith("/"):
            raise FileNotFoundError(f"No such file: {remote_path}")
        local = self.root / remote_path.lstrip("/")
        try:
            local.resolve().relative_to(self.root.resolve())
        except ValueError as exc:  # pragma: no cover - 越权保护
            raise PermissionError(f"Permission denied: {remote_path}") from exc
        return local

    # -- paramiko 兼容方法 --------------------------------------------
    def get_channel(self) -> _FakeChannel:
        return _FakeChannel()

    def listdir_attr(self, path: str) -> List[FakeSFTPAttr]:
        local = self._local(path)
        if not local.exists():
            raise FileNotFoundError(f"No such file: {path}")
        if not local.is_dir():
            raise NotADirectoryError(f"Not a directory: {path}")
        return [FakeSFTPAttr(entry.name, entry.stat()) for entry in local.iterdir()]

    def stat(self, path: str) -> FakeSFTPAttr:
        local = self._local(path)
        if not local.exists():
            raise FileNotFoundError(f"No such file: {path}")
        return FakeSFTPAttr(local.name, local.stat())

    def put(self, local_path: str, remote_path: str, callback=None, confirm: bool = True) -> None:
        source = Path(local_path)
        target = self._local(remote_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        total = source.stat().st_size
        copied = 0
        with source.open("rb") as reader, target.open("wb") as writer:
            while True:
                chunk = reader.read(32768)
                if not chunk:
                    break
                writer.write(chunk)
                copied += len(chunk)
                if callback is not None:
                    callback(copied, total)
        if total == 0 and callback is not None:
            callback(0, 0)

    def get(self, remote_path: str, local_path: str, callback=None) -> None:
        source = self._local(remote_path)
        target = Path(local_path)
        total = source.stat().st_size
        copied = 0
        with source.open("rb") as reader, target.open("wb") as writer:
            while True:
                chunk = reader.read(32768)
                if not chunk:
                    break
                writer.write(chunk)
                copied += len(chunk)
                if callback is not None:
                    callback(copied, total)

    def remove(self, path: str) -> None:
        local = self._local(path)
        if not local.exists():
            raise FileNotFoundError(f"No such file: {path}")
        if local.is_dir():
            raise IsADirectoryError(f"Is a directory: {path}")
        local.unlink()

    def rmdir(self, path: str) -> None:
        local = self._local(path)
        if not local.exists():
            raise FileNotFoundError(f"No such file: {path}")
        local.rmdir()

    def mkdir(self, path: str) -> None:
        local = self._local(path)
        local.mkdir(exist_ok=False)

    def open(self, path: str, mode: str = "r"):
        local = self._local(path)
        if not local.exists():
            raise FileNotFoundError(f"No such file: {path}")
        return local.open("rb" if "b" in mode else "r")

    def close(self) -> None:
        return None


# ---------------------------------------------------------------------------
# SSH 会话替身
# ---------------------------------------------------------------------------
class FakeRemote:
    """模拟一台已开机的 Ubuntu 机器人。"""

    def __init__(self, root: Path, password: str = "fake-robot-password") -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.home = self.root / "home" / "booster"
        self.workspace = self.home / "Workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._bin = self.root / "_bin"
        self._bin.mkdir(parents=True, exist_ok=True)
        self._write_setsid_shim()

        self.password = password
        self.connected = False
        self.host = "127.0.0.1"
        self.port = 2222
        self.username = "booster"
        self.connect_timeout = 10
        self.command_timeout = 60
        self.commands: List[str] = []
        self.missing_unzip = False
        self._lock = threading.Lock()

        self._env: Dict[str, str] = dict(os.environ)
        self._env["PATH"] = f"{self._bin}{os.pathsep}" + self._env.get("PATH", "")

    # -- 环境准备 ------------------------------------------------------
    def _write_setsid_shim(self) -> None:
        """macOS 没有 setsid，提供一个等价 shim，让启动命令可以真实跑起来。

        用 bash 包一层而不直接把 python 写进 shebang，避免解释器路径包含空格。
        """
        shim = self._bin / "setsid"
        shim.write_text(
            "#!/bin/bash\n"
            f'exec "{sys.executable}" -c '
            '"import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])" "$@"\n',
            encoding="utf-8",
        )
        shim.chmod(0o755)

    # -- 连接 ----------------------------------------------------------
    def connect(self, password: Optional[str] = None) -> None:
        if not password or password != self.password:
            raise AuthFailedError(
                "机器人拒绝了账号 / 密码认证。",
                hint="（模拟）密码不正确。",
            )
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def open_sftp(self) -> FakeSFTP:
        if not self.connected:
            raise RuntimeError("会话未连接")
        return FakeSFTP(self)

    # -- 命令执行 ------------------------------------------------------
    def _translate(self, command: str) -> str:
        return command.replace(REMOTE_HOME, str(self.home))

    def _untranslate(self, text: str) -> str:
        """把本地沙箱路径还原成远端路径，模拟真实 SSH 的返回内容。"""
        return text.replace(str(self.home), REMOTE_HOME)

    def _run(self, command: str, input_text: Optional[str] = None) -> CommandResult:
        translated = self._translate(command)
        with self._lock:
            completed = subprocess.run(
                ["bash", "-c", translated],
                cwd=str(self.home),
                env=self._env,
                input=input_text.encode("utf-8") if input_text is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
            )
        return CommandResult(
            command=command,
            exit_code=completed.returncode,
            stdout=self._untranslate(completed.stdout.decode("utf-8", errors="replace")),
            stderr=self._untranslate(completed.stderr.decode("utf-8", errors="replace")),
        )

    def exec_command(self, command: str, timeout: Optional[float] = None) -> CommandResult:
        self.commands.append(command)
        if self.missing_unzip and command.strip() == "command -v unzip":
            return CommandResult(command=command, exit_code=1, stdout="", stderr="")
        return self._run(command)

    def exec_command_with_input(
        self,
        command: str,
        input_text: str,
        timeout: Optional[float] = None,
    ) -> CommandResult:
        """模拟向远端命令的标准输入写入内容（用于自动输入密码）。"""
        self.commands.append(command)
        return self._run(command, input_text=input_text)

    def exec_stream(
        self,
        command: str,
        on_line: Callable[[str], None],
        timeout: Optional[float] = None,
    ) -> CommandResult:
        self.commands.append(command)
        translated = self._translate(command)
        process = subprocess.Popen(
            ["bash", "-c", translated],
            cwd=str(self.home),
            env=self._env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        chunks: List[str] = []
        assert process.stdout is not None
        for raw in iter(process.stdout.readline, b""):
            line = self._untranslate(
                raw.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")
            )
            chunks.append(line + "\n")
            on_line(line)
        process.wait(timeout=timeout or 120)
        return CommandResult(
            command=command,
            exit_code=process.returncode,
            stdout="".join(chunks),
        )

    # -- 断言辅助 ------------------------------------------------------
    def local_path(self, remote_path: str) -> Path:
        return self.home / remote_path[len(REMOTE_HOME) :].lstrip("/")

    def exists(self, remote_path: str) -> bool:
        return self.local_path(remote_path).exists()

    def read_text(self, remote_path: str) -> str:
        return self.local_path(remote_path).read_text(encoding="utf-8")

    def write_text(self, remote_path: str, text: str) -> None:
        target = self.local_path(remote_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def cleanup(self) -> None:
        self.connected = False
