"""构建 Windows 便携版 BRM（在 macOS / Linux 上交叉构建）。

产物：``dist-windows/BoosterRobotManager-Windows-x64.zip``
解压后双击 ``BoosterRobotManager.exe`` 即可运行，无需安装 Python。

组成：
* ``BoosterRobotManager.exe``：Zig 交叉编译的 GUI 启动器（调用内置 pythonw.exe -m brm）
* ``python/``：Windows embeddable Python 3.12 + Windows 版依赖（PySide6 Essentials / paramiko）
* ``brm/``：本项目源码（示例配置，不含任何密码）
* ``README-Windows.txt`` / ``BoosterRobotManager-Console.bat``

依赖（仅构建机需要）：
* 能访问网络（下载 Python embeddable 与 Windows wheels）
* ``pip install ziglang``（用于编译 .exe 启动器）

用法::

    .venv/bin/pip install ziglang        # 仅首次
    .venv/bin/python tools/build_windows_bundle.py
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = PROJECT_ROOT / ".build-windows"
DIST_DIR = PROJECT_ROOT / "dist-windows"
STAGE_DIR = BUILD_DIR / "stage" / "BoosterRobotManager"
PYTHON_DIR = BUILD_DIR / "python"
SITE_PACKAGES = PYTHON_DIR / "Lib" / "site-packages"

PYTHON_EMBED_VERSION = "3.12.10"
PYTHON_EMBED_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_EMBED_VERSION}/"
    f"python-{PYTHON_EMBED_VERSION}-embed-amd64.zip"
)

WINDOWS_WHEELS = ["PySide6-Essentials", "paramiko"]
WHEEL_INDEX_ARGS = [
    "--platform", "win_amd64",
    "--python-version", "3.12",
    "--implementation", "cp",
    "--abi", "cp312",
    "--only-binary=:all:",
    "--no-compile",
    "--upgrade",
]

LAUNCHER_RC = '1 ICON "app.ico"\n'

LAUNCHER_C = r"""
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wchar.h>

static void show_error(const wchar_t *message) {
    MessageBoxW(NULL, message, L"Booster Robot Manager", MB_ICONERROR | MB_OK);
}

int main(void) {
    wchar_t exe_path[MAX_PATH];
    DWORD length = GetModuleFileNameW(NULL, exe_path, MAX_PATH);
    if (length == 0 || length >= MAX_PATH) {
        show_error(L"无法获取程序路径。");
        return 1;
    }
    for (DWORD i = length; i > 0; --i) {
        if (exe_path[i - 1] == L'\\' || exe_path[i - 1] == L'/') {
            exe_path[i - 1] = 0;
            break;
        }
    }

    wchar_t python_path[MAX_PATH * 2];
    _snwprintf(python_path, MAX_PATH * 2, L"%ls\\python\\pythonw.exe", exe_path);
    if (GetFileAttributesW(python_path) == INVALID_FILE_ATTRIBUTES) {
        show_error(L"未找到 python\\pythonw.exe，请确认整个程序文件夹已完整解压。");
        return 1;
    }

    wchar_t command[MAX_PATH * 3];
    _snwprintf(command, MAX_PATH * 3, L"\"%ls\" -m brm", python_path);
    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    ZeroMemory(&startup, sizeof(startup));
    startup.cb = sizeof(startup);
    ZeroMemory(&process, sizeof(process));

    BOOL ok = CreateProcessW(
        python_path, command, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL,
        exe_path, &startup, &process
    );
    if (!ok) {
        wchar_t message[512];
        _snwprintf(message, 512, L"启动失败（错误码 %lu）。请查看 README-Windows.txt。",
                   (unsigned long)GetLastError());
        show_error(message);
        return 1;
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return 0;
}
"""

README_WINDOWS = """Booster 机器人管理器（Windows 便携版）
====================================

【运行】
1. 把整个 BoosterRobotManager 文件夹解压到有写入权限的位置
   （例如桌面 / 文档；不要放在 C:\\Program Files，否则无法保存配置与日志）。
2. 双击 BoosterRobotManager.exe 即可启动，无需安装 Python。
3. 首次启动请点击右上角「⚙ 密码设置」设置机器人默认密码。

【目录说明】
- python\\                 内置的 Windows Python 运行时（自带依赖，请勿删除）
- brm\\                    程序源码与配置
- brm\\config\\robots.json  机器人配置（密码 Base64 混淆存储，非加密）
- brm\\logs\\               部署日志
- BoosterRobotManager-Console.bat  控制台启动方式（排查问题时使用）

【常见问题】
1. Windows 提示"已保护你的电脑"/"未知发布者"：
   这是未签名程序的正常提示。点击"更多信息"→"仍要运行"，
   或右键 exe →"属性"→勾选"解除锁定"→确定。
2. 双击没反应或提示找不到 python：
   请确认整个文件夹（尤其是 python\\ 目录）都解压出来了，
   不要只复制 exe 一个文件。
3. 启动后窗口出不来 / 缺少 DLL：
   请安装"Microsoft Visual C++ 2015-2022 运行库（x64）"后重试。
4. 想确认启动错误：
   双击 BoosterRobotManager-Console.bat，控制台会显示 Python 报错信息。

【安全说明】
- 配置里的密码仅为 Base64 混淆，不是加密；请只在可信任的 Windows 电脑上使用。
- 日志中的密码会显示为 ******。
"""


def run(command: list[str], **kwargs) -> None:
    print("$", " ".join(str(part) for part in command))
    subprocess.run(command, check=True, **kwargs)


def download_python_embed() -> None:
    archive = BUILD_DIR / "python-embed.zip"
    if PYTHON_DIR.exists():
        print("已存在 python/，跳过下载。")
        return
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        print(f"下载 Windows embeddable Python：{PYTHON_EMBED_URL}")
        urllib.request.urlretrieve(PYTHON_EMBED_URL, archive)
    print("解压 Python embeddable …")
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(PYTHON_DIR)


def install_windows_wheels() -> None:
    if (SITE_PACKAGES / "PySide6").exists() and (SITE_PACKAGES / "paramiko").exists():
        print("已存在 Windows 依赖，跳过安装。")
        return
    print("安装 Windows 版依赖（PySide6-Essentials、paramiko）…")
    SITE_PACKAGES.mkdir(parents=True, exist_ok=True)
    run(
        [
            sys.executable, "-m", "pip", "install",
            "--target", str(SITE_PACKAGES),
            *WHEEL_INDEX_ARGS,
            *WINDOWS_WHEELS,
        ]
    )


def prepare_stage() -> None:
    print("准备发布目录 …")
    if STAGE_DIR.exists():
        shutil.rmtree(STAGE_DIR)
    STAGE_DIR.mkdir(parents=True)
    shutil.copytree(PYTHON_DIR, STAGE_DIR / "python")

    # 应用源码（排除缓存、日志、测试、用户配置）
    shutil.copytree(
        PROJECT_ROOT / "brm",
        STAGE_DIR / "brm",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "logs"),
    )
    (STAGE_DIR / "brm" / "logs").mkdir(parents=True, exist_ok=True)
    (STAGE_DIR / "brm" / "logs" / "说明.txt").write_text(
        "部署日志会保存在此目录。\n", encoding="utf-8"
    )
    (STAGE_DIR / "run.py").write_text(
        "from brm.app import main\n\nif __name__ == '__main__':\n    raise SystemExit(main())\n",
        encoding="utf-8",
    )

    # 写入不含任何密码的默认配置（绝不能把开发机上的真实配置打进去）
    sys.path.insert(0, str(PROJECT_ROOT))
    from brm.core.robots import default_config  # noqa: E402

    config_path = STAGE_DIR / "brm" / "config" / "robots.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(default_config(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # embeddable Python 的 _pth：允许 import site、加载 site-packages 与上层应用目录
    pth = STAGE_DIR / "python" / "python312._pth"
    pth.write_text(
        "python312.zip\n.\nLib\\site-packages\n..\nimport site\n",
        encoding="utf-8",
    )

    (STAGE_DIR / "README-Windows.txt").write_text(README_WINDOWS, encoding="utf-8")
    (STAGE_DIR / "BoosterRobotManager-Console.bat").write_text(
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        'cd /d "%~dp0"\r\n'
        "echo 正在以控制台模式启动 Booster 机器人管理器…\r\n"
        "python\\python.exe -m brm\r\n"
        "pause\r\n",
        encoding="utf-8",
    )


def build_launcher() -> None:
    print("编译 Windows 启动器（Zig 交叉编译）…")
    source = BUILD_DIR / "launcher.c"
    source.write_text(LAUNCHER_C, encoding="utf-8")
    target = STAGE_DIR / "BoosterRobotManager.exe"
    env = {
        "ZIG_GLOBAL_CACHE_DIR": str(BUILD_DIR / "zig-global-cache"),
        "ZIG_LOCAL_CACHE_DIR": str(BUILD_DIR / "zig-cache"),
    }
    import os

    zig_env = {**os.environ, **env}
    sources = [str(source)]
    icon = PROJECT_ROOT / "tools" / "app_icon.ico"
    if icon.exists():
        shutil.copyfile(icon, BUILD_DIR / "app.ico")
        (BUILD_DIR / "launcher.rc").write_text(LAUNCHER_RC, encoding="utf-8")
        resource = BUILD_DIR / "launcher.res"
        run(
            [
                sys.executable, "-m", "ziglang", "rc",
                "launcher.rc", "launcher.res",
            ],
            env=zig_env,
            cwd=BUILD_DIR,
        )
        sources.append(str(resource))

    run(
        [
            sys.executable, "-m", "ziglang", "cc",
            "-target", "x86_64-windows-gnu",
            "-O2",
            "-Wl,--subsystem,windows",
            "-o", str(target),
            *sources,
            "-luser32",
        ],
        env=zig_env,
    )


def make_zip() -> Path:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    archive = DIST_DIR / "BoosterRobotManager-Windows-x64.zip"
    print(f"打包：{archive}")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(STAGE_DIR.rglob("*")):
            relative = path.relative_to(STAGE_DIR.parent)
            if path.is_dir():
                bundle.writestr(str(relative) + "/", b"")
            else:
                bundle.write(path, relative.as_posix())
    return archive


def main() -> int:
    download_python_embed()
    install_windows_wheels()
    prepare_stage()
    build_launcher()
    archive = make_zip()
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    size_mb = archive.stat().st_size / 1024 / 1024
    print()
    print(f"完成：{archive}（{size_mb:.1f} MB）")
    print(f"SHA256：{digest}")
    print("解压后双击 BoosterRobotManager.exe 即可运行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
