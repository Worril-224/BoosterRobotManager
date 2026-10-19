"""分级错误体系。

设计目标：
1. 每种失败都能给出人话级别、可操作的中文提示，绝不把 Python 堆栈直接甩给用户；
2. **只有认证失败**才会被归类为 :class:`AuthFailedError`，超时 / 拒绝连接 /
   网络不可达 / 主机密钥异常各自独立，绝不误报成密码错误。
"""

from __future__ import annotations

import errno
import socket
from typing import Optional


class BrmError(Exception):
    """所有业务异常的基类。

    ``title`` 用于弹窗标题，``message`` 是主提示，``hint`` 是补充建议。
    """

    title = "操作失败"

    def __init__(self, message: str, *, hint: str = "", detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.detail = detail

    def user_message(self) -> str:
        """返回适合直接展示给用户的多行文本。"""
        text = self.message
        if self.hint:
            text += f"\n\n建议：{self.hint}"
        if self.detail:
            text += f"\n\n详情：{self.detail}"
        return text

    def __str__(self) -> str:  # pragma: no cover - 便于日志
        return self.user_message()


# ---------------------------------------------------------------------------
# 连接类
# ---------------------------------------------------------------------------
class AuthFailedError(BrmError):
    """账号或密码认证失败（唯一允许触发“重新输入密码”的错误）。"""

    title = "密码不正确"


class ConnectTimeoutError(BrmError):
    title = "连接超时"


class ConnectionRejectedError(BrmError):
    """远端拒绝连接（端口未监听 / SSH 服务未启动）。"""

    title = "连接被拒绝"


class NetworkUnreachableError(BrmError):
    """网络不可达或主机名无法解析。"""

    title = "网络不可达"


class HostKeyError(BrmError):
    """主机密钥异常（known_hosts 中记录的密钥与远端不一致）。"""

    title = "主机密钥异常"


class SessionNotConnectedError(BrmError):
    title = "未连接机器人"


class CommandTimeoutError(BrmError):
    title = "命令执行超时"


# ---------------------------------------------------------------------------
# 远端操作类
# ---------------------------------------------------------------------------
class RemoteCommandError(BrmError):
    title = "远端命令执行失败"

    def __init__(
        self,
        message: str,
        *,
        command: str = "",
        exit_code: Optional[int] = None,
        output: str = "",
        hint: str = "",
    ) -> None:
        detail_parts = []
        if command:
            detail_parts.append(f"命令：{command}")
        if exit_code is not None:
            detail_parts.append(f"退出码：{exit_code}")
        tail = output.strip()
        if tail:
            if len(tail) > 2000:
                tail = "……" + tail[-2000:]
            detail_parts.append(f"输出：\n{tail}")
        super().__init__(message, hint=hint, detail="\n".join(detail_parts))
        self.command = command
        self.exit_code = exit_code
        self.output = output


class PermissionDeniedRemoteError(BrmError):
    title = "权限不足"


class DiskFullError(BrmError):
    title = "远端磁盘空间不足"


class MissingRemoteCommandError(BrmError):
    title = "远端缺少依赖命令"


class ScriptNotFoundError(BrmError):
    title = "未找到脚本"


class CompileFailedError(BrmError):
    title = "编译失败"


class FileValidationError(BrmError):
    title = "文件校验未通过"


class UnsafePathError(BrmError):
    title = "危险路径已拦截"


class AlreadyRunningError(BrmError):
    title = "服务已在运行"


class NotRunningError(BrmError):
    title = "服务未运行"


class DeployCancelledError(BrmError):
    title = "部署已取消"


# ---------------------------------------------------------------------------
# 异常归类
# ---------------------------------------------------------------------------
def _errno_of(exc: BaseException) -> Optional[int]:
    value = getattr(exc, "errno", None)
    if isinstance(value, int):
        return value
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], int):
        return args[0]
    return None


def classify_connection_error(exc: BaseException, host: str = "", port: int = 0) -> BrmError:
    """把 paramiko / socket / OS 异常转换为分级的中文错误。

    注意：``paramiko.AuthenticationException`` 是唯一映射到
    :class:`AuthFailedError` 的异常类型，其余异常都不会被当成密码错误。
    """
    if isinstance(exc, BrmError):
        return exc

    target = f"{host}:{port}" if port else host

    # 延迟导入，便于在无 paramiko 的纯逻辑测试中复用本模块
    try:
        import paramiko
    except Exception:  # pragma: no cover - paramiko 是硬依赖
        paramiko = None  # type: ignore[assignment]

    if paramiko is not None:
        # NoValidConnectionsError 是 SSHException 的子类，必须先判断
        if isinstance(exc, paramiko.ssh_exception.NoValidConnectionsError):
            return ConnectionRejectedError(
                f"无法连接到 {target}。",
                hint="请确认机器人已开机、IP 正确，并且 SSH 服务（端口 22）已启动。",
            )
        if isinstance(exc, paramiko.AuthenticationException):
            return AuthFailedError(
                "机器人拒绝了账号 / 密码认证。",
                hint="请检查用户名与密码是否正确；Booster 出厂默认密码通常为 booster。",
            )
        if isinstance(exc, paramiko.BadHostKeyException):
            return HostKeyError(
                "远端主机密钥与本机 known_hosts 中记录的不一致。",
                hint=(
                    "机器人可能重装过系统。确认无误后，可执行 "
                    "ssh-keygen -R <机器人IP> 清除旧记录再连接。"
                ),
            )
        if isinstance(exc, paramiko.PasswordRequiredException):
            return AuthFailedError(
                "私钥文件已加密，需要输入私钥密码。",
                hint="请改用未加密的私钥，或在机器人配置中填写密码。",
            )
        if isinstance(exc, paramiko.SSHException):
            text = str(exc)
            lowered = text.lower()
            if "authentication" in lowered or "auth fail" in lowered:
                return AuthFailedError(
                    "机器人拒绝了账号 / 密码认证。",
                    hint="请检查用户名与密码是否正确。",
                )
            if "banner" in lowered:
                return NetworkUnreachableError(
                    f"与 {target} 的 SSH 握手失败（banner 读取异常）。",
                    hint="对方可能不是 SSH 服务，或网络质量很差，请检查 IP 与端口。",
                    detail=text,
                )
            if "host key" in lowered:
                return HostKeyError("远端主机密钥校验失败。", detail=text)
            return BrmError("SSH 协议错误。", hint="请检查机器人 SSH 服务状态。", detail=text)

    if isinstance(exc, socket.gaierror):
        return NetworkUnreachableError(
            f"无法解析主机地址：{host}。",
            hint="请检查 IP / 主机名是否填写正确。",
        )
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return ConnectTimeoutError(
            f"连接 {target} 超时。",
            hint="请检查机器人是否开机、与本机是否在同一网段、网线是否插好。",
        )
    if isinstance(exc, ConnectionRefusedError):
        return ConnectionRejectedError(
            f"{target} 拒绝连接。",
            hint="机器人 SSH 服务可能未启动，或被防火墙拦截。",
        )

    err = _errno_of(exc)
    if err in (errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EADDRNOTAVAIL):
        return NetworkUnreachableError(
            f"无法访问 {target}，网络不可达。",
            hint="请确认本机与机器人处于同一局域网，并检查 IP 是否正确。",
        )
    if err == errno.ECONNREFUSED:
        return ConnectionRejectedError(
            f"{target} 拒绝连接。",
            hint="机器人 SSH 服务可能未启动，或被防火墙拦截。",
        )
    if err == errno.ETIMEDOUT:
        return ConnectTimeoutError(f"连接 {target} 超时。")
    if err in (errno.EACCES, errno.EPERM):
        return BrmError("本机权限不足。", hint="请检查私钥文件权限（建议 chmod 600）。")

    return BrmError(
        "发生未知错误。",
        hint="请检查网络与机器人状态后重试。",
        detail=f"{type(exc).__name__}: {exc}",
    )


def classify_remote_failure(output: str, exit_code: Optional[int] = None) -> Optional[BrmError]:
    """根据远端命令输出识别常见失败原因，返回对应错误或 ``None``。

    只在命令失败（退出码非 0）时调用，用于给出比“退出码 1”更可读的提示。
    """
    text = (output or "").lower()
    if "no space left on device" in text:
        return DiskFullError(
            "远端磁盘空间不足。",
            hint="请清理机器人 /home/booster 下的旧工程或日志后重试。",
        )
    if "permission denied" in text:
        return PermissionDeniedRemoteError(
            "远端权限不足。",
            hint="请确认 booster 账号对目标目录有读写权限。",
        )
    if "unzip: command not found" in text or "unzip: not found" in text:
        return MissingRemoteCommandError(
            "远端未安装 unzip。",
            hint="请在机器人上执行：sudo apt install -y unzip",
        )
    if "not found" in text or "command not found" in text:
        return MissingRemoteCommandError(
            "远端缺少所需命令。",
            hint="请检查机器人系统环境是否完整。",
        )
    return None


def looks_like_auth_failure(text: str) -> bool:
    """从文本中识别认证失败（用于极少数需要通过 stderr 判断的场景）。"""
    lowered = (text or "").lower()
    fingerprints = (
        "permission denied (publickey",
        "permission denied (password",
        "authentication failed",
        "too many authentication failures",
    )
    return any(fp in lowered for fp in fingerprints)
