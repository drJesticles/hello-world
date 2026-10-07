"""Interactive tools for VectorBench. Positions passed in are document coordinates."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QTransform

from .geometry import cubic_point, dist, nearest_t_on_cubic, rdp, smooth_handles, split_cubic
from .model import (EllipseItem, GroupItem, ImageItem, Item, Node, Paint, PathItem, PolygonItem, RectItem, Subpath,
                    TextItem)
from .pathops import selection_bounds, transform_about


def _spec(key, label, type_, default, **kw):
    d = {"key": key, "label": label, "type": type_, "default": default}
    d.update(kw)
    return d


def _shift(ev):
    return bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)


def _alt(ev):
    return bool(ev.modifiers() & Qt.KeyboardModifier.AltModifier)


def _ctrl(ev):
    return bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier)


class Tool:
    name = "tool"; label = "Tool"; shortcut = ""; glyph = "?"; tooltip = ""
    cursor = Qt.CursorShape.ArrowCursor

    def __init__(self, ctx):
        self.ctx = ctx

    def option_specs(self):
        return []

    def opt(self, key, default=None):
        return self.ctx.opt(self.name, key, default)

    def activate(self, view): pass
    def deactivate(self, view): pass
    def press(self, view, pos, ev): pass
    def move(self, view, pos, ev): pass
    def release(self, view, pos, ev): pass
    def double_click(self, view, pos, ev): pass

    def key_press(self, view, ev) -> bool:
        return False

    def draw_overlay(self, p: QPainter, view): pass


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------
class HandTool(Tool):
    name, label, shortcut, glyph = "hand", "Hand", "H", "✋"
    cursor = Qt.CursorShape.OpenHandCursor
    tooltip = "Hand (H): drag to pan. Space+drag works with any tool."

    def press(self, view, pos, ev):
        self._s = ev.position(); self._o = QPointF(view.offset); view.setCursor(Qt.CursorShape.ClosedHandCursor)

    def move(self, view, pos, ev):
        if ev.buttons() & Qt.MouseButton.LeftButton:
            d = ev.position() - self._s
            view.offset = QPointF(self._o.x() + d.x(), self._o.y() + d.y())

    def release(self, view, pos, ev):
        view.setCursor(self.cursor)


class ZoomTool(Tool):
    name, label, shortcut, glyph = "zoom", "Zoom", "Z", "🔍"
    cursor = Qt.CursorShape.CrossCursor
    tooltip = "Zoom (Z): click in, Alt+click out, drag a box to zoom to it."

    def press(self, view, pos, ev):
        self._a = pos; self._b = None

    def move(self, view, pos, ev):
        if ev.buttons() & Qt.MouseButton.LeftButton:
            self._b = pos

    def release(self, view, pos, ev):
        if self._b is not None and dist(self._a, self._b) * view.zoom > 8:
            view.zoom_to_rect(QRectF(self._a, self._b).normalized())
        elif _alt(ev):
            view.zoom_out(ev.position())
        else:
            view.zoom_in(ev.position())
        self._b = None

    def draw_overlay(self, p, view):
        if getattr(self, "_b", None) is not None:
            p.setPen(QPen(QColor(255, 255, 255), 1, Qt.PenStyle.DashLine)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(view.view_rect(QRectF(self._a, self._b).normalized()))


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------
class SelectTool(Tool):
    name, label, shortcut, glyph = "select", "Selection", "V", "↖"
    tooltip = "Selection (V): click or drag-box to select; drag to move; handles scale, outside corners rotate. Alt+drag duplicates."

    def __init__(self, ctx):
        super().__init__(ctx)
        self._mode = None
        self._marquee = None

    def _handle_at(self, view, vpos) -> str | None:
        for k, r in view.handle_rects().items():
            if r.adjusted(-2, -2, 2, 2).contains(vpos):
                return k
        return None

    def _rotate_zone(self, view, vpos) -> bool:
        if not view.selection:
            return False
        r = view.view_rect(view.selection_bounds())
        outer = r.adjusted(-18, -18, 18, 18)
        inner = r.adjusted(-4, -4, 4, 4)
        return outer.contains(vpos) and not inner.contains(vpos)

    def press(self, view, pos, ev):
        vpos = ev.position()
        self._start = pos
        self._moved = False
        h = self._handle_at(view, vpos) if view.selection else None
        if h:
            self._mode = "scale"; self._handle = h
            self._bbox0 = view.selection_bounds()
            self._snap = [(it, QTransform(it.transform)) for it in view.selection]
            view.history.begin("Scale")
            return
        if view.selection and self._rotate_zone(view, vpos):
            self._mode = "rotate"
            self._center = view.selection_bounds().center()
            self._angle0 = math.degrees(math.atan2(pos.y() - self._center.y(), pos.x() - self._center.x()))
            self._snap = [(it, QTransform(it.transform)) for it in view.selection]
            view.history.begin("Rotate")
            return
        hit = view.doc.item_at(pos, view.tol())
        if hit is None:
            if not _shift(ev):
                view.clear_selection()
            self._mode = "marquee"; self._marquee = QRectF(pos, pos)
            return
        if _shift(ev):
            view.select_toggle(hit)
            self._mode = None
            return
        if hit not in view.selection:
            view.set_selection([hit])
        self._mode = "move"
        self._dup_done = False
        self._snap = [(it, QTransform(it.transform)) for it in view.selection]
        view.history.begin("Move")

    def move(self, view, pos, ev):
        if not (ev.buttons() & Qt.MouseButton.LeftButton):
            vpos = ev.position()
            if view.selection and self._handle_at(view, vpos):
                h = self._handle_at(view, vpos)
                view.setCursor({"n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor, "e": Qt.CursorShape.SizeHorCursor,
                                "w": Qt.CursorShape.SizeHorCursor, "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
                                "nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor}[h])
            elif view.selection and self._rotate_zone(view, vpos):
                view.setCursor(Qt.CursorShape.CrossCursor)
            else:
                view.setCursor(Qt.CursorShape.ArrowCursor)
            return
        if self._mode is None:
            return
        self._moved = True
        if self._mode == "marquee":
            self._marquee = QRectF(self._start, pos).normalized()
        elif self._mode == "move":
            if _alt(ev) and not self._dup_done:
                self._dup_done = True
                view.history.cancel(); view.history.begin("Duplicate")
                clones = []
                for it, _ in self._snap:
                    c = it.copy()
                    layer = view.doc.layer_of(it)
                    view.doc.add_item(c, layer)
                    clones.append(c)
                view.set_selection(clones)
                self._snap = [(it, QTransform(it.transform)) for it in clones]
            target = view.snap(pos, exclude=view.selection) if view.snap_points else pos
            dx, dy = target.x() - self._start.x(), target.y() - self._start.y()
            if _shift(ev):
                if abs(dx) > abs(dy): dy = 0
                else: dx = 0
            for it, t0 in self._snap:
                it.transform = t0 * QTransform.fromTranslate(dx, dy)
            view.doc.changed()
        elif self._mode == "scale":
            self._apply_scale(view, pos, ev)
        elif self._mode == "rotate":
            a = math.degrees(math.atan2(pos.y() - self._center.y(), pos.x() - self._center.x())) - self._angle0
            if _shift(ev):
                a = round(a / 15.0) * 15.0
            t = QTransform(); t.rotate(a)
            for it, t0 in self._snap:
                it.transform = t0
            transform_about([it for it, _ in self._snap], t, self._center)
            view.doc.changed()
            self.ctx.status(f"Rotate {a:.1f}°")

    def _apply_scale(self, view, pos, ev):
        b = self._bbox0
        h = self._handle
        anchor = {"nw": b.bottomRight(), "n": QPointF(b.center().x(), b.bottom()), "ne": b.bottomLeft(),
                  "e": QPointF(b.left(), b.center().y()), "se": b.topLeft(), "s": QPointF(b.center().x(), b.top()),
                  "sw": b.topRight(), "w": QPointF(b.right(), b.center().y())}[h]
        if _alt(ev):
            anchor = b.center()
        sx = sy = 1.0
        if "e" in h or "w" in h:
            denom = (b.right() - anchor.x()) if "e" in h else (b.left() - anchor.x())
            sx = (pos.x() - anchor.x()) / denom if abs(denom) > 1e-6 else 1.0
        if "n" in h or "s" in h:
            denom = (b.bottom() - anchor.y()) if "s" in h else (b.top() - anchor.y())
            sy = (pos.y() - anchor.y()) / denom if abs(denom) > 1e-6 else 1.0
        if _shift(ev) or len(h) == 1 and False:
            s = max(abs(sx), abs(sy)) if len(h) == 2 else (sx if sx != 1.0 else sy)
            sx, sy = math.copysign(s, sx if sx != 1.0 else 1), math.copysign(s, sy if sy != 1.0 else 1)
        if abs(sx) < 1e-3 or abs(sy) < 1e-3:
            return
        t = QTransform.fromScale(sx, sy)
        for it, t0 in self._snap:
            it.transform = t0
        transform_about([it for it, _ in self._snap], t, anchor)
        view.doc.changed()
        self.ctx.status(f"Scale {sx*100:.0f}% × {sy*100:.0f}%")

    def release(self, view, pos, ev):
        mode, self._mode = self._mode, None
        if mode == "marquee":
            r = self._marquee; self._marquee = None
            if r is not None and r.width() > 1 and r.height() > 1:
                items = view.doc.items_in_rect(r)
                if _shift(ev):
                    for it in items:
                        if it not in view.selection:
                            view.selection.append(it)
                    view.selectionChanged.emit(); view.update()
                else:
                    view.set_selection(items)
        elif mode in ("move", "scale", "rotate"):
            if self._moved:
                view.history.commit()
            else:
                view.history.cancel()
            view.selectionChanged.emit()

    def double_click(self, view, pos, ev):
        hit = view.doc.item_at(pos, view.tol())
        if isinstance(hit, TextItem):
            view.set_selection([hit])
            view.begin_text_edit(hit)
        elif isinstance(hit, GroupItem):
            self.ctx.status("Groups: Object > Ungroup (Ctrl+Shift+G) to edit children.")

    def key_press(self, view, ev):
        step = 10 if _shift(ev) else 1
        d = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0), Qt.Key.Key_Up: (0, -step), Qt.Key.Key_Down: (0, step)}.get(ev.key())
        if d is None or not view.selection:
            return False
        view.history.push("Nudge")
        for it in view.selection:
            it.transform = it.transform * QTransform.fromTranslate(*d)
        view.doc.changed()
        return True

    def draw_overlay(self, p, view):
        if self._marquee is not None:
            p.setPen(QPen(QColor(40, 120, 255), 1, Qt.PenStyle.DashLine)); p.setBrush(QColor(40, 120, 255, 30))
            p.drawRect(view.view_rect(self._marquee))


class DirectSelectTool(Tool):
    name, label, shortcut, glyph = "direct", "Direct selection", "A", "↗"
    tooltip = "Direct Selection (A): click anchors or handles to edit them; drag-box selects anchors; Shift adds."

    def __init__(self, ctx):
        super().__init__(ctx)
        self._mode = None
        self._marquee = None

    def _anchor_at(self, view, pos):
        tol = 6 / view.zoom
        for it in view.selection:
            if not isinstance(it, PathItem):
                continue
            for si, ni, nd in it.all_nodes():
                if dist(it.transform.map(nd.p), pos) <= tol:
                    return it, si, ni
        return None

    def _handle_at(self, view, pos):
        tol = 6 / view.zoom
        for (iid, si, ni) in view.selected_nodes:
            it = view.doc.find(iid)
            if not isinstance(it, PathItem):
                continue
            nd = it.subpaths[si].nodes[ni]
            for which in ("h_out", "h_in"):
                h = getattr(nd, which)
                if h != nd.p and dist(it.transform.map(h), pos) <= tol:
                    return it, si, ni, which
        return None

    def press(self, view, pos, ev):
        self._start = pos; self._moved = False
        h = self._handle_at(view, pos)
        if h:
            self._mode = "handle"; self._h = h
            view.history.begin("Edit Handle")
            return
        a = self._anchor_at(view, pos)
        if a:
            it, si, ni = a
            key = (it.id, si, ni)
            if _shift(ev):
                if key in view.selected_nodes:
                    view.selected_nodes.discard(key)
                else:
                    view.selected_nodes.add(key)
            elif key not in view.selected_nodes:
                view.selected_nodes = {key}
            self._mode = "anchors"
            self._orig = self._capture(view)
            view.history.begin("Move Anchors")
            view.update()
            return
        hit = view.doc.item_at(pos, view.tol())
        if hit is None:
            if not _shift(ev):
                view.set_selection([])
            self._mode = "marquee"; self._marquee = QRectF(pos, pos)
            return
        if hit in view.selection and isinstance(hit, PathItem):
            # drag a segment = move the whole item
            self._mode = "item"
            self._snap = [(hit, QTransform(hit.transform))]
            view.history.begin("Move")
            return
        if _shift(ev):
            view.select_toggle(hit)
        else:
            view.set_selection([hit])
        self._mode = "item"
        self._snap = [(it, QTransform(it.transform)) for it in view.selection]
        view.history.begin("Move")

    def _capture(self, view):
        out = {}
        for (iid, si, ni) in view.selected_nodes:
            it = view.doc.find(iid)
            if isinstance(it, PathItem):
                nd = it.subpaths[si].nodes[ni]
                out[(iid, si, ni)] = (QPointF(nd.p), QPointF(nd.h_in), QPointF(nd.h_out))
        return out

    def move(self, view, pos, ev):
        if not (ev.buttons() & Qt.MouseButton.LeftButton) or self._mode is None:
            return
        self._moved = True
        if self._mode == "marquee":
            self._marquee = QRectF(self._start, pos).normalized()
        elif self._mode == "anchors":
            target = view.snap(pos, exclude=view.selection) if view.snap_points else pos
            dx, dy = target.x() - self._start.x(), target.y() - self._start.y()
            if _shift(ev):
                if abs(dx) > abs(dy): dy = 0
                else: dx = 0
            for key, (p0, i0, o0) in self._orig.items():
                it = view.doc.find(key[0])
                inv, _ = it.transform.inverted()
                d = inv.map(QPointF(dx, dy)) - inv.map(QPointF(0, 0))
                nd = it.subpaths[key[1]].nodes[key[2]]
                nd.p = p0 + d; nd.h_in = i0 + d; nd.h_out = o0 + d
            view.doc.changed()
        elif self._mode == "handle":
            it, si, ni, which = self._h
            nd = it.subpaths[si].nodes[ni]
            inv, _ = it.transform.inverted()
            lp = inv.map(pos)
            setattr(nd, which, lp)
            if nd.smooth and not _alt(ev):
                other = "h_in" if which == "h_out" else "h_out"
                v = QPointF(lp.x() - nd.p.x(), lp.y() - nd.p.y())
                L = math.hypot(v.x(), v.y())
                oh = getattr(nd, other)
                ol = dist(oh, nd.p) if oh != nd.p else L
                if L > 1e-6:
                    setattr(nd, other, QPointF(nd.p.x() - v.x() / L * ol, nd.p.y() - v.y() / L * ol))
            elif _alt(ev):
                nd.smooth = False
            view.doc.changed()
        elif self._mode == "item":
            dx, dy = pos.x() - self._start.x(), pos.y() - self._start.y()
            for it, t0 in self._snap:
                it.transform = t0 * QTransform.fromTranslate(dx, dy)
            view.doc.changed()

    def release(self, view, pos, ev):
        mode, self._mode = self._mode, None
        if mode == "marquee":
            r = self._marquee; self._marquee = None
            if r is not None and r.width() > 1 and r.height() > 1:
                hits = view.doc.items_in_rect(r)
                nodes = set(view.selected_nodes) if _shift(ev) else set()
                sel = list(view.selection) if _shift(ev) else []
                for it in hits:
                    if isinstance(it, PathItem):
                        for si, ni, nd in it.all_nodes():
                            if r.contains(it.transform.map(nd.p)):
                                nodes.add((it.id, si, ni))
                                if it not in sel:
                                    sel.append(it)
                    elif it not in sel:
                        sel.append(it)
                view.set_selection(sel, nodes)
        elif mode in ("anchors", "handle", "item"):
            if self._moved:
                view.history.commit()
            else:
                view.history.cancel()

    def key_press(self, view, ev):
        if ev.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and view.selected_nodes:
            view.history.push("Delete Anchors")
            by_item: dict[str, set] = {}
            for iid, si, ni in view.selected_nodes:
                by_item.setdefault(iid, set()).add((si, ni))
            for iid, keys in by_item.items():
                it = view.doc.find(iid)
                if not isinstance(it, PathItem):
                    continue
                for si, sp in enumerate(it.subpaths):
                    sp.nodes = [nd for ni, nd in enumerate(sp.nodes) if (si, ni) not in keys]
                it.subpaths = [sp for sp in it.subpaths if len(sp.nodes) >= 1]
                if not it.subpaths:
                    view.doc.remove_item(it)
            view.selected_nodes = set()
            view.doc.changed(structure=True)
            return True
        step = 10 if _shift(ev) else 1
        d = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0), Qt.Key.Key_Up: (0, -step), Qt.Key.Key_Down: (0, step)}.get(ev.key())
        if d and view.selected_nodes:
            view.history.push("Nudge Anchors")
            for iid, si, ni in view.selected_nodes:
                it = view.doc.find(iid)
                inv, _ = it.transform.inverted()
                dd = inv.map(QPointF(*d)) - inv.map(QPointF(0, 0))
                nd = it.subpaths[si].nodes[ni]
                nd.p += dd; nd.h_in += dd; nd.h_out += dd
            view.doc.changed()
            return True
        return False

    def draw_overlay(self, p, view):
        if self._marquee is not None:
            p.setPen(QPen(QColor(40, 120, 255), 1, Qt.PenStyle.DashLine)); p.setBrush(QColor(40, 120, 255, 30))
            p.drawRect(view.view_rect(self._marquee))


# ---------------------------------------------------------------------------
# Drawing: pen, pencil, anchors
# ---------------------------------------------------------------------------
def _new_item(view, item: Item, label: str, select=True):
    view.history.push(label)
    item.style = view.ctx.style.copy()
    view.doc.add_item(item)
    view.doc.changed(structure=True)
    if select:
        view.set_selection([item])
    return item


class PenTool(Tool):
    name, label, shortcut, glyph = "pen", "Pen", "P", "✒"
    cursor = Qt.CursorShape.CrossCursor
    tooltip = "Pen (P): click for corners, drag for curves, click the first point to close. Enter ends the path, Esc cancels."

    def __init__(self, ctx):
        super().__init__(ctx)
        self.item: PathItem | None = None
        self._hover = None
        self._dragging = False

    def deactivate(self, view):
        self.finish(view)

    def _sp(self) -> Subpath:
        return self.item.subpaths[-1]

    def press(self, view, pos, ev):
        pos = view.snap(pos, exclude=[self.item] if self.item else None) if view.snap_points else pos
        if self.item is None:
            # continue an open path if clicking its endpoint
            hit = view.doc.item_at(pos, view.tol())
            if isinstance(hit, PathItem) and hit.is_open() and not hit.locked:
                sp = hit.subpaths[-1]
                inv, _ = hit.transform.inverted()
                lp = inv.map(pos)
                if dist(sp.nodes[-1].p, lp) < 8 / view.zoom:
                    view.history.begin("Pen")
                    self.item = hit; view.set_selection([hit]); self._dragging = True; self._cur = sp.nodes[-1]; self._press = lp
                    return
                if dist(sp.nodes[0].p, lp) < 8 / view.zoom:
                    view.history.begin("Pen")
                    sp.nodes.reverse()
                    for nd in sp.nodes:
                        nd.h_in, nd.h_out = nd.h_out, nd.h_in
                    self.item = hit; view.set_selection([hit]); self._dragging = True; self._cur = sp.nodes[-1]; self._press = lp
                    return
            view.history.begin("Pen")
            self.item = PathItem([Subpath([Node(pos)])])
            self.item.style = self.ctx.style.copy()
            view.doc.add_item(self.item)
            view.set_selection([self.item])
            self._cur = self._sp().nodes[0]; self._press = pos; self._dragging = True
            view.doc.changed(structure=True)
            return
        inv, _ = self.item.transform.inverted()
        lp = inv.map(pos)
        sp = self._sp()
        if len(sp.nodes) > 1 and dist(lp, sp.nodes[0].p) < 8 / view.zoom:
            sp.closed = True
            self._cur = sp.nodes[0]; self._press = sp.nodes[0].p; self._dragging = True; self._closing = True
            view.doc.changed()
            return
        self._closing = False
        nd = Node(lp)
        sp.nodes.append(nd)
        self._cur = nd; self._press = lp; self._dragging = True
        view.doc.changed()

    def move(self, view, pos, ev):
        self._hover = pos
        if self.item is None or not self._dragging or not (ev.buttons() & Qt.MouseButton.LeftButton):
            return
        inv, _ = self.item.transform.inverted()
        lp = inv.map(pos)
        if dist(lp, self._press) < 2 / view.zoom:
            return
        nd = self._cur
        nd.smooth = True
        nd.h_out = lp
        if not _alt(ev):
            nd.h_in = QPointF(2 * nd.p.x() - lp.x(), 2 * nd.p.y() - lp.y())
        view.doc.changed()

    def release(self, view, pos, ev):
        self._dragging = False
        if self.item is not None and getattr(self, "_closing", False):
            self._closing = False
            self.finish(view)

    def finish(self, view):
        if self.item is None:
            return
        it = self.item
        self.item = None
        if len(it.subpaths[-1].nodes) < 2:
            view.doc.remove_item(it)
            view.history.cancel()
            view.set_selection([])
        else:
            view.history.commit()
        view.doc.changed(structure=True)

    def key_press(self, view, ev):
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish(view); return True
        if ev.key() == Qt.Key.Key_Escape:
            if self.item is not None:
                self.finish(view)
            else:
                view.set_selection([])
            return True
        return False

    def draw_overlay(self, p, view):
        if self.item is None or self._hover is None or self._dragging:
            return
        sp = self._sp()
        if not sp.nodes or sp.closed:
            return
        last = sp.nodes[-1]
        a = view.doc_to_view(self.item.transform.map(last.p)); h = view.doc_to_view(self.item.transform.map(last.h_out))
        b = view.doc_to_view(self._hover)
        path = QPainterPath(a)
        path.cubicTo(h, b, b)
        p.setPen(QPen(QColor(40, 120, 255), 1)); p.setBrush(Qt.BrushStyle.NoBrush); p.drawPath(path)


class PencilTool(Tool):
    name, label, shortcut, glyph = "pencil", "Pencil", "N", "✎"
    cursor = Qt.CursorShape.CrossCursor
    tooltip = "Pencil (N): draw freehand; the stroke is smoothed into a bezier path. Release near the start to close."

    def __init__(self, ctx):
        super().__init__(ctx)
        self._pts: list[QPointF] = []

    def option_specs(self):
        return [_spec("smooth", "Smoothing", "int", 4, min=0, max=30, suffix="px"), _spec("closed", "Close path", "bool", False)]

    def press(self, view, pos, ev):
        self._pts = [pos]

    def move(self, view, pos, ev):
        if self._pts and ev.buttons() & Qt.MouseButton.LeftButton:
            if dist(self._pts[-1], pos) * view.zoom > 2:
                self._pts.append(pos)

    def release(self, view, pos, ev):
        pts, self._pts = self._pts, []
        if len(pts) < 2:
            return
        eps = max(0.5, float(self.opt("smooth"))) / view.zoom
        simp = rdp(pts, eps)
        closed = bool(self.opt("closed")) or (len(pts) > 4 and dist(pts[0], pts[-1]) * view.zoom < 10)
        if closed and len(simp) > 2 and dist(simp[0], simp[-1]) * view.zoom < 10:
            simp = simp[:-1]
        item = PathItem.from_points(simp, closed=closed, smooth=len(simp) > 2)
        _new_item(view, item, "Pencil")

    def draw_overlay(self, p, view):
        if len(self._pts) > 1:
            path = QPainterPath(view.doc_to_view(self._pts[0]))
            for q in self._pts[1:]:
                path.lineTo(view.doc_to_view(q))
            p.setPen(QPen(QColor(40, 120, 255), 1)); p.setBrush(Qt.BrushStyle.NoBrush); p.drawPath(path)


def _nearest_segment(view, pos):
    """Find (item, si, ni, t, dist) for the path segment nearest pos among selected (or hit) path items."""
    best = None
    cands = [i for i in view.selection if isinstance(i, PathItem)]
    hit = view.doc.item_at(pos, view.tol())
    if isinstance(hit, PathItem) and hit not in cands:
        cands.append(hit)
    for it in cands:
        inv, _ = it.transform.inverted()
        lp = inv.map(pos)
        for si, sp in enumerate(it.subpaths):
            n = len(sp.nodes)
            segs = n if sp.closed else n - 1
            for ni in range(segs):
                a, b = sp.nodes[ni], sp.nodes[(ni + 1) % n]
                t, d = nearest_t_on_cubic(a.p, a.h_out, b.h_in, b.p, lp)
                if best is None or d < best[4]:
                    best = (it, si, ni, t, d)
    if best and best[4] * view.zoom < 8:
        return best
    return None


class AddAnchorTool(Tool):
    name, label, shortcut, glyph = "add_anchor", "Add anchor", "+", "+"
    cursor = Qt.CursorShape.CrossCursor
    tooltip = "Add Anchor Point (+): click on a path segment."

    def press(self, view, pos, ev):
        seg = _nearest_segment(view, pos)
        if seg is None:
            return
        it, si, ni, t, _ = seg
        view.history.push("Add Anchor")
        sp = it.subpaths[si]
        a, b = sp.nodes[ni], sp.nodes[(ni + 1) % len(sp.nodes)]
        curved = a.has_out() or b.has_in()
        (p0, c1, m1, m), (m2, c2, c3, p3) = split_cubic(a.p, a.h_out, b.h_in, b.p, t)
        nd = Node(m, m1 if curved else m, c2 if curved else m, smooth=curved)
        if curved:
            a.h_out = c1; b.h_in = c3
        sp.nodes.insert(ni + 1, nd)
        if it not in view.selection:
            view.set_selection([it])
        view.selected_nodes = {(it.id, si, ni + 1)}
        view.doc.changed()


class DeleteAnchorTool(Tool):
    name, label, shortcut, glyph = "del_anchor", "Delete anchor", "-", "−"
    cursor = Qt.CursorShape.CrossCursor
    tooltip = "Delete Anchor Point (-): click an anchor to remove it."

    def press(self, view, pos, ev):
        hit = view.doc.item_at(pos, view.tol())
        cands = [i for i in view.selection if isinstance(i, PathItem)] + ([hit] if isinstance(hit, PathItem) else [])
        for it in cands:
            for si, ni, nd in it.all_nodes():
                if dist(it.transform.map(nd.p), pos) <= 6 / view.zoom:
                    view.history.push("Delete Anchor")
                    sp = it.subpaths[si]
                    sp.nodes.pop(ni)
                    if len(sp.nodes) < (1 if not sp.closed else 2):
                        it.subpaths.pop(si)
                    if not it.subpaths:
                        view.doc.remove_item(it); view.set_selection([])
                    else:
                        view.set_selection([it])
                    view.doc.changed(structure=True)
                    return


class ConvertAnchorTool(Tool):
    name, label, shortcut, glyph = "convert", "Anchor point", "Shift+C", "⌃"
    cursor = Qt.CursorShape.CrossCursor
    tooltip = "Anchor Point (Shift+C): click a smooth anchor to make it a corner; drag a corner to pull out handles."

    def press(self, view, pos, ev):
        self._target = None
        hit = view.doc.item_at(pos, view.tol())
        cands = [i for i in view.selection if isinstance(i, PathItem)] + ([hit] if isinstance(hit, PathItem) else [])
        for it in cands:
            for si, ni, nd in it.all_nodes():
                if dist(it.transform.map(nd.p), pos) <= 6 / view.zoom:
                    view.history.begin("Convert Anchor")
                    self._target = (it, nd); self._dragged = False
                    view.set_selection([it], {(it.id, si, ni)})
                    return

    def move(self, view, pos, ev):
        if self._target is None or not (ev.buttons() & Qt.MouseButton.LeftButton):
            return
        it, nd = self._target
        inv, _ = it.transform.inverted(); lp = inv.map(pos)
        if dist(lp, nd.p) < 2 / view.zoom:
            return
        self._dragged = True
        nd.smooth = True; nd.h_out = lp; nd.h_in = QPointF(2 * nd.p.x() - lp.x(), 2 * nd.p.y() - lp.y())
        view.doc.changed()

    def release(self, view, pos, ev):
        if self._target is None:
            return
        it, nd = self._target; self._target = None
        if not self._dragged:
            if nd.has_in() or nd.has_out():
                nd.h_in = QPointF(nd.p); nd.h_out = QPointF(nd.p); nd.smooth = False
            else:
                # corner without handles -> smooth with handles along neighbours
                sp = next(sp for sp in it.subpaths if nd in sp.nodes)
                i = sp.nodes.index(nd); n = len(sp.nodes)
                prev = sp.nodes[(i - 1) % n] if (sp.closed or i > 0) else nd
                nxt = sp.nodes[(i + 1) % n] if (sp.closed or i < n - 1) else nd
                v = QPointF(nxt.p.x() - prev.p.x(), nxt.p.y() - prev.p.y())
                nd.h_out = QPointF(nd.p.x() + v.x() * 0.25, nd.p.y() + v.y() * 0.25)
                nd.h_in = QPointF(nd.p.x() - v.x() * 0.25, nd.p.y() - v.y() * 0.25); nd.smooth = True
        view.history.commit()
        view.doc.changed()


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------
class ShapeToolBase(Tool):
    cursor = Qt.CursorShape.CrossCursor

    def __init__(self, ctx):
        super().__init__(ctx)
        self._a = self._b = None

    def press(self, view, pos, ev):
        self._a = view.snap(pos) if view.snap_points else pos
        self._b = self._a

    def _rect(self, ev) -> QRectF:
        a, b = self._a, self._b
        dx, dy = b.x() - a.x(), b.y() - a.y()
        if _shift(ev):
            s = max(abs(dx), abs(dy)); dx, dy = math.copysign(s, dx or 1), math.copysign(s, dy or 1)
        if _alt(ev):
            return QRectF(a.x() - dx, a.y() - dy, 2 * dx, 2 * dy).normalized()
        return QRectF(a.x(), a.y(), dx, dy).normalized()

    def move(self, view, pos, ev):
        if self._a is not None and ev.buttons() & Qt.MouseButton.LeftButton:
            self._b = view.snap(pos) if view.snap_points else pos
            self._ev = ev

    def release(self, view, pos, ev):
        if self._a is None:
            return
        r = self._rect(ev)
        self._a = self._b = None
        if r.width() < 1 and r.height() < 1:
            self.click_create(view, pos)
            return
        _new_item(view, self.make(r), self.label)

    def click_create(self, view, pos):
        from .dialogs import ShapeSizeDialog
        dlg = ShapeSizeDialog(self.label, view.window())
        if dlg.exec():
            w, h = dlg.values()
            _new_item(view, self.make(QRectF(pos.x(), pos.y(), w, h)), self.label)

    def make(self, r: QRectF) -> Item:
        raise NotImplementedError

    def draw_overlay(self, p, view):
        if self._a is None or self._b is None or self._a == self._b:
            return
        r = self._rect(getattr(self, "_ev", None) or _NoMods())
        p.setPen(QPen(QColor(40, 120, 255), 1)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.save(); p.translate(view.offset); p.scale(view.zoom, view.zoom)
        p.setPen(QPen(QColor(40, 120, 255), 0)); p.drawPath(self.make(r).local_path()); p.restore()


class _NoMods:
    def modifiers(self):
        return Qt.KeyboardModifier.NoModifier


class RectTool(ShapeToolBase):
    name, label, shortcut, glyph = "rect", "Rectangle", "M", "▭"
    tooltip = "Rectangle (M): drag; Shift for square, Alt from centre. Click for exact size."

    def option_specs(self):
        return [_spec("radius", "Corner radius", "float", 0.0, min=0.0, max=1000.0, step=1.0)]

    def make(self, r):
        return RectItem(r, float(self.opt("radius") or 0))


class EllipseTool(ShapeToolBase):
    name, label, shortcut, glyph = "ellipse", "Ellipse", "L", "◯"
    tooltip = "Ellipse (L): drag; Shift for circle, Alt from centre."

    def make(self, r):
        return EllipseItem(r)


class PolygonTool(ShapeToolBase):
    name, label, shortcut, glyph = "polygon", "Polygon / Star", "", "⬠"
    tooltip = "Polygon: drag from centre; set sides and star in the options bar."

    def option_specs(self):
        return [_spec("sides", "Sides", "int", 6, min=3, max=64), _spec("star", "Star", "bool", False),
                _spec("inner", "Inner radius", "int", 50, min=5, max=95, suffix="%")]

    def _rect(self, ev):
        a, b = self._a, self._b
        r = dist(a, b)
        return QRectF(a.x() - r, a.y() - r, 2 * r, 2 * r)

    def make(self, r):
        c = r.center()
        return PolygonItem(c, r.width() / 2, int(self.opt("sides")), bool(self.opt("star")), self.opt("inner") / 100.0)


class LineTool(ShapeToolBase):
    name, label, shortcut, glyph = "line", "Line", "\\", "╱"
    tooltip = "Line (\\): drag; Shift snaps to 45°."

    def _rect(self, ev):
        return QRectF(self._a, self._b)

    def release(self, view, pos, ev):
        if self._a is None:
            return
        a, b = self._a, self._b
        self._a = self._b = None
        if dist(a, b) < 1:
            return
        if _shift(ev):
            ang = round(math.atan2(b.y() - a.y(), b.x() - a.x()) / (math.pi / 4)) * (math.pi / 4)
            d = dist(a, b); b = QPointF(a.x() + d * math.cos(ang), a.y() + d * math.sin(ang))
        it = PathItem.from_points([a, b])
        _new_item(view, it, "Line")
        if it.style.stroke.is_none():
            it.style.stroke = Paint.solid(QColor(0, 0, 0))
        it.style.fill = Paint.none()
        view.doc.changed()

    def make(self, r):
        return PathItem.from_points([r.topLeft(), r.bottomRight()])

    def draw_overlay(self, p, view):
        if self._a is None or self._b is None:
            return
        p.setPen(QPen(QColor(40, 120, 255), 1)); p.drawLine(view.doc_to_view(self._a), view.doc_to_view(self._b))


# ---------------------------------------------------------------------------
# Type
# ---------------------------------------------------------------------------
class TypeTool(Tool):
    name, label, shortcut, glyph = "type", "Type", "T", "T"
    cursor = Qt.CursorShape.IBeamCursor
    tooltip = "Type (T): click to start typing; click existing text to edit it. Esc or Ctrl+Enter finishes."

    def option_specs(self):
        return [_spec("font", "Font", "font", "Arial"), _spec("size", "Size", "float", 24.0, min=1.0, max=2000.0, step=1.0),
                _spec("bold", "Bold", "bool", False), _spec("italic", "Italic", "bool", False),
                _spec("align", "Align", "choice", "Left", choices=["Left", "Center", "Right"])]

    def press(self, view, pos, ev):
        hit = view.doc.item_at(pos, view.tol())
        if isinstance(hit, TextItem):
            view.set_selection([hit]); view.begin_text_edit(hit)
            return
        view.history.push("Type")
        t = TextItem("", pos, self.opt("font"), float(self.opt("size")))
        t.bold, t.italic, t.align = bool(self.opt("bold")), bool(self.opt("italic")), self.opt("align")
        t.style = self.ctx.style.copy()
        if t.style.fill.is_none() and t.style.stroke.is_none():
            t.style.fill = Paint.solid(QColor(0, 0, 0))
        view.doc.add_item(t); view.doc.changed(structure=True)
        view.set_selection([t])
        view.begin_text_edit(t)


# ---------------------------------------------------------------------------
# Transform tools
# ---------------------------------------------------------------------------
class RotateTool(Tool):
    name, label, shortcut, glyph = "rotate", "Rotate", "R", "↻"
    cursor = Qt.CursorShape.CrossCursor
    tooltip = "Rotate (R): drag to rotate the selection about its centre (Alt+click sets the pivot). Shift snaps 15°. Double-click for the dialog."

    def __init__(self, ctx):
        super().__init__(ctx)
        self.pivot = None

    def activate(self, view):
        self.pivot = None

    def press(self, view, pos, ev):
        if not view.selection:
            hit = view.doc.item_at(pos, view.tol())
            if hit: view.set_selection([hit])
        if not view.selection:
            return
        if _alt(ev):
            self.pivot = pos; return
        c = self.pivot or view.selection_bounds().center()
        self._c = c
        self._a0 = math.degrees(math.atan2(pos.y() - c.y(), pos.x() - c.x()))
        self._snap = [(it, QTransform(it.transform)) for it in view.selection]
        view.history.begin("Rotate")
        self._active = True

    def move(self, view, pos, ev):
        if not getattr(self, "_active", False) or not (ev.buttons() & Qt.MouseButton.LeftButton):
            return
        a = math.degrees(math.atan2(pos.y() - self._c.y(), pos.x() - self._c.x())) - self._a0
        if _shift(ev):
            a = round(a / 15) * 15
        t = QTransform(); t.rotate(a)
        for it, t0 in self._snap:
            it.transform = t0
        transform_about([it for it, _ in self._snap], t, self._c)
        self.ctx.window.last_transform = (t, self._c)
        view.doc.changed(); self.ctx.status(f"Rotate {a:.1f}°")

    def release(self, view, pos, ev):
        if getattr(self, "_active", False):
            self._active = False; view.history.commit()

    def double_click(self, view, pos, ev):
        if self.ctx.window:
            self.ctx.window.transform_dialog("rotate")

    def draw_overlay(self, p, view):
        c = self.pivot or (view.selection_bounds().center() if view.selection else None)
        if c is None:
            return
        v = view.doc_to_view(c)
        p.setPen(QPen(QColor(40, 120, 255), 1)); p.setBrush(Qt.BrushStyle.NoBrush); p.drawEllipse(v, 5, 5)
        p.drawLine(v + QPointF(-8, 0), v + QPointF(8, 0)); p.drawLine(v + QPointF(0, -8), v + QPointF(0, 8))


class ScaleTool(RotateTool):
    name, label, shortcut, glyph = "scale", "Scale", "S", "⤢"
    tooltip = "Scale (S): drag away from the pivot to scale. Shift keeps proportions. Double-click for the dialog."

    def press(self, view, pos, ev):
        super().press(view, pos, ev)
        if getattr(self, "_active", False):
            self._d0 = max(1e-3, dist(pos, self._c)); self._p0 = pos

    def move(self, view, pos, ev):
        if not getattr(self, "_active", False) or not (ev.buttons() & Qt.MouseButton.LeftButton):
            return
        if _shift(ev):
            s = dist(pos, self._c) / self._d0; sx = sy = s
        else:
            sx = (pos.x() - self._c.x()) / (self._p0.x() - self._c.x()) if abs(self._p0.x() - self._c.x()) > 1 else 1.0
            sy = (pos.y() - self._c.y()) / (self._p0.y() - self._c.y()) if abs(self._p0.y() - self._c.y()) > 1 else 1.0
        if abs(sx) < 1e-3 or abs(sy) < 1e-3:
            return
        t = QTransform.fromScale(sx, sy)
        for it, t0 in self._snap:
            it.transform = t0
        transform_about([it for it, _ in self._snap], t, self._c)
        self.ctx.window.last_transform = (t, self._c)
        view.doc.changed(); self.ctx.status(f"Scale {sx*100:.0f}% × {sy*100:.0f}%")

    def double_click(self, view, pos, ev):
        if self.ctx.window:
            self.ctx.window.transform_dialog("scale")


class GradientTool(Tool):
    name, label, shortcut, glyph = "gradient", "Gradient", "G", "▤"
    cursor = Qt.CursorShape.CrossCursor
    tooltip = "Gradient (G): drag across the selected objects to set the gradient direction. Applies a gradient if the fill is solid."

    def press(self, view, pos, ev):
        self._a = pos; self._b = None

    def move(self, view, pos, ev):
        if ev.buttons() & Qt.MouseButton.LeftButton:
            self._b = pos

    def release(self, view, pos, ev):
        if self._b is None or not view.selection:
            self._b = None; return
        view.history.push("Gradient")
        for it in view.selection:
            if isinstance(it, (ImageItem, GroupItem)):
                continue
            inv, _ = it.transform.inverted()
            bb = it.local_bbox()
            a, b = inv.map(self._a), inv.map(self._b)
            if bb.width() <= 0 or bb.height() <= 0:
                continue
            paint = it.style.fill
            if paint.kind not in ("linear", "radial"):
                base = paint.color if paint.kind == "solid" else QColor(0, 0, 0)
                paint = Paint("linear", stops=[(0.0, QColor(base)), (1.0, QColor(255, 255, 255))])
            paint.start = ((a.x() - bb.left()) / bb.width(), (a.y() - bb.top()) / bb.height())
            paint.end = ((b.x() - bb.left()) / bb.width(), (b.y() - bb.top()) / bb.height())
            it.style.fill = paint
        self.ctx.style.fill = view.selection[0].style.fill.copy()
        self.ctx.styleChanged.emit()
        view.doc.changed(); view.selectionChanged.emit()
        self._b = None

    def draw_overlay(self, p, view):
        if getattr(self, "_b", None) is None:
            return
        p.setPen(QPen(QColor(40, 120, 255), 1)); p.drawLine(view.doc_to_view(self._a), view.doc_to_view(self._b))


class EyedropperTool(Tool):
    name, label, shortcut, glyph = "eyedropper", "Eyedropper", "I", "💧"
    cursor = Qt.CursorShape.CrossCursor
    tooltip = "Eyedropper (I): click an object to copy its appearance to the selection (and to new objects)."

    def press(self, view, pos, ev):
        hit = view.doc.item_at(pos, view.tol(), ignore_locked=False)
        if hit is None or isinstance(hit, (GroupItem, ImageItem)):
            return
        self.ctx.style = hit.style.copy()
        self.ctx.styleChanged.emit()
        targets = [i for i in view.selection if i is not hit and not isinstance(i, (GroupItem, ImageItem))]
        if targets:
            view.history.push("Eyedropper")
            for it in targets:
                it.style = hit.style.copy()
            view.doc.changed(); view.selectionChanged.emit()


ALL_TOOLS = [SelectTool, DirectSelectTool, PenTool, AddAnchorTool, DeleteAnchorTool, ConvertAnchorTool, PencilTool,
             LineTool, RectTool, EllipseTool, PolygonTool, TypeTool, RotateTool, ScaleTool, GradientTool,
             EyedropperTool, HandTool, ZoomTool]
