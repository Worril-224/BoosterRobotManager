"""pytest 公共 fixture。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 确保从项目根目录运行 pytest 时可以 import brm / tests
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.fake_remote import FakeRemote  # noqa: E402

from brm import constants  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_log_dir(tmp_path: Path, monkeypatch) -> None:
    """测试产生的部署日志统一写入临时目录，不污染项目 brm/logs/。"""
    monkeypatch.setattr(constants, "LOG_DIR", tmp_path / "brm-logs")


@pytest.fixture()
def remote(tmp_path: Path) -> FakeRemote:
    instance = FakeRemote(tmp_path / "remote")
    instance.connected = True
    yield instance
    instance.cleanup()
