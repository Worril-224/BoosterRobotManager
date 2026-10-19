"""部署流水线 + start.sh 控制的集成测试。

用 ``tests/fake_remote.py`` 在本地沙箱里模拟 Ubuntu 机器人，真实执行
unzip / chmod / bash / kill 等命令，覆盖 R3 - R8 的完整流程。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from brm.core.deployer import Deployer, DeployResult
from brm.core.errors import (
    AlreadyRunningError,
    BrmError,
    CompileFailedError,
    FileValidationError,
    MissingRemoteCommandError,
    ScriptNotFoundError,
)
from brm.core.logbook import DeployLog
from brm.core.proc_control import ProcControl
from brm.core.sftp_client import SFTPClient
from tests.fake_remote import REMOTE_WORKSPACE, FakeRemote
from tests.helpers import build_demo_project_zip, build_zip

PROJECT = "/home/booster/Workspace/demo"


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def run_deploy(remote: FakeRemote, zip_path: Path, tmp_path: Path):
    logs = []
    sftp = SFTPClient(remote)
    log = DeployLog("107", log_dir=tmp_path / "deploy-logs", echo=logs.append)
    deployer = Deployer(remote, sftp, log, robot_name="107")
    try:
        result = deployer.run(zip_path, REMOTE_WORKSPACE)
    finally:
        log.close()
    return result, logs, log


# ---------------------------------------------------------------------------
# R3 - R7 部署流水线
# ---------------------------------------------------------------------------
def test_deploy_end_to_end(remote: FakeRemote, tmp_path: Path) -> None:
    archive = build_demo_project_zip(tmp_path / "demo.zip")
    result, logs, log = run_deploy(remote, archive, tmp_path)

    assert isinstance(result, DeployResult)
    assert result.project_dir == PROJECT
    assert result.project_name == "demo"
    assert result.remote_zip == "/home/booster/Workspace/demo.zip"

    # R3：zip 已上传到 Workspace
    assert remote.exists("/home/booster/Workspace/demo.zip")
    # R6：唯一顶层目录不会产生 demo/demo 双重嵌套
    assert remote.exists(f"{PROJECT}/scripts/build.sh")
    assert not remote.exists(f"{PROJECT}/demo")
    # R7：编译产物存在
    assert remote.exists(f"{PROJECT}/app.bin")
    assert remote.read_text(f"{PROJECT}/app.bin") == "artifact"
    # R8：start.sh 具备执行权限
    assert os.access(remote.local_path(f"{PROJECT}/scripts/start.sh"), os.X_OK)
    # 流式输出与退出码
    joined = "\n".join(logs)
    assert "编译输出开始" in joined
    assert "build-line-1" in joined
    assert "build-line-2" in joined
    assert "编译退出码：0" in joined
    assert "未找到 stop.sh，跳过执行权限授权" in joined
    # 本地日志文件已生成且与界面日志一致
    assert log.path.exists()
    assert "build-line-1" in log.path.read_text(encoding="utf-8")


def test_upload_progress_and_single_top_dir(remote: FakeRemote, tmp_path: Path) -> None:
    archive = build_demo_project_zip(tmp_path / "my_project.zip", top_dir="demo")
    progress = []
    logs = []
    sftp = SFTPClient(remote)
    log = DeployLog("107", log_dir=tmp_path / "logs", echo=logs.append)
    deployer = Deployer(
        remote, sftp, log, robot_name="107", progress=progress.append
    )
    try:
        result = deployer.run(archive, REMOTE_WORKSPACE)
    finally:
        log.close()
    # zip 名与顶层目录不同：解压到 Workspace 根目录，项目目录为 demo
    assert result.project_dir == PROJECT
    assert not remote.exists("/home/booster/Workspace/my_project")
    assert progress and progress[-1] == 100


def test_redeploy_cleans_old_files(remote: FakeRemote, tmp_path: Path) -> None:
    archive = build_demo_project_zip(tmp_path / "test.zip")
    run_deploy(remote, archive, tmp_path)

    # 制造旧部署残留
    remote.write_text("/home/booster/Workspace/test.zip", "old zip content")
    remote.write_text(f"{PROJECT}/OLD_MARKER.txt", "stale")
    remote.write_text("/home/booster/Workspace/test/OLD_STAGING.txt", "stale")

    result, logs, _ = run_deploy(remote, archive, tmp_path)
    assert result.project_dir == PROJECT
    assert not remote.exists(f"{PROJECT}/OLD_MARKER.txt")
    assert not remote.exists("/home/booster/Workspace/test/OLD_STAGING.txt")
    joined = "\n".join(logs)
    assert "已删除远端同名文件" in joined
    assert "已删除远端同名目录" in joined
    # 远端只保留最新解压结果
    assert remote.exists(f"{PROJECT}/app/main.py")


def test_reject_non_zip_without_remote_operation(remote: FakeRemote, tmp_path: Path) -> None:
    archive = build_zip(tmp_path / "demo.rar", {"demo/x.txt": "1"})
    with pytest.raises(FileValidationError):
        run_deploy(remote, archive, tmp_path)
    assert remote.commands == []
    assert not remote.exists("/home/booster/Workspace/demo.rar")


def test_reject_fake_zip_header(remote: FakeRemote, tmp_path: Path) -> None:
    fake = tmp_path / "fake.zip"
    fake.write_text("I am not a zip", encoding="utf-8")
    with pytest.raises(FileValidationError):
        run_deploy(remote, fake, tmp_path)
    assert remote.commands == []


def test_reject_illegal_path_zip(remote: FakeRemote, tmp_path: Path) -> None:
    archive = build_zip(tmp_path / "evil.zip", {"demo/a.txt": "1", "../evil.txt": "x"})
    with pytest.raises(FileValidationError):
        run_deploy(remote, archive, tmp_path)
    assert remote.commands == []


def test_flat_zip_project_dir(remote: FakeRemote, tmp_path: Path) -> None:
    archive = build_zip(
        tmp_path / "flat.zip",
        {
            "build.sh": "#!/bin/bash\necho flat-build\n",
            "start.sh": "#!/bin/bash\necho flat-start\n",
            "readme.txt": "flat",
        },
    )
    result, logs, _ = run_deploy(remote, archive, tmp_path)
    assert result.project_dir == "/home/booster/Workspace/flat"
    assert "flat-build" in "\n".join(logs)


def test_missing_unzip(remote: FakeRemote, tmp_path: Path) -> None:
    remote.missing_unzip = True
    archive = build_demo_project_zip(tmp_path / "demo.zip")
    with pytest.raises(MissingRemoteCommandError) as excinfo:
        run_deploy(remote, archive, tmp_path)
    assert "unzip" in excinfo.value.message


def test_compile_failure_stops_pipeline(remote: FakeRemote, tmp_path: Path) -> None:
    archive = build_demo_project_zip(tmp_path / "demo.zip", build_exit_code=3)
    with pytest.raises(CompileFailedError) as excinfo:
        run_deploy(remote, archive, tmp_path)
    assert "退出码 3" in excinfo.value.message
    # 编译失败后不再授权 start.sh
    assert not os.access(remote.local_path(f"{PROJECT}/scripts/start.sh"), os.X_OK)


def test_missing_build_script(remote: FakeRemote, tmp_path: Path) -> None:
    archive = build_demo_project_zip(tmp_path / "demo.zip", include_build=False)
    with pytest.raises(ScriptNotFoundError) as excinfo:
        run_deploy(remote, archive, tmp_path)
    assert "build.sh" in excinfo.value.message


def test_missing_start_script(remote: FakeRemote, tmp_path: Path) -> None:
    archive = build_demo_project_zip(tmp_path / "demo.zip", include_start=False)
    with pytest.raises(ScriptNotFoundError) as excinfo:
        run_deploy(remote, archive, tmp_path)
    assert "start.sh" in excinfo.value.message


def test_compile_output_is_streamed(remote: FakeRemote, tmp_path: Path) -> None:
    """编译输出必须边执行边回显，而不是结束后一次性打印。"""
    archive = build_demo_project_zip(tmp_path / "demo.zip", build_sleep=2)
    events = []
    start = time.monotonic()

    sftp = SFTPClient(remote)
    log = DeployLog(
        "107",
        log_dir=tmp_path / "logs",
        echo=lambda text: events.append((time.monotonic(), text)),
    )
    deployer = Deployer(remote, sftp, log, robot_name="107")
    try:
        deployer.run(archive, REMOTE_WORKSPACE)
    finally:
        log.close()
    end = time.monotonic()

    line_times = [moment for moment, text in events if "build-line-1" in text]
    assert line_times, "未捕获到 build-line-1 输出"
    assert end - start > 1.5, "构建脚本的 sleep 未生效，测试无效"
    assert line_times[0] - start < end - start - 0.8, "编译输出不是流式回显"


# ---------------------------------------------------------------------------
# R8 start.sh 控制
# ---------------------------------------------------------------------------
def deploy_demo(remote: FakeRemote, tmp_path: Path, **kwargs):
    archive = build_demo_project_zip(tmp_path / "demo.zip", **kwargs)
    result, _logs, _ = run_deploy(remote, archive, tmp_path)
    return result


def test_status_before_start(remote: FakeRemote, tmp_path: Path) -> None:
    deploy_demo(remote, tmp_path)
    control = ProcControl(remote, REMOTE_WORKSPACE)
    status = control.status("demo")
    assert not status.running
    assert status.text == "未运行"


def test_start_stop_cycle(remote: FakeRemote, tmp_path: Path) -> None:
    result = deploy_demo(remote, tmp_path)
    logs = []
    control = ProcControl(remote, REMOTE_WORKSPACE, log=logs.append)

    started = control.start(
        result.project_name, result.project_dir, result.start_script, wait_seconds=10
    )
    assert started.running
    assert started.pid
    assert control.status("demo").running
    # 远端 pid / 日志文件齐备
    assert remote.exists("/home/booster/Workspace/demo.start.pid")
    assert remote.exists("/home/booster/Workspace/demo.start.log")

    # 重复启动被拒绝
    with pytest.raises(AlreadyRunningError):
        control.start(
            result.project_name, result.project_dir, result.start_script, wait_seconds=3
        )

    time.sleep(0.6)
    tail = control.tail_log("demo", 100)
    assert "demo service running" in tail or "demo service starting" in tail

    stopped = control.stop("demo", result.project_dir, wait_seconds=5)
    assert not stopped.running
    assert not control.status("demo").running
    assert not remote.exists("/home/booster/Workspace/demo.start.pid")
    # 进程确实消失
    kill_check = remote.exec_command(f"kill -0 {started.pid} 2>/dev/null")
    assert kill_check.exit_code != 0


def test_start_failure_reports_log(remote: FakeRemote, tmp_path: Path) -> None:
    result = deploy_demo(
        remote,
        tmp_path,
        start_content="#!/bin/bash\necho 'start.sh 故意失败' >&2\nexit 7\n",
    )
    control = ProcControl(remote, REMOTE_WORKSPACE)
    with pytest.raises(BrmError) as excinfo:
        control.start(
            result.project_name, result.project_dir, result.start_script, wait_seconds=5
        )
    assert "启动失败" in excinfo.value.message
    assert "故意失败" in excinfo.value.detail


def test_tail_log_missing(remote: FakeRemote, tmp_path: Path) -> None:
    control = ProcControl(remote, REMOTE_WORKSPACE)
    assert control.tail_log("not-exist") == ""

# ---------------------------------------------------------------------------
# R8 扩展：启动时自动向 start.sh 输入两遍默认密码
# ---------------------------------------------------------------------------
PASSWORD_START_SCRIPT = """#!/bin/bash
IFS= read -r __P1
IFS= read -r __P2
EXPECTED="robot-default-password"
if [ "$__P1" != "$EXPECTED" ] || [ "$__P2" != "$EXPECTED" ]; then
  echo "密码校验失败" >&2
  exit 9
fi
echo "密码校验通过"
exec python3 -c "import time; print('demo service running', flush=True); time.sleep(300)"
"""


def test_start_auto_feeds_default_password_twice(remote: FakeRemote, tmp_path: Path) -> None:
    result = deploy_demo(remote, tmp_path, start_content=PASSWORD_START_SCRIPT)
    logs = []
    control = ProcControl(remote, REMOTE_WORKSPACE, log=logs.append)
    try:
        status = control.start(
            result.project_name,
            result.project_dir,
            result.start_script,
            wait_seconds=10,
            password="robot-default-password",
        )
        assert status.running
        time.sleep(0.6)
        tail = control.tail_log("demo", 100)
        assert "密码校验通过" in tail

        joined_logs = "\n".join(logs)
        assert "注入密码处理" in joined_logs

        # 密码不能出现在远端命令行参数或任何日志里
        all_commands = "\n".join(remote.commands)
        assert "robot-default-password" not in all_commands
        assert "__BRM_P1" in all_commands
        assert "robot-default-password" not in tail
        assert "robot-default-password" not in joined_logs
        assert "robot-default-password" not in remote.read_text(
            "/home/booster/Workspace/demo.start.log"
        )
    finally:
        control.stop("demo", result.project_dir, wait_seconds=5)


def test_start_password_mismatch_reports_failure(remote: FakeRemote, tmp_path: Path) -> None:
    result = deploy_demo(remote, tmp_path, start_content=PASSWORD_START_SCRIPT)
    control = ProcControl(remote, REMOTE_WORKSPACE)
    with pytest.raises(BrmError) as excinfo:
        control.start(
            result.project_name,
            result.project_dir,
            result.start_script,
            wait_seconds=5,
            password="wrong-password",
        )
    assert "启动失败" in excinfo.value.message
    assert "密码校验失败" in excinfo.value.detail

def test_prefers_scripts_directory(remote: FakeRemote, tmp_path: Path) -> None:
    """scripts/ 是当前约定的脚本目录，优先级高于旧的 script/。"""
    archive = build_zip(
        tmp_path / "pref.zip",
        {
            "pref/scripts/build.sh": "#!/bin/bash\necho build-from-scripts\n",
            "pref/scripts/start.sh": "#!/bin/bash\nexec sleep 120\n",
            "pref/script/build.sh": "#!/bin/bash\necho build-from-legacy-script\n",
            "pref/script/start.sh": "#!/bin/bash\nexec sleep 120\n",
        },
    )
    result, logs, _ = run_deploy(remote, archive, tmp_path)
    joined = "\n".join(logs)
    assert result.project_dir == "/home/booster/Workspace/pref"
    assert result.build_script == "/home/booster/Workspace/pref/scripts/build.sh"
    assert result.start_script == "/home/booster/Workspace/pref/scripts/start.sh"
    assert "build-from-scripts" in joined
    assert "build-from-legacy-script" not in joined


def test_legacy_script_directory_still_supported(remote: FakeRemote, tmp_path: Path) -> None:
    """旧工程只有 script/ 目录时，仍能兼容找到脚本。"""
    archive = build_zip(
        tmp_path / "legacy.zip",
        {
            "legacy/script/build.sh": "#!/bin/bash\necho build-from-legacy\n",
            "legacy/script/start.sh": "#!/bin/bash\nexec sleep 120\n",
            "legacy/readme.txt": "legacy project\n",
        },
    )
    result, logs, _ = run_deploy(remote, archive, tmp_path)
    assert result.project_dir == "/home/booster/Workspace/legacy"
    assert result.build_script == "/home/booster/Workspace/legacy/script/build.sh"
    assert "build-from-legacy" in "\n".join(logs)


def test_stop_script_permission_granted_during_build(remote: FakeRemote, tmp_path: Path) -> None:
    """编译阶段就应给 stop.sh 加上执行权限（zip 里通常不带可执行位）。"""
    archive = build_zip(
        tmp_path / "stopdemo.zip",
        {
            "stopdemo/scripts/build.sh": "#!/bin/bash\necho build-ok\n",
            "stopdemo/scripts/start.sh": "#!/bin/bash\nexec sleep 120\n",
            "stopdemo/scripts/stop.sh": "#!/bin/bash\necho stop-ok\n",
        },
    )
    result, logs, _ = run_deploy(remote, archive, tmp_path)
    stop_script = remote.local_path(f"{result.project_dir}/scripts/stop.sh")
    assert stop_script.is_file()
    assert os.access(stop_script, os.X_OK)
    assert "已赋予 stop.sh 执行权限" in "\n".join(logs)


# ---------------------------------------------------------------------------
# 通过项目自带的 stop.sh 停止服务
# ---------------------------------------------------------------------------
STOP_SCRIPT = """#!/bin/bash
declare -F sudo >/dev/null || { echo "STOP_FUNCTIONS_MISSING"; exit 8; }
echo "stop.sh 函数注入成功"
IFS= read -r __P1
IFS= read -r __P2
EXPECTED="robot-default-password"
if [ "$__P1" != "$EXPECTED" ] || [ "$__P2" != "$EXPECTED" ]; then
  echo "stop.sh 密码校验失败" >&2
  exit 9
fi
echo "stop.sh 密码校验通过"
pkill -f BRM_FAKE_SERVICE 2>/dev/null || true
echo "stop.sh 执行完成"
echo executed > stop-ran.txt
"""


def test_stop_uses_project_stop_script(remote: FakeRemote, tmp_path: Path) -> None:
    archive = build_zip(
        tmp_path / "stopdemo2.zip",
        {
            "swdemo/scripts/build.sh": "#!/bin/bash\necho build-ok\n",
            "swdemo/scripts/start.sh": (
                "#!/bin/bash\n"
                'exec python3 -c "import time; time.sleep(120)  # BRM_FAKE_SERVICE"\n'
            ),
            "swdemo/scripts/stop.sh": STOP_SCRIPT,
        },
    )
    result, logs, _ = run_deploy(remote, archive, tmp_path)
    control = ProcControl(remote, REMOTE_WORKSPACE, log=logs.append)
    try:
        status = control.start(
            result.project_name,
            result.project_dir,
            result.start_script,
            wait_seconds=10,
            password="robot-default-password",
        )
        assert status.running

        stopped = control.stop(
            result.project_name,
            result.project_dir,
            password="robot-default-password",
        )
        assert not stopped.running
        assert stopped.message == "已通过 stop.sh 停止"
        joined = "\n".join(logs)
        assert "检测到项目停止脚本" in joined
        assert "stop.sh 函数注入成功" in joined
        assert "stop.sh 密码校验通过" in joined
        assert "stop.sh 执行完成" in joined
        assert remote.exists(f"{result.project_dir}/stop-ran.txt")
        assert not remote.exists("/home/booster/Workspace/.brm-helpers")
        assert not remote.exists("/home/booster/Workspace/swdemo.start.pid")
    finally:
        remote.exec_command("pkill -f BRM_FAKE_SERVICE 2>/dev/null || true")


def test_status_can_detect_project_processes(remote: FakeRemote, monkeypatch) -> None:
    control = ProcControl(remote, REMOTE_WORKSPACE)
    monkeypatch.setattr(control, "_count_project_processes", lambda _project: 3)
    status = control.status("demo", "/home/booster/Workspace/demo")
    assert status.running
    assert "3 个项目进程" in status.message


def test_status_detects_processes_when_pid_stale(remote: FakeRemote, monkeypatch) -> None:
    """PID 记录已失效时，应通过项目进程判断服务仍在运行。"""
    remote.write_text("/home/booster/Workspace/demo.start.pid", "999999")
    control = ProcControl(remote, REMOTE_WORKSPACE)
    monkeypatch.setattr(control, "_count_project_processes", lambda _project: 2)
    status = control.status("demo", "/home/booster/Workspace/demo")
    assert status.running
    assert "2 个项目进程" in status.message


def test_start_one_shot_script_detected_by_processes(
    remote: FakeRemote, tmp_path: Path, monkeypatch
) -> None:
    """start.sh 拉起节点后自身退出时，仍应判定为启动成功。"""
    start_content = "#!/bin/bash\nsleep 0.3\necho '一次性启动脚本结束'\n"
    result = deploy_demo(remote, tmp_path, start_content=start_content)
    control = ProcControl(remote, REMOTE_WORKSPACE)

    calls = []

    def fake_count(_project: str) -> int:
        calls.append(1)
        return 2 if len(calls) > 1 else 0

    monkeypatch.setattr(control, "_count_project_processes", fake_count)
    status = control.start(
        result.project_name,
        result.project_dir,
        result.start_script,
        wait_seconds=5,
        password="",
    )
    assert status.running
    assert "2 个项目进程" in status.message


def test_start_injects_password_functions_without_files(
    remote: FakeRemote, tmp_path: Path
) -> None:
    """密码处理通过 shell 函数注入，不在机器人上创建任何辅助文件。"""
    start_content = (
        "#!/bin/bash\n"
        "if declare -F sudo >/dev/null && declare -F systemctl >/dev/null; then\n"
        '  echo "FUNCTIONS_OK"\n'
        "else\n"
        '  echo "FUNCTIONS_MISSING"\n'
        "fi\n"
        'if [ -n "$BRM_SUDO_PASSWORD" ]; then echo "PASSWORD_ENV_OK"; fi\n'
        "exec sleep 120\n"
    )
    result = deploy_demo(remote, tmp_path, start_content=start_content)
    control = ProcControl(remote, REMOTE_WORKSPACE)
    try:
        status = control.start(
            result.project_name,
            result.project_dir,
            result.start_script,
            wait_seconds=10,
            password="robot-default-password",
        )
        assert status.running
        time.sleep(0.6)
        tail = control.tail_log("demo", 100)
        assert "FUNCTIONS_OK" in tail
        assert "PASSWORD_ENV_OK" in tail
        assert "robot-default-password" not in tail
        # 不再生成 .brm-helpers 之类的辅助文件
        assert not remote.exists("/home/booster/Workspace/.brm-helpers")
    finally:
        remote.exec_command("pkill -f 'sleep 120' 2>/dev/null || true")


def test_start_authorizes_scripts_before_launch(remote: FakeRemote, tmp_path: Path) -> None:
    """启动前由程序自动补脚本执行权限（模拟旧部署丢失可执行位的情况）。"""
    archive = build_zip(
        tmp_path / "permdemo.zip",
        {
            "permdemo/scripts/build.sh": "#!/bin/bash\necho build-ok\n",
            "permdemo/scripts/start.sh": (
                "#!/bin/bash\n"
                "./scripts/stop.sh\n"
                'echo "start 调用 stop.sh 成功"\n'
                'exec python3 -c "import time; time.sleep(120)  # BRM_PERM_TEST"\n'
            ),
            "permdemo/scripts/stop.sh": "#!/bin/bash\necho \"stop.sh 被调用\"\n",
        },
    )
    result, logs, _ = run_deploy(remote, archive, tmp_path)
    start_path = remote.local_path(f"{result.project_dir}/scripts/start.sh")
    stop_path = remote.local_path(f"{result.project_dir}/scripts/stop.sh")
    # 模拟旧部署：压缩包没有携带可执行位
    os.chmod(start_path, 0o644)
    os.chmod(stop_path, 0o644)

    control = ProcControl(remote, REMOTE_WORKSPACE, log=logs.append)
    try:
        status = control.start(
            result.project_name,
            result.project_dir,
            result.start_script,
            wait_seconds=10,
            password="",
        )
        assert status.running
        time.sleep(0.6)
        tail = control.tail_log("permdemo", 100)
        assert "start 调用 stop.sh 成功" in tail
        assert "stop.sh 被调用" in tail
        assert os.access(start_path, os.X_OK)
        assert os.access(stop_path, os.X_OK)
    finally:
        remote.exec_command("pkill -f BRM_PERM_TEST 2>/dev/null || true")

def test_build_and_start_load_remote_environment(
    remote: FakeRemote, tmp_path: Path, monkeypatch
) -> None:
    """编译 / 启动前应加载机器人 ROS 环境（这里用假的 setup.bash 验证）。"""
    from brm.core import remote_env

    setup_script = tmp_path / "fake_ros_setup.bash"
    setup_script.write_text("export BRM_TEST_ROS_ENV=loaded\n", encoding="utf-8")
    monkeypatch.setattr(
        remote_env, "DEFAULT_SETUP_GLOBS", (str(setup_script),)
    )

    archive = build_zip(
        tmp_path / "envdemo.zip",
        {
            "envdemo/scripts/build.sh": (
                "#!/bin/bash\n"
                'echo "BUILD_ENV:${BRM_TEST_ROS_ENV}"\n'
                "echo build-ok\n"
            ),
            "envdemo/scripts/start.sh": (
                "#!/bin/bash\n"
                'echo "START_ENV:${BRM_TEST_ROS_ENV}"\n'
                'exec python3 -c "import time; time.sleep(120)  # BRM_ENV_TEST"\n'
            ),
            "envdemo/scripts/stop.sh": (
                "#!/bin/bash\n"
                'echo "STOP_ENV:${BRM_TEST_ROS_ENV}"\n'
            ),
        },
    )
    result, logs, _ = run_deploy(remote, archive, tmp_path)
    joined = "\n".join(logs)
    assert "BUILD_ENV:loaded" in joined

    control = ProcControl(remote, REMOTE_WORKSPACE, log=logs.append)
    try:
        status = control.start(
            result.project_name,
            result.project_dir,
            result.start_script,
            wait_seconds=10,
            password="",
        )
        assert status.running
        time.sleep(0.6)
        tail = control.tail_log("envdemo", 100)
        assert "START_ENV:loaded" in tail
    finally:
        remote.exec_command("pkill -f BRM_ENV_TEST 2>/dev/null || true")
