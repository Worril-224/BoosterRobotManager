"""kid_size 足球场地几何参数。

数据来自 T2 工程 ``FD_KIDSIZE{9,6,1.5,2.6,0.75,2,5,1,3}``：
场地长 9 m、宽 6 m，球门宽 2.6 m，中圈半径 0.75 m，
禁区 2 m × 5 m，球门区 1 m × 3 m，点球点距门线 1.5 m。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldDimensions:
    length: float = 9.0
    width: float = 6.0
    goal_width: float = 2.6
    center_circle_radius: float = 0.75
    penalty_area_length: float = 2.0
    penalty_area_width: float = 5.0
    goal_area_length: float = 1.0
    goal_area_width: float = 3.0
    penalty_mark_distance: float = 1.5
    goal_depth: float = 0.5

    @property
    def half_length(self) -> float:
        return self.length / 2.0

    @property
    def half_width(self) -> float:
        return self.width / 2.0


KID_SIZE = FieldDimensions()
