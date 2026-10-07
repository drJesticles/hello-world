"""Bezier and path geometry helpers (pure Qt/numpy, no UI)."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPainterPath, QTransform


def dist(a: QPointF, b: QPointF) -> float:
    return math.hypot(a.x() - b.x(), a.y() - b.y())


def lerp(a: QPointF, b: QPointF, t: float) -> QPointF:
    return QPointF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t)


def split_cubic(p0, p1, p2, p3, t=0.5):
    """De Casteljau split. Returns ((p0,a,b,m), (m,c,d,p3))."""
    a = lerp(p0, p1, t); b = lerp(p1, p2, t); c = lerp(p2, p3, t)
    d = lerp(a, b, t); e = lerp(b, c, t)
    m = lerp(d, e, t)
    return (p0, a, d, m), (m, e, c, p3)


def cubic_point(p0, p1, p2, p3, t):
    mt = 1 - t
    x = mt**3 * p0.x() + 3 * mt**2 * t * p1.x() + 3 * mt * t**2 * p2.x() + t**3 * p3.x()
    y = mt**3 * p0.y() + 3 * mt**2 * t * p1.y() + 3 * mt * t**2 * p2.y() + t**3 * p3.y()
    return QPointF(x, y)


def nearest_t_on_cubic(p0, p1, p2, p3, pt, steps=32) -> tuple[float, float]:
    """Coarse-to-fine search for the parameter nearest to pt. Returns (t, distance)."""
    best_t, best_d = 0.0, float("inf")
    for i in range(steps + 1):
        t = i / steps
        d = dist(cubic_point(p0, p1, p2, p3, t), pt)
        if d < best_d:
            best_t, best_d = t, d
    lo, hi = max(0.0, best_t - 1 / steps), min(1.0, best_t + 1 / steps)
    for _ in range(12):
        m1, m2 = lo + (hi - lo) / 3, hi - (hi - lo) / 3
        if dist(cubic_point(p0, p1, p2, p3, m1), pt) < dist(cubic_point(p0, p1, p2, p3, m2), pt):
            hi = m2
        else:
            lo = m1
    t = (lo + hi) / 2
    return t, dist(cubic_point(p0, p1, p2, p3, t), pt)


def arc_to_cubics(x1, y1, rx, ry, phi_deg, large_arc, sweep, x2, y2):
    """SVG elliptical arc -> list of cubic segments [(c1, c2, end), ...] (absolute coordinates)."""
    if rx == 0 or ry == 0:
        return [(QPointF(x1, y1), QPointF(x2, y2), QPointF(x2, y2))]
    if x1 == x2 and y1 == y2:
        return []
    phi = math.radians(phi_deg)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p = cp * dx + sp * dy
    y1p = -sp * dx + cp * dy
    rx, ry = abs(rx), abs(ry)
    lam = (x1p**2) / (rx**2) + (y1p**2) / (ry**2)
    if lam > 1:
        s = math.sqrt(lam); rx *= s; ry *= s
    num = rx**2 * ry**2 - rx**2 * y1p**2 - ry**2 * x1p**2
    den = rx**2 * y1p**2 + ry**2 * x1p**2
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large_arc == sweep:
        coef = -coef
    cxp = coef * rx * y1p / ry
    cyp = -coef * ry * x1p / rx
    cx = cp * cxp - sp * cyp + (x1 + x2) / 2
    cy = sp * cxp + cp * cyp + (y1 + y2) / 2

    def ang(ux, uy, vx, vy):
        return math.atan2(vy, vx) - math.atan2(uy, ux)

    th1 = math.atan2((y1p - cyp) / ry, (x1p - cxp) / rx)
    dth = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    dth = math.atan2(math.sin(dth), math.cos(dth))  # normalise to -pi..pi
    if not sweep and dth > 0:
        dth -= 2 * math.pi
    elif sweep and dth < 0:
        dth += 2 * math.pi
    n = max(1, int(math.ceil(abs(dth) / (math.pi / 2))))
    out = []
    for i in range(n):
        a0 = th1 + dth * i / n
        a1 = th1 + dth * (i + 1) / n
        t = 4 / 3 * math.tan((a1 - a0) / 4)

        def pt(a):
            x = rx * math.cos(a); y = ry * math.sin(a)
            return QPointF(cp * x - sp * y + cx, sp * x + cp * y + cy)

        def dpt(a):
            x = -rx * math.sin(a); y = ry * math.cos(a)
            return QPointF(cp * x - sp * y, sp * x + cp * y)

        p0, p3 = pt(a0), pt(a1)
        d0, d1 = dpt(a0), dpt(a1)
        c1 = QPointF(p0.x() + t * d0.x(), p0.y() + t * d0.y())
        c2 = QPointF(p3.x() - t * d1.x(), p3.y() - t * d1.y())
        out.append((c1, c2, p3))
    return out


def rdp(points: list[QPointF], eps: float) -> list[QPointF]:
    """Ramer-Douglas-Peucker simplification."""
    if len(points) < 3:
        return list(points)
    a, b = points[0], points[-1]
    ab = QPointF(b.x() - a.x(), b.y() - a.y())
    L = math.hypot(ab.x(), ab.y())
    idx, dmax = 0, 0.0
    for i in range(1, len(points) - 1):
        p = points[i]
        if L == 0:
            d = dist(p, a)
        else:
            d = abs(ab.x() * (a.y() - p.y()) - ab.y() * (a.x() - p.x())) / L
        if d > dmax:
            idx, dmax = i, d
    if dmax > eps:
        left = rdp(points[: idx + 1], eps)
        right = rdp(points[idx:], eps)
        return left[:-1] + right
    return [a, b]


def smooth_handles(points: list[QPointF], closed: bool, tension: float = 0.3):
    """Catmull-Rom style handles for a polyline. Returns list of (h_in, h_out) per point."""
    n = len(points)
    out = []
    for i, p in enumerate(points):
        if closed:
            prev, nxt = points[(i - 1) % n], points[(i + 1) % n]
        else:
            prev = points[i - 1] if i > 0 else p
            nxt = points[i + 1] if i < n - 1 else p
        tx, ty = (nxt.x() - prev.x()) * tension, (nxt.y() - prev.y()) * tension
        if not closed and (i == 0 or i == n - 1):
            tx *= 2; ty *= 2
        out.append((QPointF(p.x() - tx, p.y() - ty), QPointF(p.x() + tx, p.y() + ty)))
    return out


def transform_rect(t: QTransform, r: QRectF) -> QRectF:
    return t.mapRect(r)


def polygon_points(cx, cy, r, sides, rotation=-90.0, inner=None):
    pts = []
    n = sides * (2 if inner is not None else 1)
    for i in range(n):
        a = math.radians(rotation + 360.0 * i / n)
        rad = r if (inner is None or i % 2 == 0) else inner
        pts.append(QPointF(cx + rad * math.cos(a), cy + rad * math.sin(a)))
    return pts
