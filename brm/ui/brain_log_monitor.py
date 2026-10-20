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
        self._recent_lines: List[str] = []
        self._last_batch: List[str] = []
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self._flush_pending)
        # 自动适配窗口大小用独立的防抖定时器：日志持续输出时保持窗口稳定，
        # 等日志停顿约 0.7 秒后再按最新一帧调整一次。
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._apply_auto_fit)

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
            "日志持续输出时窗口大小保持稳定，不会每一帧都跳动；"
            "日志停顿约 0.7 秒后，才按最新一帧内容自动适配一次。"
            "如不想被自动调整，可取消勾选后手动拖动窗口。"
        )
        self._autosize_check.toggled.connect(self._on_autosize_toggled)
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
            self._flush_timer.start(250)

    def _flush_pending(self) -> None:
        if self._closed or not self._pending_lines:
            return
        lines = self._pending_lines
        self._pending_lines = []
        self._last_batch = lines
        self._recent_lines.extend(lines)
        if len(self._recent_lines) > 600:
            self._recent_lines = self._recent_lines[-600:]

        scrollbar = self._view.verticalScrollBar()
        old_value = scrollbar.value()
        at_bottom = old_value >= scrollbar.maximum() - 4

        # 一次插入整批文本，并在插入期间关闭重绘，避免逐行刷新造成闪烁。
        self._view.setUpdatesEnabled(False)
        try:
            self._view.appendPlainText("\n".join(lines))
        finally:
            self._view.setUpdatesEnabled(True)

        if at_bottom:
            scrollbar.setValue(scrollbar.maximum())
        else:
            scrollbar.setValue(old_value)

        if self._autosize_check.isChecked():
            # 防抖：持续有日志到达时不断重置，窗口保持稳定；
            # 只有日志停顿后才调整一次大小。
            self._resize_timer.start(700)

    def _on_autosize_toggled(self, checked: bool) -> None:
        if checked:
            self._resize_timer.start(500)
        else:
            self._resize_timer.stop()

    def _apply_auto_fit(self) -> None:
        if self._closed or not self._autosize_check.isChecked():
            return
        if self._last_batch:
            self._autofit(self._last_batch)

    def _autofit(self, lines: List[str]) -> None:
        """按最新一帧日志的宽高调整窗口大小（带迟滞，避免反复微调）。"""
        if not lines:
            return
        if self.isMaximized() or self.isFullScreen():
            return
        metrics = QFontMetrics(self._view.font())
        recent = self._recent_lines[-400:] or lines[-300:]
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
        # 变化很小时不调整，避免窗口边缘一直抖动。
        if abs(width - self.width()) < 50 and abs(height - self.height()) < 60:
            return
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
        self._resize_timer.stop()
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
