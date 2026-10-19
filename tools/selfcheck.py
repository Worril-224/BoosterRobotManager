"""交付前自检脚本（人工可读输出）。

覆盖 pytest 无法完全覆盖的验收项：
* 真实 socket 层面的错误分级（拒绝连接 / 无法解析主机），确认不会误报为密码错误；
* 源码与示例配置中没有硬编码密码字面量；
* 部署日志中密码被打码。

运行：``.venv/bin/python tools/selfcheck.py``
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brm.core.errors import AuthFailedError, BrmError  # noqa: E402
from brm.core.ssh_client import SSHSession  # noqa: E402

PASS = "通过"
FAIL = "失败"

results = []


def record(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"[{PASS if ok else FAIL}] {name}：{detail}")


def check_real_connection_errors() -> None:
    """用真实 socket 验证错误分级，绝不误报为密码错误。"""

    # 1) 本机 22 端口未监听 -> 拒绝连接
    session = SSHSession("127.0.0.1", 22, "booster", password="placeholder", connect_timeout=3)
    try:
        session.connect()
        record("连接被拒绝分类", False, "意外连接成功（本机 22 端口不应开放）")
    except BrmError as exc:
        record(
            "连接被拒绝分类",
            not isinstance(exc, AuthFailedError),
            f"{type(exc).__name__} / {exc.title}",
        )
    finally:
        session.close()

    # 2) 非法 IP -> 无法解析 / 网络不可达（而不是密码错误）
    session = SSHSession("192.168.1.999", 22, "booster", password="placeholder", connect_timeout=3)
    try:
        session.connect()
        record("非法 IP 分类", False, "意外连接成功")
    except BrmError as exc:
        record(
            "非法 IP 分类（192.168.1.999）",
            not isinstance(exc, AuthFailedError),
            f"{type(exc).__name__} / {exc.title} / {exc.message}",
        )
    finally:
        session.close()


def check_no_hardcoded_password() -> None:
    """扫描生产代码与示例配置，确认没有硬编码密码。"""
    offenders = []
    password_pattern = re.compile(r"""password\s*=\s*["'][^"']+["']""")
    for path in (PROJECT_ROOT / "brm").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in password_pattern.finditer(text):
            offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {match.group(0)}")
    record(
        "源码无硬编码密码字面量",
        not offenders,
        "未发现" if not offenders else "；".join(offenders),
    )

    config = PROJECT_ROOT / "brm" / "config" / "robots.json"
    text = config.read_text(encoding="utf-8")
    empty_password = '"password": ""' in text and '"default_password": ""' in text
    record("示例配置密码为空", empty_password, "password / default_password 均为空")


def check_log_masking() -> None:
    """确认部署日志对密码打码（R0 验收第 7 条）。"""
    import tempfile

    from brm.core.logbook import DeployLog

    with tempfile.TemporaryDirectory() as tmp:
        secret = "p@ssw0rd-for-test"
        log = DeployLog("107", log_dir=Path(tmp), secrets=[secret])
        log.info(f"使用密码 {secret} 尝试连接")
        log.info(f"password={secret}")
        log.close()
        text = log.path.read_text(encoding="utf-8")
        masked = secret not in text and "******" in text
        record("部署日志密码打码", masked, "日志中未出现明文密码")


def main() -> int:
    check_real_connection_errors()
    check_no_hardcoded_password()
    check_log_masking()
    print()
    failed = [name for name, ok, _detail in results if not ok]
    print(f"自检完成：{len(results) - len(failed)}/{len(results)} 项通过")
    if failed:
        print("未通过：" + "、".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
