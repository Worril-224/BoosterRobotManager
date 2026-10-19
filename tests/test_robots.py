"""配置 / 密码存储与复用逻辑的单元测试（R0 / R1）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brm.core.errors import BrmError
from brm.core.robots import (
    Robot,
    RobotStore,
    deobfuscate_password,
    obfuscate_password,
)


def make_store(tmp_path: Path) -> RobotStore:
    store = RobotStore(tmp_path / "config" / "robots.json")
    store.load()
    return store


def test_first_run_creates_default_robot(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    assert store.config_path.exists()
    assert len(store.robots) == 1
    assert store.robots[0].name == "107"
    assert store.robots[0].host == "192.168.1.107"
    assert store.robots[0].port == 22
    assert store.robots[0].username == "booster"
    assert store.robots[0].password_b64 == ""
    assert not store.settings.has_default_password()
    assert not store.has_any_password()


def test_obfuscate_is_not_plaintext() -> None:
    stored = obfuscate_password("hunter2")
    assert "hunter2" not in stored
    assert stored.startswith("b64:")
    assert deobfuscate_password(stored) == "hunter2"
    assert deobfuscate_password("") == ""


def test_default_password_persists_and_reuses(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.set_default_password("SuperSecret-123")
    assert store.resolve_password(store.robots[0]) == "SuperSecret-123"

    raw = store.config_path.read_text(encoding="utf-8")
    assert "SuperSecret-123" not in raw
    assert "b64:" in raw

    # 模拟程序重启：重新加载配置后仍然能自动取到密码
    reloaded = RobotStore(store.config_path)
    reloaded.load()
    assert reloaded.resolve_password(reloaded.robots[0]) == "SuperSecret-123"


def test_per_robot_password_overrides_default(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.set_default_password("default-pwd")
    store.add_robot(Robot(name="108", host="192.168.1.108"))
    store.set_robot_password("108", "robot108-pwd")

    robot_107 = store.get_robot("107")
    robot_108 = store.get_robot("108")
    assert robot_107 is not None and robot_108 is not None
    assert store.resolve_password(robot_107) == "default-pwd"
    assert store.resolve_password(robot_108) == "robot108-pwd"
    assert store.password_source(robot_107) == "默认密码"
    assert store.password_source(robot_108) == "单独设置"

    store.clear_all_robot_passwords()
    assert store.resolve_password(store.get_robot("108")) == "default-pwd"


def test_key_auth_has_no_password(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.set_default_password("default-pwd")
    robot = store.get_robot("107")
    assert robot is not None
    robot.auth_type = "key"
    robot.key_path = "/tmp/id_rsa"
    assert store.resolve_password(robot) is None
    assert store.password_source(robot) == "私钥登录"


def test_robot_crud(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.add_robot(Robot(name="108", host="192.168.1.108", port=2222))
    assert [robot.name for robot in store.robots] == ["107", "108"]

    updated = store.get_robot("108")
    assert updated is not None
    updated.host = "192.168.1.109"
    store.update_robot("108", updated)
    assert store.get_robot("108").host == "192.168.1.109"  # type: ignore[union-attr]

    with pytest.raises(BrmError):
        store.add_robot(Robot(name="108", host="x"))

    store.remove_robot("108")
    assert store.get_robot("108") is None


def test_invalid_config_raises_readable_error(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "robots.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{not valid json", encoding="utf-8")
    store = RobotStore(config_path)
    with pytest.raises(BrmError) as excinfo:
        store.load()
    assert "配置文件读取失败" in excinfo.value.message


def test_config_contains_no_hardcoded_password(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    data = json.loads(store.config_path.read_text(encoding="utf-8"))
    for robot in data["robots"]:
        assert robot["password"] == ""
    assert data["settings"]["default_password"] == ""


def test_default_config_factory_has_empty_password() -> None:
    """程序自动生成的默认配置不得包含任何密码（真实配置由用户设置）。"""
    from brm.core.robots import default_config

    data = default_config()
    assert data["robots"][0]["host"] == "192.168.1.107"
    assert data["robots"][0]["password"] == ""
    assert data["settings"]["default_password"] == ""
