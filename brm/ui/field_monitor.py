"""足球地图监视窗口：把 brain.log 中的机器人位置与球位置画在 kid_size 场地上。

设计为多机器人可扩展：
* :class:`FieldCanvas` 用 ``dict[name] -> RobotTrack`` 保存任意数量机器人；
* :class:`FieldMonitorDialog.add_source` 可以为一个窗口追加多路 brain.log 数据源；
* 每台机器人有独立颜色，球用黄色实心点 + 机器人同色描边区分来源。

当前 MainWindow 只接入当前机器人的一路 brain.log，未来可直接为其它机器人
调用 ``add_source()``，无需改动画布和解析器。
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.brain_log_parser import Ball2D, BrainLogParser, Pose2D
from ..core.errors import BrmError
from ..core.field_geometry import KID_SIZE, FieldDimensions
from .workers import TailLogWorker

ROBOT_COLORS = [
    QColor("#4da3ff"),  # 蓝
    QColor("#ff9f43"),  # 橙
    QColor("#2ed573"),  # 绿
    QColor("#e056fd"),  # 紫
    QColor("#ff6b81"),  # 粉
    QColor("#f7d154"),  # 黄
]
BALL_COLOR = QColor("#ffd166")
FIELD_COLOR = QColor("#14532d")
FIELD_EDGE = QColor("#e8f5e9")
BACKGROUND = QColor("#08130d")


@dataclass
class RobotTrack:
    name: str
    color: QColor
    pose: Optional[Pose2D] = None
    ball: Optional[Ball2D] = None
    trail: Deque[tuple] = field(default_factory=lambda: deque(maxlen=240))
    last_update: float = 0.0


@dataclass
class DataSource:
    name: str
    remote_path: str
    color: QColor
    worker: Optional[TailLogWorker] = None
    parser: BrainLogParser = field(default_factory=BrainLogParser)
    connected: bool = False


class FieldCanvas(QWidget):
    """按 kid_size 真实比例绘制足球场、机器人与球。"""

    def __init__(self, dimensions: FieldDimensions = KID_SIZE, parent=None) -> None:
        super().__init__(parent)
        self.dimensions = dimensions
        self._tracks: Dict[str, RobotTrack] = {}
        self.setMinimumSize(620, 420)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ------------------------------------------------------------------
    def update_track(
        self,
        name: str,
        color: QColor,
        pose: Optional[Pose2D],
        ball: Optional[Ball2D],
    ) -> None:
        track = self._tracks.get(name)
        if track is None:
            track = RobotTrack(name=name, color=color)
            self._tracks[name] = track
        track.color = color
        if pose is not None:
            if (
                track.pose is None
                or abs(track.pose.x - pose.x) > 1e-6
                or abs(track.pose.y - pose.y) > 1e-6
            ):
                track.trail.append((pose.x, pose.y))
            track.pose = pose
        if ball is not None:
            track.ball = ball
        track.last_update = time.monotonic()
        self.update()

    def clear_trails(self) -> None:
        for track in self._tracks.values():
            track.trail.clear()
        self.update()

    def tracks(self) -> Dict[str, RobotTrack]:
        return dict(self._tracks)

    # ------------------------------------------------------------------
    def _field_rect(self) -> QRectF:
        margin = 46.0
        available_w = max(10.0, self.width() - 2 * margin)
        available_h = max(10.0, self.height() - 2 * margin)
        scale = min(
            available_w / self.dimensions.length,
            available_h / self.dimensions.width,
        )
        field_w = self.dimensions.length * scale
        field_h = self.dimensions.width * scale
        left = (self.width() - field_w) / 2.0
        top = (self.height() - field_h) / 2.0
        return QRectF(left, top, field_w, field_h)

    def _to_screen(self, rect: QRectF, x: float, y: float) -> QPointF:
        scale = rect.width() / self.dimensions.length
        return QPointF(
            rect.center().x() + x * scale,
            rect.center().y() - y * scale,
        )

    def _scale(self, rect: QRectF) -> float:
        return rect.width() / self.dimensions.length

    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), BACKGROUND)

        rect = self._field_rect()
        scale = self._scale(rect)
        self._paint_field(painter, rect, scale)

        for track in self._tracks.values():
            self._paint_track(painter, rect, scale, track)

        if not self._tracks:
            painter.setPen(QPen(QColor("#8b929c")))
            font = QFont()
            font.setPointSize(13)
            painter.setFont(font)
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "等待 brain.log 中的 FIELD_POSE / [BALL] 数据…",
            )

    # ------------------------------------------------------------------
    def _paint_field(self, painter: QPainter, rect: QRectF, scale: float) -> None:
        dim = self.dimensions
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(FIELD_COLOR)
        painter.drawRect(rect)

        line_pen = QPen(FIELD_EDGE)
        line_pen.setWidthF(1.8)
        painter.setPen(line_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # 边线 / 中线 / 中圈
        painter.drawRect(rect)
        painter.drawLine(
            self._to_screen(rect, 0, -dim.half_width),
            self._to_screen(rect, 0, dim.half_width),
        )
        center = self._to_screen(rect, 0, 0)
        painter.drawEllipse(center, dim.center_circle_radius * scale, dim.center_circle_radius * scale)
        painter.setBrush(FIELD_EDGE)
        painter.drawEllipse(center, max(2.0, 0.08 * scale), max(2.0, 0.08 * scale))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # 禁区（左右）
        for sign in (-1, 1):
            x_goal = sign * dim.half_length
            x_area = sign * (dim.half_length - dim.penalty_area_length)
            area = QRectF(
                self._to_screen(
                    rect, min(x_goal, x_area), -dim.penalty_area_width / 2
                ),
                self._to_screen(
                    rect, max(x_goal, x_area), dim.penalty_area_width / 2
                ),
            )
            painter.drawRect(area)
            painter.setBrush(FIELD_EDGE)
            painter.drawEllipse(
                self._to_screen(
                    rect,
                    sign * (dim.half_length - dim.penalty_mark_distance),
                    0,
                ),
                max(2.0, 0.07 * scale),
                max(2.0, 0.07 * scale),
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)

        # 球门区（左右）
        for sign in (-1, 1):
            x_goal = sign * dim.half_length
            x_area = sign * (dim.half_length - dim.goal_area_length)
            area = QRectF(
                self._to_screen(
                    rect, min(x_goal, x_area), -dim.goal_area_width / 2
                ),
                self._to_screen(
                    rect, max(x_goal, x_area), dim.goal_area_width / 2
                ),
            )
            painter.drawRect(area)

        # 球门（场地外侧的小矩形）
        for sign in (-1, 1):
            x_inner = sign * dim.half_length
            x_outer = sign * (dim.half_length + dim.goal_depth)
            goal = QRectF(
                self._to_screen(rect, min(x_outer, x_inner), -dim.goal_width / 2),
                self._to_screen(rect, max(x_outer, x_inner), dim.goal_width / 2),
            )
            painter.drawRect(goal)

        # 尺寸说明
        painter.setPen(QPen(QColor("#8b929c")))
        font = QFont()
        font.setPointSize(9)
        painter.setFont(font)
        painter.drawText(
            QRectF(rect.left(), rect.bottom() + 8, rect.width(), 22),
            Qt.AlignmentFlag.AlignHCenter,
            f"kid_size  长 {dim.length:g} m × 宽 {dim.width:g} m",
        )
        painter.restore()

    # ------------------------------------------------------------------
    def _paint_track(
        self, painter: QPainter, rect: QRectF, scale: float, track: RobotTrack
    ) -> None:
        painter.save()
        # 轨迹
        if len(track.trail) >= 2:
            trail_pen = QPen(QColor(track.color.red(), track.color.green(), track.color.blue(), 110))
            trail_pen.setWidthF(2.0)
            painter.setPen(trail_pen)
            points = [self._to_screen(rect, x, y) for x, y in track.trail]
            for first, second in zip(points, points[1:]):
                painter.drawLine(first, second)

        if track.pose is not None:
            center = self._to_screen(rect, track.pose.x, track.pose.y)
            size = max(9.0, min(20.0, 0.42 * scale))
            angle = -track.pose.theta  # 屏幕 y 轴向下
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            local = [
                QPointF(size, 0.0),
                QPointF(-size * 0.65, size * 0.62),
                QPointF(-size * 0.65, -size * 0.62),
            ]
            polygon = QPolygonF(
                [
                    QPointF(
                        center.x() + point.x() * cos_a - point.y() * sin_a,
                        center.y() + point.x() * sin_a + point.y() * cos_a,
                    )
                    for point in local
                ]
            )
            fill = QColor(track.color)
            if track.pose.calibrated is False:
                fill.setAlpha(140)
            painter.setPen(QPen(Qt.GlobalColor.white, 1.4))
            painter.setBrush(fill)
            painter.drawPolygon(polygon)

            # 机器人名字
            label_font = QFont()
            label_font.setPointSize(9)
            label_font.setBold(True)
            painter.setFont(label_font)
            painter.setPen(QPen(track.color.lighter(120)))
            painter.drawText(
                QRectF(center.x() - 80, center.y() - size - 22, 160, 18),
                Qt.AlignmentFlag.AlignHCenter,
                track.name,
            )

        if track.ball is not None:
            ball_center = self._to_screen(rect, track.ball.x, track.ball.y)
            radius = max(4.0, 0.11 * scale)
            painter.setPen(QPen(track.color, 2.0))
            painter.setBrush(BALL_COLOR)
            painter.drawEllipse(ball_center, radius, radius)
        painter.restore()


class FieldMonitorDialog(QDialog):
    """非模态足球地图监视窗口，可同时接入多路 brain.log。"""

    def __init__(
        self,
        session,
        remote_path: str,
        source_name: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._closed = False
        self._sources: Dict[str, DataSource] = {}
        self._source_order: List[str] = []
        self._legend_labels: Dict[str, QLabel] = {}
        self._dirty = False

        self.setWindowTitle(
            f"足球地图监视{' - ' + source_name if source_name else ''}"
        )
        self.resize(1000, 700)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("brain.log → kid_size 足球地图")
        title.setStyleSheet("font-size: 15px; font-weight: 700; color: #f0f3f7;")
        self._status_label = QLabel("正在连接…")
        self._status_label.setObjectName("MutedLabel")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._status_label)
        layout.addLayout(header)

        self.canvas = FieldCanvas(parent=self)
        layout.addWidget(self.canvas, 1)

        legend_row = QHBoxLayout()
        legend_row.setSpacing(14)
        self._legend_layout = legend_row
        ball_hint = QLabel("球：黄色实心点（同色描边表示来源机器人）")
        ball_hint.setObjectName("MutedLabel")
        legend_row.addStretch(1)
        legend_row.addWidget(ball_hint)
        layout.addLayout(legend_row)

        footer = QHBoxLayout()
        self._stats_label = QLabel("等待数据…")
        self._stats_label.setObjectName("MutedLabel")
        clear_button = QPushButton("清空轨迹")
        clear_button.setObjectName("FlatButton")
        clear_button.clicked.connect(self.canvas.clear_trails)
        close_button = QPushButton("关闭")
        close_button.setObjectName("FlatButton")
        close_button.clicked.connect(self.close)
        footer.addWidget(self._stats_label, 1)
        footer.addWidget(clear_button)
        footer.addWidget(close_button)
        layout.addLayout(footer)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(120)
        self._refresh_timer.timeout.connect(self._refresh_ui)
        self._refresh_timer.start()

        if session is not None and remote_path:
            self.add_source(source_name or "robot", session, remote_path)

    # ------------------------------------------------------------------
    def add_source(
        self,
        name: str,
        session,
        remote_path: str,
        color: Optional[QColor] = None,
    ) -> str:
        """追加一路 brain.log 数据源（未来多机器人监视的接口）。"""
        base = name or "robot"
        unique = base
        index = 2
        while unique in self._sources:
            unique = f"{base}-{index}"
            index += 1
        if color is None:
            color = ROBOT_COLORS[len(self._sources) % len(ROBOT_COLORS)]

        worker = TailLogWorker(session, remote_path)
        source = DataSource(
            name=unique,
            remote_path=remote_path,
            color=color,
            worker=worker,
            connected=True,
        )
        self._sources[unique] = source
        self._source_order.append(unique)

        worker.stream_line.connect(lambda line, key=unique: self._on_line(key, line))
        worker.failed.connect(lambda error, key=unique: self._on_worker_failed(key, error))
        worker.succeeded.connect(
            lambda code, key=unique: self._on_worker_succeeded(key, code)
        )
        worker.finished.connect(lambda key=unique: self._on_worker_finished(key))
        worker.start()

        legend = QLabel(f"● {unique}  (x, y, θ)")
        legend.setStyleSheet(f"color: {color.name()}; font-weight: 600;")
        self._legend_labels[unique] = legend
        self._legend_layout.insertWidget(max(0, self._legend_layout.count() - 1), legend)
        self._status_label.setText("正在监视…")
        return unique

    # ------------------------------------------------------------------
    def _on_line(self, name: str, line: str) -> None:
        if self._closed:
            return
        source = self._sources.get(name)
        if source is None:
            return
        if source.parser.feed(line):
            self.canvas.update_track(
                name, source.color, source.parser.pose, source.parser.ball
            )
            self._dirty = True

    def _refresh_ui(self) -> None:
        if self._closed:
            return
        if self._dirty:
            self._dirty = False
            self.canvas.update()
        parts: List[str] = []
        for name in self._source_order:
            source = self._sources.get(name)
            track = self.canvas.tracks().get(name)
            if source is None:
                continue
            if track is None or track.pose is None:
                parts.append(f"{name}: 等待 FIELD_POSE")
                continue
            pose = track.pose
            status = "已定位" if pose.calibrated else ("未定位" if pose.calibrated is False else "")
            parts.append(
                f"{name}: ({pose.x:+.2f}, {pose.y:+.2f}, "
                f"{math.degrees(pose.theta):+.1f}°) {status}"
            )
            label = self._legend_labels.get(name)
            if label is not None:
                ball_text = ""
                if track.ball is not None:
                    ball_text = f"  球=({track.ball.x:+.2f},{track.ball.y:+.2f})"
                label.setText(f"● {name}  ({pose.x:+.2f}, {pose.y:+.2f}){ball_text}")
        if parts:
            self._stats_label.setText("   |   ".join(parts))
            self._status_label.setText("监视中")
        else:
            self._stats_label.setText("等待 brain.log 数据…")

    # ------------------------------------------------------------------
    def _on_worker_failed(self, name: str, error: BrmError) -> None:
        if self._closed:
            return
        self._status_label.setText(f"{name} 读取失败：{error.title}")
        self._status_label.setStyleSheet("color: #ff8a8a;")

    def _on_worker_succeeded(self, name: str, exit_code) -> None:
        if self._closed:
            return
        try:
            code = int(exit_code)
        except (TypeError, ValueError):
            code = -1
        if code not in (0, -1):
            self._status_label.setText(f"{name} 监视结束（tail 退出码 {code}）")

    def _on_worker_finished(self, name: str) -> None:
        source = self._sources.get(name)
        if source is not None:
            source.connected = False
            if source.worker is not None:
                source.worker.deleteLater()
            source.worker = None

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        self._closed = True
        self._refresh_timer.stop()
        for source in list(self._sources.values()):
            worker = source.worker
            if worker is None:
                continue
            try:
                worker.stop()
            except Exception:
                pass
            if not worker.wait(2000):
                worker.terminate()
                worker.wait(500)
            source.worker = None
        super().closeEvent(event)
