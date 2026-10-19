"""程序入口：创建 QApplication、加载配置、启动主窗口。"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from . import APP_NAME, __version__
from .constants import CONFIG_PATH
from .core.errors import BrmError
from .core.robots import RobotStore
from .ui.main_window import MainWindow
from .ui.theme import APP_QSS


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName("Booster 机器人管理器")
    app.setApplicationVersion(__version__)
    app.setStyleSheet(APP_QSS)

    store = RobotStore(CONFIG_PATH)
    try:
        store.load()
    except BrmError as exc:
        QMessageBox.critical(None, exc.title, exc.user_message())
        return 1

    window = MainWindow(store)
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
