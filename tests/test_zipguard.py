"""zip 白名单 / 非法路径 / 项目目录推断的单元测试（R5 / R6）。"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from brm.core.errors import FileValidationError
from brm.core.zipguard import inspect_zip, resolve_project_subdir, zip_stem
from tests.helpers import build_zip


def test_valid_single_top_dir(tmp_path: Path) -> None:
    archive = build_zip(tmp_path / "demo.zip", {"demo/scripts/build.sh": "echo hi"})
    inspection = inspect_zip(archive)
    assert inspection.entry_count == 1
    assert inspection.top_level_dirs == ("demo",)
    assert inspection.project_subdir == "demo"
    assert inspection.stem == "demo"


def test_multiple_top_dirs_has_no_project_subdir(tmp_path: Path) -> None:
    archive = build_zip(
        tmp_path / "multi.zip",
        {"a/x.txt": "1", "b/y.txt": "2"},
    )
    inspection = inspect_zip(archive)
    assert inspection.project_subdir is None
    assert set(inspection.top_level_dirs) == {"a", "b"}


def test_root_files_has_no_project_subdir(tmp_path: Path) -> None:
    archive = build_zip(
        tmp_path / "flat.zip",
        {"build.sh": "echo hi", "start.sh": "echo hi", "readme.txt": "hi"},
    )
    inspection = inspect_zip(archive)
    assert inspection.project_subdir is None
    assert set(inspection.top_level_files) == {"build.sh", "start.sh", "readme.txt"}


def test_macos_metadata_is_ignored(tmp_path: Path) -> None:
    archive = build_zip(
        tmp_path / "mac.zip",
        {
            "__MACOSX/._demo": "meta",
            "demo/scripts/build.sh": "echo hi",
            "demo/.DS_Store": "meta",
            ".DS_Store": "meta",
        },
    )
    inspection = inspect_zip(archive)
    assert inspection.top_level_dirs == ("demo",)
    assert inspection.project_subdir == "demo"


def test_extension_is_case_insensitive(tmp_path: Path) -> None:
    archive = build_zip(tmp_path / "DEMO.ZIP", {"demo/x.txt": "1"})
    assert inspect_zip(archive).project_subdir == "demo"


def test_non_zip_extension_rejected(tmp_path: Path) -> None:
    archive = build_zip(tmp_path / "demo.rar", {"demo/x.txt": "1"})
    with pytest.raises(FileValidationError) as excinfo:
        inspect_zip(archive)
    assert "只支持 .zip 压缩包，当前文件：demo.rar" in excinfo.value.message


def test_renamed_file_rejected_by_magic(tmp_path: Path) -> None:
    fake = tmp_path / "fake.zip"
    fake.write_text("this is not a zip file", encoding="utf-8")
    with pytest.raises(FileValidationError) as excinfo:
        inspect_zip(fake)
    assert "不是有效的 zip" in excinfo.value.message


def test_empty_zip_is_allowed(tmp_path: Path) -> None:
    archive = tmp_path / "empty.zip"
    with zipfile.ZipFile(archive, "w"):
        pass
    inspection = inspect_zip(archive)
    assert inspection.entry_count == 0
    assert inspection.project_subdir is None


@pytest.mark.parametrize(
    "bad_entry",
    [
        "../evil.txt",
        "demo/../../evil.txt",
        "/etc/passwd",
        "C:/Windows/system32/evil.dll",
        "demo\\..\\..\\evil.txt",
    ],
)
def test_illegal_paths_rejected(tmp_path: Path, bad_entry: str) -> None:
    archive = build_zip(tmp_path / "bad.zip", {"demo/ok.txt": "1", bad_entry: "evil"})
    with pytest.raises(FileValidationError) as excinfo:
        inspect_zip(archive)
    assert "压缩包包含非法路径" in excinfo.value.message


def test_zip_stem() -> None:
    assert zip_stem(Path("/tmp/demo.zip")) == "demo"
    assert zip_stem(Path("/tmp/demo.v1.ZIP")) == "demo.v1"


def test_resolve_project_subdir_with_actual_entries(tmp_path: Path) -> None:
    archive = build_zip(tmp_path / "demo.zip", {"demo/a.txt": "1"})
    inspection = inspect_zip(archive)
    assert resolve_project_subdir(inspection, ["demo", "README.md"]) == "demo"
    # 解压结果与 zip 结构不一致时，以实际唯一的目录为准
    assert resolve_project_subdir(inspection, ["other"]) == "other"
    assert resolve_project_subdir(inspection, ["a.txt", "b.txt"]) is None
