"""解析 brain.log 中的机器人位姿与球位置。

真实 brain.log 行（可能带 ANSI 颜色与 ROS 节点前缀）示例::

    [brain_node-1] FIELD_POSE:
    [brain_node-1]     x: 0.000 m    y: 0.000 m    theta: 0.309 rad (17.7 deg)    Calibrated: NO
    [brain_node-1] [BALL] fld=(2.23,1.92) rbt=(2.71,1.16) r=2.94m yaw=0.40 c=50 det=Y knw=Y

解析器每个数据源（每台机器人）一个实例；一个日志流只更新自己的 RobotState，
上层可以并行创建多个实例实现多机器人监视。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_NODE_PREFIX_RE = re.compile(r"^\s*\[brain[^\]]*\]\s*", re.IGNORECASE)
_POSE_RE = re.compile(
    r"x:\s*(?P<x>-?\d+(?:\.\d+)?)\s*m\s+"
    r"y:\s*(?P<y>-?\d+(?:\.\d+)?)\s*m\s+"
    r"theta:\s*(?P<theta>-?\d+(?:\.\d+)?)\s*rad"
)
_CALIBRATED_RE = re.compile(r"Calibrated:\s*(?P<cal>YES|NO)\b", re.IGNORECASE)
_BALL_RE = re.compile(
    r"\[BALL\]\s+fld=\(\s*(?P<x>-?\d+(?:\.\d+)?)\s*,\s*"
    r"(?P<y>-?\d+(?:\.\d+)?)\s*\)"
)
_BALL_CONF_RE = re.compile(r"\bc=(?P<conf>\d+)")
_BALL_DET_RE = re.compile(r"\bdet=(?P<det>[YN])", re.IGNORECASE)
_BALL_KNOWN_RE = re.compile(r"\bknw=(?P<known>[YN])", re.IGNORECASE)
_TEAM_RE = re.compile(r"TeamID:\s*(?P<team>\d+)")
_PLAYER_RE = re.compile(r"PlayerID:\s*(?P<player>\d+)")


@dataclass
class Pose2D:
    x: float
    y: float
    theta: float
    calibrated: Optional[bool] = None


@dataclass
class Ball2D:
    x: float
    y: float
    confidence: Optional[int] = None
    detected: Optional[bool] = None
    known: Optional[bool] = None


class BrainLogParser:
    """增量解析 brain.log 行，保存最新机器人位姿与球位置。"""

    def __init__(self) -> None:
        self.pose: Optional[Pose2D] = None
        self.ball: Optional[Ball2D] = None
        self.team_id: Optional[int] = None
        self.player_id: Optional[int] = None
        self.line_count = 0
        self.update_count = 0

    @staticmethod
    def _clean(line: str) -> str:
        text = _ANSI_RE.sub("", line or "")
        return _NODE_PREFIX_RE.sub("", text)

    def feed(self, line: str) -> bool:
        """喂入一行日志；有新的位姿或球数据时返回 True。"""
        self.line_count += 1
        text = self._clean(line)
        changed = False

        match = _POSE_RE.search(text)
        if match:
            x = float(match.group("x"))
            y = float(match.group("y"))
            theta = float(match.group("theta"))
            if math.isfinite(x) and math.isfinite(y) and math.isfinite(theta):
                cal_match = _CALIBRATED_RE.search(text)
                calibrated = None
                if cal_match:
                    calibrated = cal_match.group("cal").upper() == "YES"
                self.pose = Pose2D(x, y, theta, calibrated)
                changed = True

        match = _BALL_RE.search(text)
        if match:
            x = float(match.group("x"))
            y = float(match.group("y"))
            if math.isfinite(x) and math.isfinite(y):
                conf_match = _BALL_CONF_RE.search(text)
                det_match = _BALL_DET_RE.search(text)
                known_match = _BALL_KNOWN_RE.search(text)
                self.ball = Ball2D(
                    x=x,
                    y=y,
                    confidence=int(conf_match.group("conf")) if conf_match else None,
                    detected=(
                        det_match.group("det").upper() == "Y" if det_match else None
                    ),
                    known=(
                        known_match.group("known").upper() == "Y"
                        if known_match
                        else None
                    ),
                )
                changed = True

        team_match = _TEAM_RE.search(text)
        if team_match:
            self.team_id = int(team_match.group("team"))
        player_match = _PLAYER_RE.search(text)
        if player_match:
            self.player_id = int(player_match.group("player"))

        if changed:
            self.update_count += 1
        return changed
