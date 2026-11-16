"""Checks on the result. Section 07 adds the minimum-requirements table here."""

import math

from shapely.geometry import LineString, Point
from shapely.ops import unary_union

from . import config


def _samples(axes, step):
    points = []
    for (x1, y1), (x2, y2) in axes:
        n = max(1, round(math.hypot(x2 - x1, y2 - y1) / step))
        points += [(x1 + (x2 - x1) * i / n, y1 + (y2 - y1) * i / n) for i in range(n + 1)]
    return points


def wall_pr(pred_axes, true_axes, tol=config.WALL_MATCH_TOL, step=0.05) -> tuple[float, float]:
    """Length-weighted precision and recall of wall axes: a point counts if the other set is within tol."""
    def share(axes, others):
        if not axes:
            return 0.0
        if not others:
            return 0.0
        target = unary_union([LineString(a) for a in others])
        points = _samples(axes, step)
        return sum(target.distance(Point(p)) <= tol + 1e-9 for p in points) / len(points)

    return share(pred_axes, true_axes), share(true_axes, pred_axes)
