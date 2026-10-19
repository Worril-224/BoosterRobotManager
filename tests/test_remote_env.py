"""远端环境加载（ROS setup.bash）的单元测试。"""

from __future__ import annotations

from brm.core.remote_env import DEFAULT_SETUP_GLOBS, env_setup_command


def test_default_command_sources_ros_and_booster() -> None:
    command = env_setup_command()
    assert "for __brm_env in" in command
    assert "/opt/ros/*/setup.bash" in command
    assert "/opt/booster/BoosterRos2/install/setup.bash" in command
    assert "[ -f \"$__brm_env\" ]" in command
    assert ". \"$__brm_env\"" in command
    assert "unset __brm_env" in command


def test_custom_globs() -> None:
    command = env_setup_command(["/tmp/setup-a.bash", "/tmp/setup-b.bash"])
    assert "/tmp/setup-a.bash /tmp/setup-b.bash" in command


def test_default_globs_are_absolute_patterns() -> None:
    for pattern in DEFAULT_SETUP_GLOBS:
        assert pattern.startswith("/")
