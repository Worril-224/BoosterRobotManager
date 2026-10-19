"""远端 shell 环境加载（ROS 2 等）。

机器人通过 SSH 非交互执行命令时**不会**加载交互式 shell 的 ``~/.bashrc``，
因此 ``ros2`` / ``colcon`` 可能不在 PATH 中，项目 ``start.sh`` 里的
``nohup ros2 launch ...`` 会报 ``ros2: No such file or directory``。

这里的做法是在执行项目脚本前显式 source 机器人的 ROS 环境脚本：

* ``/opt/ros/<distro>/setup.bash``（如 kilted / humble）
* ``/opt/booster/BoosterRos2/install/setup.bash``（Booster 机器人自带的 ROS 工作空间）

文件不存在的会被自动跳过，因此在不同机器人镜像上都能安全使用。
"""

from __future__ import annotations

from typing import Iterable, Optional

# 支持通配符（由远端 shell 展开）；源顺序即列表顺序
DEFAULT_SETUP_GLOBS = (
    "/opt/ros/*/setup.bash",
    "/opt/booster/BoosterRos2/install/setup.bash",
)


def env_setup_command(globs: Optional[Iterable[str]] = None) -> str:
    """返回一段 bash 片段：依次 source 存在的环境脚本，最后清理循环变量。

    注意：通配符必须保持**不加引号**才能展开；这里只接受内部常量或受信任的
    绝对路径模式，不拼接用户输入。
    """
    patterns = " ".join(str(pattern) for pattern in (globs or DEFAULT_SETUP_GLOBS))
    return (
        f"for __brm_env in {patterns}; do "
        'if [ -f "$__brm_env" ]; then '
        '. "$__brm_env" >/dev/null 2>&1; '
        'echo "[BRM] 已加载远端环境：$__brm_env"; '
        "fi; done; unset __brm_env; "
    )
