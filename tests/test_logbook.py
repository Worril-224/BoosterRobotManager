"""生成本地部署日志的单元测试（密码打码 + 文件与界面一致）。"""

from __future__ import annotations

from pathlib import Path

from brm.core.logbook import DeployLog


def test_log_written_and_echoed(tmp_path: Path) -> None:
    lines = []
    log = DeployLog("107", log_dir=tmp_path, echo=lines.append)
    log.stage("阶段 1/9：测试")
    log.info("开始部署")
    log.raw("编译输出 build-line-1")
    log.warn("警告信息")
    log.error("错误信息")
    log.close()

    assert log.path.exists()
    text = log.path.read_text(encoding="utf-8")
    assert "阶段 1/9：测试" in text
    assert "编译输出 build-line-1" in text
    assert "警告信息" in text
    assert "错误信息" in text
    # 文件内容与界面回调一致
    assert lines == text.rstrip("\n").split("\n")


def test_password_is_masked_in_logs(tmp_path: Path) -> None:
    lines = []
    log = DeployLog("107", log_dir=tmp_path, echo=lines.append, secrets=["hunter2"])
    log.info("使用密码 hunter2 连接")
    log.info("参数 password=hunter2")
    log.close()

    text = log.path.read_text(encoding="utf-8")
    assert "hunter2" not in text
    assert "******" in text
    assert all("hunter2" not in line for line in lines)


def test_stage_separator(tmp_path: Path) -> None:
    log = DeployLog("107", log_dir=tmp_path)
    log.stage("阶段 3/9：上传")
    log.close()
    text = log.path.read_text(encoding="utf-8")
    assert "──────── 阶段 3/9：上传 ────────" in text
