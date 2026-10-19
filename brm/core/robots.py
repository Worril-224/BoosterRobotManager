"""机器人配置与密码存储（R0 / R1）。

密码策略：
* 支持“全局默认密码”+“单台覆盖”；
* 配置文件中以 Base64 混淆保存（``b64:`` 前缀），**不是加密**，仅适用于可信本地环境；
* 代码里不出现任何密码字面量，默认密码只能由用户在界面中输入。
"""

from __future__ import annotations

import base64
import copy
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import constants
from .errors import BrmError


# ---------------------------------------------------------------------------
# 密码混淆
# ---------------------------------------------------------------------------
_B64_PREFIX = "b64:"


def obfuscate_password(plain: str) -> str:
    """Base64 混淆（仅防肩窥 / 防明文搜索，不是加密）。"""
    if not plain:
        return ""
    return _B64_PREFIX + base64.b64encode(plain.encode("utf-8")).decode("ascii")


def deobfuscate_password(stored: str) -> str:
    """还原 Base64 混淆后的密码；兼容历史明文（直接返回）。"""
    if not stored:
        return ""
    if stored.startswith(_B64_PREFIX):
        payload = stored[len(_B64_PREFIX) :]
        try:
            return base64.b64decode(payload.encode("ascii")).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return ""
    return stored


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
@dataclass
class Robot:
    """一台机器人。"""

    name: str
    host: str
    port: int = constants.DEFAULT_PORT
    username: str = constants.DEFAULT_USERNAME
    auth_type: str = "password"  # password | key
    password_b64: str = ""  # 单台覆盖密码（空 = 使用默认密码）
    key_path: str = ""
    workspace_dir: str = constants.DEFAULT_REMOTE_WORKSPACE

    # -- 密码 ---------------------------------------------------------------
    def own_password(self) -> str:
        return deobfuscate_password(self.password_b64)

    def set_own_password(self, plain: str) -> None:
        self.password_b64 = obfuscate_password(plain)

    def clear_own_password(self) -> None:
        self.password_b64 = ""

    def has_own_password(self) -> bool:
        return bool(self.password_b64)

    # -- 序列化 -------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "auth_type": self.auth_type,
            "password": self.password_b64,
            "key_path": self.key_path,
            "workspace_dir": self.workspace_dir,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Robot":
        return cls(
            name=str(data.get("name") or "").strip() or "未命名",
            host=str(data.get("host") or "").strip(),
            port=int(data.get("port") or constants.DEFAULT_PORT),
            username=str(data.get("username") or constants.DEFAULT_USERNAME).strip(),
            auth_type=str(data.get("auth_type") or "password"),
            password_b64=str(data.get("password") or ""),
            key_path=str(data.get("key_path") or ""),
            workspace_dir=str(data.get("workspace_dir") or constants.DEFAULT_REMOTE_WORKSPACE),
        )

    def display_name(self) -> str:
        return f"{self.name}"


@dataclass
class Settings:
    """全局设置。"""

    default_password_b64: str = ""
    connect_timeout: int = constants.DEFAULT_CONNECT_TIMEOUT
    command_timeout: int = constants.DEFAULT_COMMAND_TIMEOUT
    remote_home_dir: str = constants.DEFAULT_REMOTE_HOME

    def default_password(self) -> str:
        return deobfuscate_password(self.default_password_b64)

    def set_default_password(self, plain: str) -> None:
        self.default_password_b64 = obfuscate_password(plain)

    def has_default_password(self) -> bool:
        return bool(self.default_password_b64)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "default_password": self.default_password_b64,
            "connect_timeout": self.connect_timeout,
            "command_timeout": self.command_timeout,
            "remote_home_dir": self.remote_home_dir,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Settings":
        return cls(
            default_password_b64=str(data.get("default_password") or ""),
            connect_timeout=int(data.get("connect_timeout") or constants.DEFAULT_CONNECT_TIMEOUT),
            command_timeout=int(data.get("command_timeout") or constants.DEFAULT_COMMAND_TIMEOUT),
            remote_home_dir=str(data.get("remote_home_dir") or constants.DEFAULT_REMOTE_HOME),
        )


def default_robot() -> Robot:
    return Robot(
        name=constants.DEFAULT_ROBOT_NAME,
        host=constants.DEFAULT_HOST,
        port=constants.DEFAULT_PORT,
        username=constants.DEFAULT_USERNAME,
        auth_type="password",
        password_b64="",
        key_path="",
        workspace_dir=constants.DEFAULT_REMOTE_WORKSPACE,
    )


def default_config() -> Dict[str, Any]:
    return {
        "version": constants.CONFIG_VERSION,
        "_说明": (
            "Booster Robot Manager 配置文件。password 字段为 Base64 混淆存储，"
            "不是加密；留空表示使用全局默认密码。"
        ),
        "settings": Settings().to_dict(),
        "robots": [default_robot().to_dict()],
    }


# ---------------------------------------------------------------------------
# 配置仓库
# ---------------------------------------------------------------------------
class RobotStore:
    """负责 config/robots.json 的读写与密码解析。"""

    def __init__(self, config_path: Optional[Path] = None) -> None:
        self.config_path = Path(config_path) if config_path else constants.CONFIG_PATH
        self.settings = Settings()
        self.robots: List[Robot] = []

    # -- 加载 / 保存 --------------------------------------------------------
    def load(self) -> None:
        """加载配置；文件不存在时自动创建并写入默认机器人（R1 第 7 条）。"""
        if not self.config_path.exists():
            self.settings = Settings()
            self.robots = [default_robot()]
            self.save()
            return

        try:
            raw = self.config_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise BrmError(
                "配置文件读取失败。",
                hint=f"文件路径：{self.config_path}",
                detail=str(exc),
            ) from exc

        if not isinstance(data, dict):
            raise BrmError("配置文件格式错误：根节点必须是 JSON 对象。")

        self.settings = Settings.from_dict(data.get("settings") or {})
        robots_data = data.get("robots")
        if not isinstance(robots_data, list) or not robots_data:
            self.robots = [default_robot()]
            self.save()
            return
        self.robots = [Robot.from_dict(item) for item in robots_data if isinstance(item, dict)]
        if not self.robots:
            self.robots = [default_robot()]
            self.save()

    def save(self) -> None:
        """原子写入：先写临时文件再替换，避免写一半导致配置损坏。"""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": constants.CONFIG_VERSION,
            "_说明": (
                "Booster Robot Manager 配置文件。password 字段为 Base64 混淆存储，"
                "不是加密；留空表示使用全局默认密码。"
            ),
            "settings": self.settings.to_dict(),
            "robots": [robot.to_dict() for robot in self.robots],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        fd, tmp_name = tempfile.mkstemp(
            prefix=".robots-", suffix=".json", dir=str(self.config_path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(tmp_name, self.config_path)
            try:  # 尽量收紧权限：配置里含混淆后的密码
                os.chmod(self.config_path, 0o600)
            except OSError:
                pass
        finally:
            if os.path.exists(tmp_name):
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass

    # -- 机器人列表 ---------------------------------------------------------
    def robot_names(self) -> List[str]:
        return [robot.name for robot in self.robots]

    def get_robot(self, name: str) -> Optional[Robot]:
        for robot in self.robots:
            if robot.name == name:
                return robot
        return None

    def add_robot(self, robot: Robot) -> None:
        if self.get_robot(robot.name) is not None:
            raise BrmError(f"机器人名称已存在：{robot.name}")
        self.robots.append(robot)
        self.save()

    def update_robot(self, original_name: str, robot: Robot) -> None:
        for index, existing in enumerate(self.robots):
            if existing.name == original_name:
                if robot.name != original_name and self.get_robot(robot.name) is not None:
                    raise BrmError(f"机器人名称已存在：{robot.name}")
                self.robots[index] = robot
                self.save()
                return
        raise BrmError(f"未找到机器人：{original_name}")

    def remove_robot(self, name: str) -> None:
        remaining = [robot for robot in self.robots if robot.name != name]
        if len(remaining) == len(self.robots):
            raise BrmError(f"未找到机器人：{name}")
        self.robots = remaining
        self.save()

    # -- 密码 ---------------------------------------------------------------
    def resolve_password(self, robot: Robot) -> Optional[str]:
        """解析实际使用的密码：单台覆盖优先，其次全局默认，都没有则返回 None。"""
        if robot.auth_type == "key":
            return None
        own = robot.own_password()
        if own:
            return own
        default = self.settings.default_password()
        if default:
            return default
        return None

    def password_source(self, robot: Robot) -> str:
        """返回密码来源，用于界面提示：``单独设置`` / ``默认密码`` / ``未设置``。"""
        if robot.auth_type == "key":
            return "私钥登录"
        if robot.has_own_password():
            return "单独设置"
        if self.settings.has_default_password():
            return "默认密码"
        return "未设置"

    def has_any_password(self) -> bool:
        if self.settings.has_default_password():
            return True
        return any(robot.has_own_password() for robot in self.robots)

    def set_default_password(self, plain: str) -> None:
        self.settings.set_default_password(plain)
        self.save()

    def set_robot_password(self, name: str, plain: str) -> None:
        robot = self.get_robot(name)
        if robot is None:
            raise BrmError(f"未找到机器人：{name}")
        robot.set_own_password(plain)
        self.save()

    def clear_all_robot_passwords(self) -> None:
        """清除所有单台覆盖密码，让全部机器人都回到“使用默认密码”。"""
        for robot in self.robots:
            robot.clear_own_password()
        self.save()

    # -- 工具 ---------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        """返回深拷贝的配置字典（用于测试与展示）。"""
        return copy.deepcopy(
            {
                "settings": self.settings.to_dict(),
                "robots": [robot.to_dict() for robot in self.robots],
            }
        )
