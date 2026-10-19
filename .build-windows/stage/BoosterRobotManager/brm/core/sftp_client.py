"""SFTP 文件操作封装（R2 / R3 / R4）。

对 paramiko 的 SFTP 调用做统一异常翻译，并提供树形浏览所需的属性整理。
"""

from __future__ import annotations

import os
import socket
import stat as stat_module
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Callable, List, Optional, Tuple

from .errors import (
    BrmError,
    DiskFullError,
    PermissionDeniedRemoteError,
)
from .util import format_mtime, human_size, mode_text


@dataclass
class RemoteEntry:
    """远端文件 / 目录条目。"""

    name: str
    path: str
    is_dir: bool
    size: int = 0
    mtime: Optional[float] = None
    mode: Optional[int] = None

    @property
    def size_text(self) -> str:
        return "-" if self.is_dir else human_size(self.size)

    @property
    def mtime_text(self) -> str:
        return format_mtime(self.mtime)

    @property
    def mode_text(self) -> str:
        return mode_text(self.mode)


ProgressCallback = Callable[[int, int], None]
"""进度回调：(已传输字节, 总字节)。"""


def _translate_error(exc: BaseException, target: str) -> BrmError:
    """把 SFTP 异常转换成可读的中文错误。"""
    text = str(exc)
    lowered = text.lower()
    if "no space left" in lowered:
        return DiskFullError(
            "远端磁盘空间不足。",
            hint="请清理机器人 /home/booster 下的旧工程或日志后重试。",
            detail=text,
        )
    if "permission denied" in lowered:
        return PermissionDeniedRemoteError(
            f"远端权限不足：{target}",
            hint="请确认 booster 账号对目标路径有读写权限。",
            detail=text,
        )
    if isinstance(exc, FileNotFoundError) or "no such file" in lowered:
        return BrmError(
            f"远端路径不存在：{target}",
            hint="请刷新目录后重试。",
        )
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return BrmError(
            "SFTP 操作超时。",
            hint="请检查网络连接后重试。",
            detail=text,
        )
    return BrmError(f"远端文件操作失败：{target}", detail=text)


class SFTPClient:
    """基于已有 SSH 会话的 SFTP 操作封装（按需建立、会话内复用）。"""

    def __init__(self, session) -> None:
        self._session = session
        self._sftp = None

    # ------------------------------------------------------------------
    def _client(self):
        if self._sftp is None:
            self._sftp = self._session.open_sftp()
            try:
                channel = self._sftp.get_channel()
                timeout = getattr(self._session, "command_timeout", 600)
                channel.settimeout(float(timeout))
            except Exception:
                pass
        return self._sftp

    def close(self) -> None:
        sftp, self._sftp = self._sftp, None
        if sftp is not None:
            try:
                sftp.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    def listdir(self, path: str) -> List[RemoteEntry]:
        """列出目录内容：目录在前，其次按名称排序。"""
        try:
            attrs = self._client().listdir_attr(path)
        except Exception as exc:  # noqa: BLE001
            raise _translate_error(exc, path) from exc

        entries: List[RemoteEntry] = []
        for attr in attrs:
            name = getattr(attr, "filename", "")
            if name in ("", ".", ".."):
                continue
            mode = getattr(attr, "st_mode", None)
            is_dir = bool(mode is not None and stat_module.S_ISDIR(mode))
            entries.append(
                RemoteEntry(
                    name=name,
                    path=str(PurePosixPath(path) / name),
                    is_dir=is_dir,
                    size=int(getattr(attr, "st_size", 0) or 0),
                    mtime=getattr(attr, "st_mtime", None),
                    mode=mode,
                )
            )
        entries.sort(key=lambda item: (not item.is_dir, item.name.lower()))
        return entries

    def listdir_names(self, path: str) -> List[str]:
        return [entry.name for entry in self.listdir(path)]

    def stat(self, path: str) -> Optional[RemoteEntry]:
        try:
            attr = self._client().stat(path)
        except Exception as exc:  # noqa: BLE001
            lowered = str(exc).lower()
            if isinstance(exc, FileNotFoundError) or "no such file" in lowered:
                return None
            raise _translate_error(exc, path) from exc
        mode = getattr(attr, "st_mode", None)
        return RemoteEntry(
            name=PurePosixPath(path).name,
            path=path,
            is_dir=bool(mode is not None and stat_module.S_ISDIR(mode)),
            size=int(getattr(attr, "st_size", 0) or 0),
            mtime=getattr(attr, "st_mtime", None),
            mode=mode,
        )

    def exists(self, path: str) -> bool:
        return self.stat(path) is not None

    # ------------------------------------------------------------------
    def read_text(self, path: str, max_bytes: int) -> Tuple[str, bool]:
        """读取文本文件内容，返回 (文本, 是否被截断)。"""
        try:
            handle = self._client().open(path, "rb")
        except Exception as exc:  # noqa: BLE001
            raise _translate_error(exc, path) from exc
        try:
            data = handle.read(max_bytes + 1)
        except Exception as exc:  # noqa: BLE001
            raise _translate_error(exc, path) from exc
        finally:
            try:
                handle.close()
            except Exception:
                pass

        truncated = len(data) > max_bytes
        data = data[:max_bytes]
        if b"\x00" in data:
            raise BrmError(
                "该文件看起来是二进制文件，暂不支持预览。",
                hint="可以下载到本地后再查看。",
            )
        return data.decode("utf-8", errors="replace"), truncated

    # ------------------------------------------------------------------
    def upload(
        self,
        local_path: str,
        remote_path: str,
        progress: Optional[ProgressCallback] = None,
    ) -> int:
        """上传本地文件到远端，返回文件字节数。"""
        callback = None
        if progress is not None:
            def _report(transferred: int, total: int) -> None:
                try:
                    progress(int(transferred), int(total))
                except Exception:
                    pass

            callback = _report

        try:
            self._client().put(str(local_path), remote_path, callback=callback, confirm=True)
            entry = self.stat(remote_path)
        except BrmError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _translate_error(exc, remote_path) from exc
        if entry is not None:
            return entry.size
        try:
            return os.path.getsize(local_path)
        except OSError:
            return 0

    def download(
        self,
        remote_path: str,
        local_path: str,
        progress: Optional[ProgressCallback] = None,
    ) -> int:
        callback = None
        if progress is not None:
            def _report(transferred: int, total: int) -> None:
                try:
                    progress(int(transferred), int(total))
                except Exception:
                    pass

            callback = _report

        try:
            self._client().get(remote_path, str(local_path), callback=callback)
        except Exception as exc:  # noqa: BLE001
            raise _translate_error(exc, remote_path) from exc
        try:
            return os.path.getsize(local_path)
        except OSError:
            return 0

    # ------------------------------------------------------------------
    def mkdir(self, path: str) -> None:
        try:
            self._client().mkdir(path)
        except Exception as exc:  # noqa: BLE001
            lowered = str(exc).lower()
            if "failure" in lowered or "exists" in lowered or isinstance(exc, FileExistsError):
                return
            raise _translate_error(exc, path) from exc

    def mkdir_p(self, path: str) -> None:
        """逐级创建目录（已存在则忽略）。"""
        current = ""
        for part in PurePosixPath(path).parts:
            if part == "/":
                current = "/"
                continue
            current = str(PurePosixPath(current) / part)
            if not self.exists(current):
                self.mkdir(current)

    def remove_file(self, path: str) -> None:
        try:
            self._client().remove(path)
        except Exception as exc:  # noqa: BLE001
            raise _translate_error(exc, path) from exc

    def remove_tree(self, path: str) -> None:
        """递归删除目录。"""
        try:
            for entry in self.listdir(path):
                if entry.is_dir:
                    self.remove_tree(entry.path)
                else:
                    self.remove_file(entry.path)
            self._client().rmdir(path)
        except BrmError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _translate_error(exc, path) from exc

    # ------------------------------------------------------------------
    def read_bytes(self, path: str, max_bytes: int) -> bytes:
        try:
            handle = self._client().open(path, "rb")
        except Exception as exc:  # noqa: BLE001
            raise _translate_error(exc, path) from exc
        try:
            return handle.read(max_bytes)
        finally:
            try:
                handle.close()
            except Exception:
                pass
