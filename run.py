"""源码运行 / PyInstaller 打包入口：``python run.py``。"""

from __future__ import annotations

from brm.app import main

if __name__ == "__main__":
    raise SystemExit(main())
