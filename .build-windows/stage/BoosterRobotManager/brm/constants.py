"""全局常量与默认值。

所有“机器人侧约定”的默认值都集中在这里，并且可以在 config/robots.json
的 settings 段中覆盖，业务逻辑中不允许再硬编码这些字面量。
"""

from __future__ import annotations

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
IS_FROZEN = bool(getattr(sys, "frozen", False))

_PKG_DIR = Path(__file__).resolve().parent


def _resolve_app_root() -> Path:
    """源码运行时用包目录；PyInstaller 打包后优先使用可执行文件旁边 / Resources 目录。"""
    if not IS_FROZEN:
        return _PKG_DIR
    exe_dir = Path(sys.executable).resolve().parent
    candidates = [exe_dir, exe_dir.parent / "Resources"]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass))
    for candidate in candidates:
        try:
            if (candidate / "config").is_dir():
                return candidate
        except OSError:  # pragma: no cover - 极端文件系统错误
            continue
    return exe_dir


APP_ROOT = _resolve_app_root()
CONFIG_DIR = APP_ROOT / "config"
CONFIG_PATH = CONFIG_DIR / "robots.json"
LOG_DIR = APP_ROOT / "logs"

# ---------------------------------------------------------------------------
# 机器人侧默认约定
# ---------------------------------------------------------------------------
DEFAULT_ROBOT_NAME = "107"
DEFAULT_HOST = "192.168.1.107"
DEFAULT_PORT = 22
DEFAULT_USERNAME = "booster"
DEFAULT_REMOTE_HOME = "/home/booster/"
DEFAULT_REMOTE_WORKSPACE = "/home/booster/Workspace/"

DEFAULT_CONNECT_TIMEOUT = 10  # 秒
DEFAULT_COMMAND_TIMEOUT = 600  # 秒

# 运行 start.sh 时，自动向它的标准输入写入密码的次数
# （部分启动脚本会要求连续输入两遍密码，且密码与默认密码一致）
START_PASSWORD_INPUT_COUNT = 2

# ---------------------------------------------------------------------------
# 交互与校验相关
# ---------------------------------------------------------------------------
PASSWORD_PLACEHOLDER = "Booster 出厂默认密码通常为 booster，请以实际为准"
PASSWORD_MASK = "******"
MAX_PREVIEW_BYTES = 200 * 1024  # 文本预览最大 200 KB
START_LOG_TAIL_LINES = 200
ALLOWED_EXTENSIONS = (".zip",)
ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06")
CONFIG_VERSION = 1
