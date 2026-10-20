"""brain.log 实时监视窗口（tail -f）。

特点：
* 通过独立 SSH channel 持续执行 ``tail -f <项目目录>/brain.log``；
* 新日志按批次追加，自动滚动到底部；
* 可选「自动调整窗口大小」：每一批新日志到达时，按该帧的内容宽高
  自动调整窗口大小（受屏幕尺寸限制），跟随 brain.log 里每一帧的大小变化。
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..core.errors import BrmError
from .workers import TailLogWorker


class BrainLogMonitorDialog(QDialog):
    """实时 tail -f brain.log 的非模态窗口。"""

    def __init__(
        self,
        session,
        remote_path: str,
        project_name: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.remote_path = remote_path
        self.project_name = project_name
        self._closed = False
        self._worker: Optional[TailLogWorker] = None
        self._pending_lines: List[str] = []
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self._flush_pending)

        self.setWindowTitle(
            f"实时监视 brain.log{' - ' + project_name if project_name else ''}"
        )
        self.resize(900, 600)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        path_label = QLabel(remote_path)
        path_label.setObjectName("ProjectPath")
        path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._status_label = QLabel("正在连接…")
        self._status_label.setObjectName("MutedLabel")
        header.addWidget(QLabel("tail -f"), 0)
        header.addWidget(path_label, 1)
        header.addWidget(self._status_label)
        layout.addLayout(header)

        self._view = QPlainTextEdit()
        self._view.setObjectName("LogView")
        self._view.setReadOnly(True)
        self._view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._view.setMaximumBlockCount(50000)
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(11)
        self._view.setFont(mono)
        layout.addWidget(self._view, 1)

        footer = QHBoxLayout()
        self._autosize_check = QCheckBox("自动调整窗口大小（跟随日志帧）")
        self._autosize_check.setChecked(True)
        self._autosize_check.setToolTip(
            "每一批新日志到达时，按该批内容的宽高自动调整窗口大小；"
            "如不想被自动调整，可取消勾选后手动拖动窗口。"
        )
        clear_button = QPushButton("清空")
        clear_button.setObjectName("FlatButton")
        clear_button.clicked.connect(self._view.clear)
        close_button = QPushButton("关闭")
        close_button.setObjectName("FlatButton")
        close_button.clicked.connect(self.close)
        footer.addWidget(self._autosize_check)
        footer.addStretch(1)
        footer.addWidget(clear_button)
        footer.addWidget(close_button)
        layout.addLayout(footer)

        self._start_worker(session)

    # ------------------------------------------------------------------
    def _start_worker(self, session) -> None:
        worker = TailLogWorker(session, self.remote_path)
        self._worker = worker
        worker.stream_line.connect(self._on_line)
        worker.failed.connect(self._on_worker_failed)
        worker.succeeded.connect(self._on_worker_succeeded)
        worker.finished.connect(self._on_thread_finished)
        self._status_label.setText("正在监视…")
        worker.start()

    # ------------------------------------------------------------------
    def _on_line(self, line: str) -> None:
        if self._closed:
            return
        self._pending_lines.append(line)
        if not self._flush_timer.isActive():
            self._flush_timer.start(200)

    def _flush_pending(self) -> None:
        if self._closed or not self._pending_lines:
            return
        lines = self._pending_lines
        self._pending_lines = []

        scrollbar = self._view.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 4
        self._view.appendPlainText("\n".join(lines))
        if at_bottom:
            scrollbar.setValue(scrollbar.maximum())

        if self._autosize_check.isChecked():
            self._autofit(lines)

    def _autofit(self, lines: List[str]) -> None:
        """按这一批新日志的内容宽高调整窗口大小（有上下限）。"""
        if not lines:
            return
        metrics = QFontMetrics(self._view.font())
        recent = lines[-300:]
        max_width = max(
            (metrics.horizontalAdvance(line) for line in recent), default=0
        )
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        width = max(560, min(max_width + 110, int(available.width() * 0.92)))
        height = max(
            340,
            min(
                len(lines) * metrics.lineSpacing() + 160,
                int(available.height() * 0.82),
            ),
        )
        self.resize(width, height)

    # ------------------------------------------------------------------
    def _on_worker_failed(self, error: BrmError) -> None:
        if self._closed:
            return
        self._status_label.setText(f"读取失败：{error.title}")
        self._status_label.setStyleSheet("color: #ff8a8a;")
        self._view.appendPlainText(f"[错误] {error.user_message()}")

    def _on_worker_succeeded(self, exit_code) -> None:
        if self._closed:
            return
        try:
            code = int(exit_code)
        except (TypeError, ValueError):
            code = -1
        self._status_label.setStyleSheet("color: #8b929c;")
        if code not in (0, -1):
            self._status_label.setText(f"监视结束（tail 退出码 {code}）")
            self._view.appendPlainText(
                f"[提示] tail 已退出（退出码 {code}），请检查 brain.log 是否存在。"
            )
        else:
            self._status_label.setText("监视结束")

    def _on_thread_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        self._closed = True
        self._flush_timer.stop()
        worker = self._worker
        if worker is not None:
            try:
                worker.stop()
            except Exception:
                pass
            if not worker.wait(2000):
                worker.terminate()
                worker.wait(500)
            if self._worker is worker:
                self._worker = None
        super().closeEvent(event)
