"""解析编译输出中的进度信息（用于界面「编译进度」显示）。

尽量兼容常见构建工具的输出：

* CMake / Make：``[ 42%] Built target ...``
* Ninja：``[12/345] ...``
* colcon / ROS 2：``Starting >>> pkg``、``Finished <<< pkg``
* 其他：无法识别时只显示已输出行数与耗时（进度条为不确定状态）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Set

_PERCENT_RE = re.compile(r"\[\s*(\d{1,3})%\]")
_FRACTION_RE = re.compile(r"\[\s*(\d+)\s*/\s*(\d+)\s*\]")
_STARTING_RE = re.compile(r"Starting\s+>>>\s+(\S+)")
_FINISHED_RE = re.compile(r"Finished\s+<<<\s+(\S+)")


@dataclass
class BuildProgress:
    """累计编译进度状态。"""

    lines: int = 0
    max_percent: int = -1
    started: Set[str] = field(default_factory=set)
    finished: Set[str] = field(default_factory=set)

    def feed(self, line: str) -> None:
        self.lines += 1
        text = line or ""

        match = _PERCENT_RE.search(text)
        if match:
            try:
                self._update_percent(int(match.group(1)))
            except ValueError:
                pass

        match = _FRACTION_RE.search(text)
        if match:
            try:
                done, total = int(match.group(1)), int(match.group(2))
            except ValueError:
                pass
            else:
                if total > 0:
                    self._update_percent(int(done * 100 / total))

        match = _STARTING_RE.search(text)
        if match:
            self.started.add(match.group(1))

        match = _FINISHED_RE.search(text)
        if match:
            package = match.group(1)
            self.finished.add(package)
            self.started.add(package)

    def _update_percent(self, value: int) -> None:
        value = max(0, min(100, value))
        if value > self.max_percent:
            self.max_percent = value

    @property
    def percent(self) -> int:
        """可确定的百分比；无法解析时返回 -1（界面用不确定进度条）。"""
        return self.max_percent

    def summary_text(self, elapsed_seconds: float) -> str:
        """编译过程中显示的说明文字。"""
        elapsed = f"{elapsed_seconds:.1f} 秒"
        if self.finished:
            return (
                f"编译中… 已完成 {len(self.finished)} 个包"
                f"（已开始 {len(self.started)} 个）· 用时 {elapsed}"
            )
        if self.max_percent >= 0:
            return f"编译中… {self.max_percent}% · 用时 {elapsed}"
        return f"编译中… 已输出 {self.lines} 行 · 用时 {elapsed}"
