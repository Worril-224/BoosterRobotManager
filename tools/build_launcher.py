"""生成 macOS 启动器：``Booster机器人管理器.app`` 与 ``启动 Booster 机器人管理器.command``。

用法（在项目根目录或任意目录执行均可）：

    .venv/bin/python tools/build_launcher.py

脚本会把当前项目的**绝对路径**写入启动器，因此生成后可以把 .app 拖到
「应用程序」或程序坞，双击即可运行；如果以后移动了项目目录，重新执行本脚本
即可更新路径。

说明：
* .app 双击即可运行（不弹终端）；首次运行如果缺少虚拟环境，会弹出提示并
  自动打开终端完成依赖安装；
* .command 双击会打开终端窗口，适合排查启动问题；
* 图标来自 ``tools/app_icon.png``（可用 ``tools/make_icon.py`` 重新生成）。
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "Booster机器人管理器"
APP_BUNDLE = PROJECT_ROOT / f"{APP_NAME}.app"
COMMAND_NAME = "启动 Booster 机器人管理器.command"
COMMAND_FILE = PROJECT_ROOT / COMMAND_NAME
ICON_PNG = PROJECT_ROOT / "tools" / "app_icon.png"


def shell_quote(value: str) -> str:
    """把路径安全地放进 bash 双引号字符串。"""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")


def write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(0o755)


# ---------------------------------------------------------------------------
# .command（终端启动 + 首次自动安装依赖）
# ---------------------------------------------------------------------------
def build_command() -> None:
    project = shell_quote(str(PROJECT_ROOT))
    content = f'''#!/bin/bash
# 由 tools/build_launcher.py 自动生成；如需修改，请修改生成脚本后重新生成。
set -u

PROJECT_DIR="{project}"
cd "$PROJECT_DIR" || {{
  echo "无法进入项目目录：$PROJECT_DIR"
  read -r -p "按回车键关闭…" _
  exit 1
}}

echo "=============================================="
echo " Booster 机器人管理器"
echo " 项目目录：$PROJECT_DIR"
echo "=============================================="

pick_python() {{
  local candidate
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
        command -v "$candidate"
        return 0
      fi
    fi
  done
  # 常见安装位置兜底（Terminal 的 PATH 可能不含用户级目录）
  for candidate in "$HOME/.local/bin/python3.12" "$HOME/.local/bin/python3.11" \\
                   /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 \\
                   /usr/local/bin/python3.12 /usr/local/bin/python3.11; do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}}

VENV_PY="$PROJECT_DIR/.venv/bin/python"

if [ ! -x "$VENV_PY" ] || ! "$VENV_PY" -c "import PySide6, paramiko" >/dev/null 2>&1; then
  echo "检测到虚拟环境不存在或依赖不完整，开始自动安装（首次约需 1-3 分钟）…"
  PYTHON_BIN="$(pick_python)" || {{
    echo "未找到 Python 3.10+。请先安装 Python 3.10 或更高版本。"
    read -r -p "按回车键关闭…" _
    exit 1
  }}
  echo "使用的 Python：$PYTHON_BIN（$("$PYTHON_BIN" -V 2>&1)）"

  if [ ! -x "$VENV_PY" ]; then
    "$PYTHON_BIN" -m venv "$PROJECT_DIR/.venv" || {{
      echo "创建虚拟环境失败。"
      read -r -p "按回车键关闭…" _
      exit 1
    }}
  fi

  "$VENV_PY" -m pip install --upgrade pip
  if ! "$VENV_PY" -m pip install -r "$PROJECT_DIR/requirements.txt"; then
    echo "依赖安装失败，请检查网络后重试。"
    read -r -p "按回车键关闭…" _
    exit 1
  fi
  echo "依赖安装完成。"
fi

echo "正在启动 Booster 机器人管理器…"
exec "$VENV_PY" -m brm
'''
    write_executable(COMMAND_FILE, content)
    print(f"已生成终端启动器：{COMMAND_FILE.name}")


# ---------------------------------------------------------------------------
# .app（Finder / 程序坞双击启动）
# ---------------------------------------------------------------------------
def build_app_bundle() -> None:
    project = shell_quote(str(PROJECT_ROOT))
    if APP_BUNDLE.exists():
        shutil.rmtree(APP_BUNDLE)

    contents = APP_BUNDLE / "Contents"
    macos_dir = contents / "MacOS"
    resources = contents / "Resources"
    macos_dir.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)

    executable = macos_dir / "BoosterRobotManager"
    content = f'''#!/bin/bash
# 由 tools/build_launcher.py 自动生成；如需修改，请修改生成脚本后重新生成。
set -u

PROJECT_DIR="{project}"
VENV_PY="$PROJECT_DIR/.venv/bin/python"
LOG_DIR="$PROJECT_DIR/brm/logs"
LOG_FILE="$LOG_DIR/launcher.log"

mkdir -p "$LOG_DIR" 2>/dev/null

if [ ! -d "$PROJECT_DIR" ]; then
  /usr/bin/osascript -e 'display alert "找不到项目目录" message "请重新运行 tools/build_launcher.py 生成启动器。" as critical' >/dev/null 2>&1
  exit 1
fi

cd "$PROJECT_DIR" || exit 1

# 虚拟环境缺失或依赖不完整：提示并打开终端自动安装
if [ ! -x "$VENV_PY" ] || ! "$VENV_PY" -c "import PySide6, paramiko" >/dev/null 2>&1; then
  if /usr/bin/osascript -e 'display dialog "首次运行需要安装 Python 依赖（约 1-3 分钟）。\\n\\n点击“安装并运行”会自动打开终端完成安装。" buttons {{"安装并运行", "取消"}} default button "安装并运行" with title "Booster 机器人管理器" with icon note' >/dev/null 2>&1; then
    /usr/bin/open -a Terminal "$PROJECT_DIR/{COMMAND_NAME}"
  fi
  exit 0
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 启动 Booster 机器人管理器" >> "$LOG_FILE"
exec "$VENV_PY" -m brm >> "$LOG_FILE" 2>&1
'''
    write_executable(executable, content)

    # -- Info.plist ----------------------------------------------------
    info = {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleExecutable": "BoosterRobotManager",
        "CFBundleIdentifier": "com.booster.brm",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1",
        "CFBundleIconFile": "app",
        "LSMinimumSystemVersion": "10.15",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.developer-tools",
    }
    with (contents / "Info.plist").open("wb") as handle:
        plistlib.dump(info, handle)
    (contents / "PkgInfo").write_text("APPL????", encoding="ascii")

    # -- 图标 ----------------------------------------------------------
    if ICON_PNG.exists() and shutil.which("iconutil"):
        make_icns(ICON_PNG, resources / "app.icns")
    else:
        print("提示：未找到 tools/app_icon.png 或 iconutil，.app 将使用系统默认图标。")

    # 本地生成的 app 不需要隔离属性
    if shutil.which("xattr"):
        subprocess.run(["xattr", "-cr", str(APP_BUNDLE)], check=False)

    print(f"已生成应用：{APP_BUNDLE}")


def make_icns(source_png: Path, target: Path) -> None:
    """把 1024 PNG 转成 .icns（依赖 macOS 自带 iconutil / sips）。"""
    iconset = Path(tempfile.mkdtemp(prefix="brm-iconset-")) / "app.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    # iconutil 要求标准的 @1x / @2x 文件名
    specifications = [
        (16, "icon_16x16.png"),
        (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    ]
    try:
        for size, name in specifications:
            subprocess.run(
                ["sips", "-z", str(size), str(size), str(source_png), "--out", str(iconset / name)],
                check=True,
                capture_output=True,
            )
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(target)],
            check=True,
            capture_output=True,
        )
        print("已生成应用图标：app.icns")
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"提示：生成图标失败（{exc}），.app 将使用系统默认图标。")
    finally:
        shutil.rmtree(iconset.parent, ignore_errors=True)


def main() -> int:
    if sys.platform != "darwin":
        print("本脚本只用于生成 macOS 启动器。Windows 可用 pythonw 或 PyInstaller，见 README。")
        return 1
    build_command()
    build_app_bundle()
    print()
    print("完成！你可以：")
    print(f"  1. 双击打开：{APP_BUNDLE}")
    print("  2. 把 .app 拖到「应用程序」文件夹或程序坞，以后一键启动")
    print(f"  3. 调试启动问题：双击「{COMMAND_NAME}」查看终端输出")
    print("  4. 移动项目目录后，重新执行 .venv/bin/python tools/build_launcher.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
