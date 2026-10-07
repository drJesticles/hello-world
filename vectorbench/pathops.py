"""Path operations: pathfinder booleans, outline stroke, offset, simplify, anchors, join, align/distribute."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainterPath, QPainterPathStroker, QTransform

from .geometry import split_cubic
from .model import CAPS, JOINS, GroupItem, Item, Node, PathItem, Subpath


def _doc_path(item: Item) -> QPainterPath:
    return item.doc_path()


def _result(path: QPainterPath, template: Item) -> PathItem:
    """Wrap a document-space path into a new PathItem that copies the template's style."""
    p = PathItem.from_painter_path(path)
    p.style = template.style.copy()
    p.name = template.name
    return p


def pathfinder(items: list[Item], op: str) -> PathItem | None:
    """op: unite | minus_front | intersect | exclude. Items are bottom-to-top; 'front' = last."""
    if len(items) < 2:
        return None
    paths = [_doc_path(i).simplified() for i in items]
    if op == "unite":
        acc = paths[0]
        for p in paths[1:]:
            acc = acc.united(p)
        return _result(acc, items[-1])
    if op == "minus_front":
        acc = paths[0]
        for p in paths[1:]:
            acc = acc.subtracted(p)
        return _result(acc, items[0])
    if op == "intersect":
        acc = paths[0]
        for p in paths[1:]:
            acc = acc.intersected(p)
        return _result(acc, items[-1])
    if op == "exclude":
        acc = paths[0]
        for p in paths[1:]:
            acc = acc.united(p).subtracted(acc.intersected(p))
        return _result(acc, items[-1])
    if op == "minus_back":
        acc = paths[-1]
        for p in paths[:-1]:
            acc = acc.subtracted(p)
        return _result(acc, items[-1])
    return None


def outline_stroke(item: Item) -> PathItem | None:
    if item.style.stroke.is_none() or item.style.stroke_width <= 0:
        return None
    st = QPainterPathStroker()
    st.setWidth(item.style.stroke_width)
    st.setCapStyle(CAPS.get(item.style.cap, Qt.PenCapStyle.FlatCap))
    st.setJoinStyle(JOINS.get(item.style.join, Qt.PenJoinStyle.MiterJoin))
    if item.style.dash:
        st.setDashPattern([d / max(item.style.stroke_width, 0.01) for d in item.style.dash])
    local = st.createStroke(item.local_path()).simplified()
    out = PathItem.from_painter_path(item.transform.map(local))
    out.style = item.style.copy()
    out.style.fill = item.style.stroke.copy()
    out.style.stroke = out.style.stroke.__class__.none()
    out.name = item.name
    return out


def offset_path(item: Item, offset: float, join="Round") -> PathItem:
    path = _doc_path(item).simplified()
    st = QPainterPathStroker()
    st.setWidth(abs(offset) * 2)
    st.setJoinStyle(JOINS.get(join, Qt.PenJoinStyle.RoundJoin))
    ring = st.createStroke(path)
    if offset >= 0:
        res = path.united(ring)
    else:
        res = path.subtracted(ring)
    return _result(res.simplified(), item)


def simplify(item: Item) -> PathItem:
    return _result(_doc_path(item).simplified(), item)


def add_anchor_points(item: PathItem) -> PathItem:
    out = item.copy()
    for sp in out.subpaths:
        nodes = sp.nodes
        new: list[Node] = []
        n = len(nodes)
        segs = n if sp.closed else n - 1
        for i in range(n):
            a = nodes[i]
            new.append(a)
            if i >= segs:
                break
            b = nodes[(i + 1) % n]
            (p0, c1, m1, m), (m2, c2, c3, p3) = split_cubic(a.p, a.h_out, b.h_in, b.p)
            a.h_out = c1
            mid = Node(m, m1, c2, smooth=True)
            if not a.has_out() and not b.has_in():
                mid.h_in = mid.h_out = m; mid.smooth = False
            else:
                b.h_in = c3
            new.append(mid)
        sp.nodes = new
    return out


def reverse_path(item: PathItem) -> PathItem:
    out = item.copy()
    for sp in out.subpaths:
        sp.nodes.reverse()
        for nd in sp.nodes:
            nd.h_in, nd.h_out = nd.h_out, nd.h_in
    return out


def join_paths(a: PathItem, b: PathItem | None) -> PathItem:
    """Join two open paths end-to-end (document space), or close a single open path."""
    if b is None:
        out = a.copy()
        for sp in out.subpaths:
            sp.closed = True
        return out
    pa = PathItem.from_painter_path(a.doc_path()); pb = PathItem.from_painter_path(b.doc_path())
    sa = pa.subpaths[-1]; sb = pb.subpaths[0]
    # orient so the nearest endpoints connect
    d_end_start = (sa.nodes[-1].p - sb.nodes[0].p).manhattanLength()
    d_end_end = (sa.nodes[-1].p - sb.nodes[-1].p).manhattanLength()
    d_start_start = (sa.nodes[0].p - sb.nodes[0].p).manhattanLength()
    d_start_end = (sa.nodes[0].p - sb.nodes[-1].p).manhattanLength()
    best = min(d_end_start, d_end_end, d_start_start, d_start_end)
    if best == d_end_end or best == d_start_start:
        sb = reverse_path(pb).subpaths[0]
    if best == d_start_start or best == d_start_end:
        sa = reverse_path(pa).subpaths[-1]
    out = PathItem([Subpath(sa.nodes + sb.nodes, False)])
    out.style = a.style.copy(); out.name = a.name
    return out


def average_points(nodes: list[Node], axis="both"):
    if not nodes:
        return
    cx = sum(n.p.x() for n in nodes) / len(nodes)
    cy = sum(n.p.y() for n in nodes) / len(nodes)
    for n in nodes:
        dx = (cx - n.p.x()) if axis in ("both", "x") else 0.0
        dy = (cy - n.p.y()) if axis in ("both", "y") else 0.0
        for attr in ("p", "h_in", "h_out"):
            q = getattr(n, attr)
            setattr(n, attr, QPointF(q.x() + dx, q.y() + dy))


# --- align / distribute ---------------------------------------------------------
def align(items: list[Item], mode: str, target: QRectF | None = None):
    """mode: left|hcenter|right|top|vcenter|bottom. target=None aligns to selection bounds."""
    if not items:
        return
    boxes = {i.id: i.bbox() for i in items}
    if target is None:
        if len(items) < 2:
            return
        target = QRectF()
        for b in boxes.values():
            target = target.united(b)
    for it in items:
        b = boxes[it.id]
        dx = dy = 0.0
        if mode == "left": dx = target.left() - b.left()
        elif mode == "hcenter": dx = target.center().x() - b.center().x()
        elif mode == "right": dx = target.right() - b.right()
        elif mode == "top": dy = target.top() - b.top()
        elif mode == "vcenter": dy = target.center().y() - b.center().y()
        elif mode == "bottom": dy = target.bottom() - b.bottom()
        if dx or dy:
            it.transform = it.transform * QTransform.fromTranslate(dx, dy)


def distribute(items: list[Item], mode: str):
    """mode: hspace|vspace (equal gaps) or hcenter|vcenter (equal centre spacing)."""
    if len(items) < 3:
        return
    horiz = mode.startswith("h")
    key = (lambda b: b.left()) if horiz else (lambda b: b.top())
    ordered = sorted(items, key=lambda i: key(i.bbox()))
    boxes = [i.bbox() for i in ordered]
    if mode in ("hspace", "vspace"):
        total = (boxes[-1].right() - boxes[0].left()) if horiz else (boxes[-1].bottom() - boxes[0].top())
        sizes = sum((b.width() if horiz else b.height()) for b in boxes)
        gap = (total - sizes) / (len(boxes) - 1)
        pos = boxes[0].left() if horiz else boxes[0].top()
        for it, b in zip(ordered, boxes):
            d = pos - (b.left() if horiz else b.top())
            it.transform = it.transform * (QTransform.fromTranslate(d, 0) if horiz else QTransform.fromTranslate(0, d))
            pos += (b.width() if horiz else b.height()) + gap
    else:
        c0 = boxes[0].center().x() if horiz else boxes[0].center().y()
        c1 = boxes[-1].center().x() if horiz else boxes[-1].center().y()
        step = (c1 - c0) / (len(boxes) - 1)
        for k, (it, b) in enumerate(zip(ordered, boxes)):
            c = b.center().x() if horiz else b.center().y()
            d = c0 + step * k - c
            it.transform = it.transform * (QTransform.fromTranslate(d, 0) if horiz else QTransform.fromTranslate(0, d))


def transform_about(items: list[Item], t: QTransform, center: QPointF):
    """Apply a document-space transform ``t`` about ``center`` to every item."""
    full = QTransform.fromTranslate(-center.x(), -center.y()) * t * QTransform.fromTranslate(center.x(), center.y())
    for it in items:
        it.transform = it.transform * full


def selection_bounds(items: list[Item]) -> QRectF:
    r = QRectF()
    for it in items:
        r = r.united(it.bbox()) if not r.isNull() else it.bbox()
    return r
