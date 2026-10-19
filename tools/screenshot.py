"""生成界面截图（offscreen 渲染），用于 README 展示与人工验收。

运行方式::

    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/screenshot.py

截图会输出到 ``screenshots/`` 目录；脚本使用本地 FakeRemote 模拟一台已连接
的机器人，所以不需要真实机器人。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from brm import constants  # noqa: E402
from brm.core.proc_control import ProcControl  # noqa: E402
from brm.core.robots import Robot, RobotStore  # noqa: E402
from brm.core.sftp_client import SFTPClient  # noqa: E402
from brm.ui.main_window import MainWindow  # noqa: E402
from brm.ui.password_dialog import PasswordDialog  # noqa: E402
from brm.ui.settings_dialog import SettingsDialog  # noqa: E402
from tests.fake_remote import FakeRemote  # noqa: E402
from tests.helpers import build_demo_project_zip  # noqa: E402


def wait_until(predicate, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    QApplication.processEvents()
    return bool(predicate())


def main() -> int:
    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="brm-shot-"))
    constants.LOG_DIR = tmp / "logs"  # 截图演示产生的日志不落到仓库

    store = RobotStore(tmp / "config" / "robots.json")
    store.load()
    store.set_default_password("demo-password")
    store.add_robot(Robot(name="108", host="192.168.1.108"))
    store.add_robot(Robot(name="109", host="192.168.1.109"))

    remote = FakeRemote(tmp / "remote")
    remote.connected = True
    remote.write_text("/home/booster/notes.txt", "Booster 机器人现场记录\n")

    window = MainWindow(store)
    window.manager._session = remote
    window.manager._sftp = SFTPClient(remote)
    window.manager._robot_name = "107"
    window.show()
    app.processEvents()

    window._on_connect_success("107", False, None)
    wait_until(lambda: window.file_panel._table.rowCount() > 0, 5)

    archive = build_demo_project_zip(tmp / "demo.zip")
    window._set_zip(archive)
    window._start_deploy(archive)
    wait_until(lambda: not window._busy and window._project is not None, 40)
    wait_until(lambda: window._proc_worker is None, 15)

    window._proc_action("start")
    wait_until(lambda: window._status is not None and window._status.running, 25)
    wait_until(lambda: window._proc_worker is None, 15)

    window._navigate("/home/booster/Workspace/demo")
    wait_until(
        lambda: window.file_panel.current_path == "/home/booster/Workspace/demo"
        and not window.file_panel._loading
        and window.file_panel._table.rowCount() > 0
        and window.file_panel._table.item(0, 0).text() == "script",
        10,
    )
    window.resize(1380, 900)
    app.processEvents()

    output = PROJECT_ROOT / "screenshots"
    output.mkdir(exist_ok=True)
    window.grab().save(str(output / "main_window.png"))

    settings = SettingsDialog(store, window.manager, window)
    settings.show()
    app.processEvents()
    settings.grab().save(str(output / "settings_dialog.png"))
    settings.close()

    password = PasswordDialog(
        window,
        title="设置机器人密码",
        message="尚未设置机器人密码，请先设置。",
        note="密码保存后长期复用；只有认证失败时才会再次询问。",
    )
    password.show()
    app.processEvents()
    password.grab().save(str(output / "password_dialog.png"))
    password.close()

    try:
        ProcControl(remote, "/home/booster/Workspace/").stop(
            "demo", window._project.project_dir, wait_seconds=3
        )
    except Exception:
        pass
    window.manager._session = None
    window.manager._sftp = None
    window.close()
    print(f"截图已生成：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
