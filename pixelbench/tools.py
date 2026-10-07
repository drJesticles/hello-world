"""Interactive tools. Each tool receives document-space positions (QPointF) from the CanvasView.

Painting model: a stroke accumulates *coverage* into a scratch buffer (alpha only), and the
visible layer is recomputed as ``alpha_over(original, colour * coverage * opacity * selection)``
inside the dirty rectangle. That gives Photoshop-style stroke opacity (overlapping dabs don't
build up past the opacity setting) and makes every tool selection-aware for free.
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath,
                           QPen, QRadialGradient)

from .blend import alpha_over
from .document import shift_pixels
from .qtutil import array_from_qimage, color_to_rgba, qimage_from_array
from .selection import color_region, combine, ellipse_mask, feather, polygon_mask, rect_mask

MODES = ["New", "Add", "Subtract", "Intersect"]


def _sel_mode(ev, default: str) -> str:
    m = ev.modifiers()
    shift = bool(m & Qt.KeyboardModifier.ShiftModifier)
    alt = bool(m & Qt.KeyboardModifier.AltModifier)
    if shift and alt:
        return "intersect"
    if shift:
        return "add"
    if alt:
        return "subtract"
    return default.lower()


def new_paint_canvas(doc):
    """Transparent full-canvas array plus a QPainter drawing into it (antialiased)."""
    arr = np.zeros((doc.height, doc.width, 4), dtype=np.uint8)
    img = qimage_from_array(arr)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    return arr, img, p


def stamp_paint(view, paint: np.ndarray, label: str, opacity: float = 1.0, layer=None):
    """Composite a full-canvas RGBA paint array onto the active layer, honouring the selection."""
    doc = view.doc
    layer = layer or doc.active_layer
    if layer is None or layer.locked:
        view.ctx.status("Layer is locked.")
        return
    view.history.push(label, detach=None)
    mask = doc.selection
    if opacity < 1.0:
        paint = paint.copy()
        paint[..., 3] = (paint[..., 3].astype(np.float32) * opacity + 0.5).astype(np.uint8)
    layer.pixels = alpha_over(layer.pixels, paint, mask)
    doc.changed()


def commit_selection(view, mask: np.ndarray | None, mode: str, feather_px: float = 0.0):
    doc = view.doc
    view.history.push("Select", detach=None)
    if mask is not None and feather_px > 0:
        mask = feather(mask, feather_px)
    if mask is None:
        doc.selection = None
    else:
        doc.selection = combine(doc.selection, mask, mode)
        if doc.selection is not None and not doc.selection.any():
            doc.selection = None if mode == "new" else doc.selection
    doc.changed()


class Tool:
    name = "Tool"
    label = "Tool"
    shortcut = ""
    glyph = "?"
    cursor = Qt.CursorShape.CrossCursor
    tooltip = ""

    def __init__(self, ctx):
        self.ctx = ctx

    def option_specs(self) -> list[dict]:
        return []

    def opt(self, key, default=None):
        return self.ctx.opt(self.name, key, default)

    def activate(self, view):
        pass

    def deactivate(self, view):
        pass

    def press(self, view, pos: QPointF, ev):
        pass

    def move(self, view, pos: QPointF, ev):
        pass

    def release(self, view, pos: QPointF, ev):
        pass

    def double_click(self, view, pos: QPointF, ev):
        pass

    def key_press(self, view, ev) -> bool:
        return False

    def draw_overlay(self, painter: QPainter, view):
        pass


def _spec(key, label, type_, default, **kw):
    d = {"key": key, "label": label, "type": type_, "default": default}
    d.update(kw)
    return d


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------
class HandTool(Tool):
    name, label, shortcut, glyph = "hand", "Hand", "H", "✋"
    cursor = Qt.CursorShape.OpenHandCursor
    tooltip = "Hand (H): drag to pan. Hold Space with any tool."

    def press(self, view, pos, ev):
        self._start = ev.position()
        self._off0 = QPointF(view.offset)
        view.setCursor(Qt.CursorShape.ClosedHandCursor)

    def move(self, view, pos, ev):
        if ev.buttons() & Qt.MouseButton.LeftButton:
            d = ev.position() - self._start
            view.offset = QPointF(self._off0.x() + d.x(), self._off0.y() + d.y())
            view._clamp_offset()

    def release(self, view, pos, ev):
        view.setCursor(self.cursor)


class ZoomTool(Tool):
    name, label, shortcut, glyph = "zoom", "Zoom", "Z", "🔍"
    tooltip = "Zoom (Z): click to zoom in, Alt+click to zoom out, drag a box to zoom to it."

    def press(self, view, pos, ev):
        self._start = ev.position()
        self._end = None

    def move(self, view, pos, ev):
        if ev.buttons() & Qt.MouseButton.LeftButton:
            self._end = ev.position()

    def release(self, view, pos, ev):
        if self._end is not None and (self._end - self._start).manhattanLength() > 8:
            r = QRectF(self._start, self._end).normalized()
            d0, d1 = view.view_to_doc(r.topLeft()), view.view_to_doc(r.bottomRight())
            w, h = max(1.0, d1.x() - d0.x()), max(1.0, d1.y() - d0.y())
            z = min(view.width() / w, view.height() / h)
            view.zoom = max(0.02, min(64.0, z))
            cx, cy = (d0.x() + d1.x()) / 2, (d0.y() + d1.y()) / 2
            view.offset = QPointF(view.width() / 2 - cx * view.zoom, view.height() / 2 - cy * view.zoom)
            view.zoomChanged.emit(view.zoom)
        elif ev.modifiers() & Qt.KeyboardModifier.AltModifier:
            view.zoom_out(ev.position())
        else:
            view.zoom_in(ev.position())
        self._end = None

    def draw_overlay(self, p, view):
        if getattr(self, "_end", None) is not None:
            p.setPen(QPen(QColor(255, 255, 255), 1, Qt.PenStyle.DashLine))
            p.drawRect(QRectF(self._start, self._end).normalized())


class EyedropperTool(Tool):
    name, label, shortcut, glyph = "eyedropper", "Eyedropper", "I", "💧"
    tooltip = "Eyedropper (I): click to set foreground colour, Alt+click for background."

    def option_specs(self):
        return [_spec("sample", "Sample", "choice", "Composite", choices=["Composite", "Current layer"])]

    def _pick(self, view, pos, ev):
        doc = view.doc
        x, y = int(pos.x()), int(pos.y())
        if not (0 <= x < doc.width and 0 <= y < doc.height):
            return
        src = doc.composite() if self.opt("sample") == "Composite" else doc.active_layer.pixels
        r, g, b, a = [int(v) for v in src[y, x]]
        c = QColor(r, g, b)
        if ev.modifiers() & Qt.KeyboardModifier.AltModifier:
            self.ctx.set_bg(c)
        else:
            self.ctx.set_fg(c)
        self.ctx.status(f"Picked {c.name().upper()}  (alpha {a})")

    def press(self, view, pos, ev):
        self._pick(view, pos, ev)

    def move(self, view, pos, ev):
        if ev.buttons() & Qt.MouseButton.LeftButton:
            self._pick(view, pos, ev)


# ---------------------------------------------------------------------------
# Selection tools
# ---------------------------------------------------------------------------
class MarqueeTool(Tool):
    name, label, shortcut, glyph = "marquee", "Marquee select", "M", "▭"
    tooltip = "Marquee (M): drag to select. Shift adds, Alt subtracts, Shift+Alt intersects."

    def __init__(self, ctx):
        super().__init__(ctx)
        self._start = None
        self._end = None

    def option_specs(self):
        return [
            _spec("shape", "Shape", "choice", "Rectangle", choices=["Rectangle", "Ellipse"]),
            _spec("mode", "Mode", "choice", "New", choices=MODES),
            _spec("feather", "Feather", "float", 0.0, min=0.0, max=100.0, step=0.5),
        ]

    def press(self, view, pos, ev):
        self._start = pos
        self._end = pos
        self._mode = _sel_mode(ev, self.opt("mode"))

    def move(self, view, pos, ev):
        if self._start is not None and ev.buttons() & Qt.MouseButton.LeftButton:
            if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:  # square / circle
                dx, dy = pos.x() - self._start.x(), pos.y() - self._start.y()
                s = max(abs(dx), abs(dy))
                pos = QPointF(self._start.x() + math.copysign(s, dx), self._start.y() + math.copysign(s, dy))
            self._end = pos

    def release(self, view, pos, ev):
        if self._start is None:
            return
        doc = view.doc
        x0, y0, x1, y1 = self._start.x(), self._start.y(), self._end.x(), self._end.y()
        self._start = self._end = None
        if abs(x1 - x0) < 1 and abs(y1 - y0) < 1:
            if self._mode == "new" and doc.selection is not None:
                commit_selection(view, None, "new")
            return
        if self.opt("shape") == "Ellipse":
            m = ellipse_mask(doc.width, doc.height, x0, y0, x1, y1)
        else:
            m = rect_mask(doc.width, doc.height, x0, y0, x1, y1)
        commit_selection(view, m, self._mode, float(self.opt("feather") or 0))

    def draw_overlay(self, p, view):
        if self._start is None or self._end is None:
            return
        r = QRectF(view.doc_to_view(self._start), view.doc_to_view(self._end)).normalized()
        p.setPen(QPen(QColor(255, 255, 255), 1, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self.opt("shape") == "Ellipse":
            p.drawEllipse(r)
        else:
            p.drawRect(r)
        p.setPen(QPen(QColor(0, 0, 0), 1, Qt.PenStyle.DotLine))
        if self.opt("shape") == "Ellipse":
            p.drawEllipse(r)
        else:
            p.drawRect(r)


class LassoTool(Tool):
    name, label, shortcut, glyph = "lasso", "Lasso", "L", "ʃ"
    tooltip = "Lasso (L): drag freehand. Polygonal mode: click points, double-click or Enter to close."

    def __init__(self, ctx):
        super().__init__(ctx)
        self._pts: list[QPointF] = []
        self._dragging = False

    def option_specs(self):
        return [
            _spec("kind", "Kind", "choice", "Freehand", choices=["Freehand", "Polygonal"]),
            _spec("mode", "Mode", "choice", "New", choices=MODES),
            _spec("feather", "Feather", "float", 0.0, min=0.0, max=100.0, step=0.5),
        ]

    def deactivate(self, view):
        self._pts = []

    def press(self, view, pos, ev):
        if not self._pts:
            self._mode = _sel_mode(ev, self.opt("mode"))
        if self.opt("kind") == "Polygonal":
            if self._pts and (pos - self._pts[0]).manhattanLength() * view.zoom < 8 and len(self._pts) > 2:
                self._finish(view)
            else:
                self._pts.append(pos)
        else:
            self._pts = [pos]
            self._dragging = True

    def move(self, view, pos, ev):
        if self.opt("kind") == "Polygonal":
            self._hover = pos
        elif self._dragging:
            self._pts.append(pos)

    def release(self, view, pos, ev):
        if self.opt("kind") != "Polygonal" and self._dragging:
            self._dragging = False
            self._finish(view)

    def double_click(self, view, pos, ev):
        if self.opt("kind") == "Polygonal" and len(self._pts) > 2:
            self._finish(view)

    def key_press(self, view, ev):
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and len(self._pts) > 2:
            self._finish(view)
            return True
        if ev.key() == Qt.Key.Key_Escape:
            self._pts = []
            return True
        return False

    def _finish(self, view):
        doc = view.doc
        pts = [(p.x(), p.y()) for p in self._pts]
        self._pts = []
        if len(pts) < 3:
            if self._mode == "new" and doc.selection is not None:
                commit_selection(view, None, "new")
            return
        m = polygon_mask(doc.width, doc.height, pts)
        commit_selection(view, m, self._mode, float(self.opt("feather") or 0))

    def draw_overlay(self, p, view):
        if len(self._pts) < 1:
            return
        path = QPainterPath(view.doc_to_view(self._pts[0]))
        for q in self._pts[1:]:
            path.lineTo(view.doc_to_view(q))
        if self.opt("kind") == "Polygonal" and getattr(self, "_hover", None) is not None:
            path.lineTo(view.doc_to_view(self._hover))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255), 1))
        p.drawPath(path)
        p.setPen(QPen(QColor(0, 0, 0), 1, Qt.PenStyle.DotLine))
        p.drawPath(path)


class MagicWandTool(Tool):
    name, label, shortcut, glyph = "wand", "Magic wand", "W", "✨"
    tooltip = "Magic Wand (W): click to select similar colours. Shift adds, Alt subtracts."

    def option_specs(self):
        return [
            _spec("tolerance", "Tolerance", "int", 32, min=0, max=255),
            _spec("contiguous", "Contiguous", "bool", True),
            _spec("sample_all", "Sample all layers", "bool", True),
            _spec("mode", "Mode", "choice", "New", choices=MODES),
        ]

    def press(self, view, pos, ev):
        doc = view.doc
        x, y = int(pos.x()), int(pos.y())
        if not (0 <= x < doc.width and 0 <= y < doc.height):
            return
        src = doc.composite() if self.opt("sample_all") else doc.active_layer.pixels
        m = color_region(src, x, y, int(self.opt("tolerance")), bool(self.opt("contiguous")))
        commit_selection(view, m, _sel_mode(ev, self.opt("mode")))


# ---------------------------------------------------------------------------
# Move / crop
# ---------------------------------------------------------------------------
class MoveTool(Tool):
    name, label, shortcut, glyph = "move", "Move", "V", "✥"
    cursor = Qt.CursorShape.SizeAllCursor
    tooltip = "Move (V): drag the layer (or the selected pixels). Arrow keys nudge, Shift+arrows nudge 10px."

    def __init__(self, ctx):
        super().__init__(ctx)
        self._active = False

    def option_specs(self):
        return [_spec("auto_select", "Auto-select layer", "bool", False)]

    def _begin(self, view):
        doc = view.doc
        layer = doc.active_layer
        if layer is None or layer.locked:
            return False
        view.history.push("Move", detach=None)
        self._orig = layer.pixels
        self._sel = doc.selection
        if self._sel is not None:
            m = self._sel.astype(np.float32)[..., None] / 255.0
            self._float = (self._orig.astype(np.float32) * m + 0.5).astype(np.uint8)
            self._float[..., :3] = self._orig[..., :3]
            self._base = self._orig.copy()
            self._base[..., 3] = (self._orig[..., 3].astype(np.float32) * (1 - m[..., 0]) + 0.5).astype(np.uint8)
        else:
            self._float = self._orig
            self._base = None
        return True

    def _apply(self, view, dx, dy):
        doc = view.doc
        layer = doc.active_layer
        moved = shift_pixels(self._float, dx, dy)
        if self._base is not None:
            layer.pixels = alpha_over(self._base, moved)
            doc.selection = shift_pixels(self._sel[..., None], dx, dy)[..., 0]
        else:
            layer.pixels = moved
        doc.changed()

    def press(self, view, pos, ev):
        doc = view.doc
        if self.opt("auto_select"):
            x, y = int(pos.x()), int(pos.y())
            if 0 <= x < doc.width and 0 <= y < doc.height:
                for i in range(len(doc.layers) - 1, -1, -1):
                    l = doc.layers[i]
                    if l.visible and l.pixels[y, x, 3] > 0:
                        doc.active_index = i
                        doc.changed(structure=True)
                        break
        self._start = pos
        self._active = self._begin(view)

    def move(self, view, pos, ev):
        if self._active and ev.buttons() & Qt.MouseButton.LeftButton:
            dx = int(round(pos.x() - self._start.x()))
            dy = int(round(pos.y() - self._start.y()))
            if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                if abs(dx) > abs(dy):
                    dy = 0
                else:
                    dx = 0
            self._apply(view, dx, dy)

    def release(self, view, pos, ev):
        if not self._active:
            return
        self._active = False
        dx = int(round(pos.x() - self._start.x()))
        dy = int(round(pos.y() - self._start.y()))
        if dx == 0 and dy == 0:
            view.history.pop_last()
            view.doc.active_layer.pixels = self._orig
            view.doc.selection = self._sel
            view.doc.changed()

    def key_press(self, view, ev):
        step = 10 if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        d = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
             Qt.Key.Key_Up: (0, -step), Qt.Key.Key_Down: (0, step)}.get(ev.key())
        if d is None:
            return False
        if self._begin(view):
            self._apply(view, *d)
        return True


class CropTool(Tool):
    name, label, shortcut, glyph = "crop", "Crop", "C", "⌗"
    tooltip = "Crop (C): drag a rectangle, then press Enter or double-click to crop. Esc cancels."

    def __init__(self, ctx):
        super().__init__(ctx)
        self._rect = None
        self._drag = None

    def deactivate(self, view):
        self._rect = None

    def press(self, view, pos, ev):
        self._drag = pos
        self._rect = QRectF(pos, pos)

    def move(self, view, pos, ev):
        if self._drag is not None and ev.buttons() & Qt.MouseButton.LeftButton:
            self._rect = QRectF(self._drag, pos).normalized()

    def release(self, view, pos, ev):
        self._drag = None
        if self._rect is not None and (self._rect.width() < 1 or self._rect.height() < 1):
            self._rect = None

    def double_click(self, view, pos, ev):
        self.apply(view)

    def key_press(self, view, ev):
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.apply(view)
            return True
        if ev.key() == Qt.Key.Key_Escape:
            self._rect = None
            return True
        return False

    def apply(self, view):
        if self._rect is None:
            return
        r = self._rect
        doc = view.doc
        view.history.push("Crop", detach=None)
        doc.crop(int(round(r.left())), int(round(r.top())), int(round(r.right())), int(round(r.bottom())))
        self._rect = None
        doc.changed(structure=True)
        view.fit_in_view()

    def draw_overlay(self, p, view):
        if self._rect is None:
            return
        full = view.doc_rect_in_view()
        r = QRectF(view.doc_to_view(self._rect.topLeft()), view.doc_to_view(self._rect.bottomRight()))
        path = QPainterPath()
        path.addRect(full)
        inner = QPainterPath()
        inner.addRect(r)
        p.fillPath(path.subtracted(inner), QColor(0, 0, 0, 140))
        p.setPen(QPen(QColor(255, 255, 255), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)
        # rule-of-thirds guides
        p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        for i in (1, 2):
            x = r.left() + r.width() * i / 3
            y = r.top() + r.height() * i / 3
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        txt = f"{int(self._rect.width())} × {int(self._rect.height())}"
        p.setPen(QColor(255, 255, 255))
        p.drawText(r.topLeft() + QPointF(4, -4), txt)


# ---------------------------------------------------------------------------
# Painting
# ---------------------------------------------------------------------------
def make_dab(size: float, hardness: float) -> QImage:
    """White, premultiplied-alpha round dab with soft edge controlled by hardness."""
    d = max(1, int(math.ceil(size)))
    if d % 2 == 0:
        d += 1
    r = d / 2.0
    y, x = np.mgrid[0:d, 0:d].astype(np.float32)
    dist = np.sqrt((x + 0.5 - r) ** 2 + (y + 0.5 - r) ** 2) / max(r, 0.5)
    h = float(max(0.0, min(1.0, hardness)))
    if h >= 0.999:
        a = np.clip((1.0 - dist) * max(r, 1.0), 0, 1)  # 1px antialiased edge
    else:
        t = np.clip((dist - h) / max(1e-3, 1.0 - h), 0, 1)
        a = 1.0 - (t * t * (3 - 2 * t))
        a = np.where(dist >= 1.0, 0.0, a)
    arr = np.empty((d, d, 4), dtype=np.uint8)
    v = np.clip(a * 255 + 0.5, 0, 255).astype(np.uint8)
    arr[..., 0] = v
    arr[..., 1] = v
    arr[..., 2] = v
    arr[..., 3] = v
    img = QImage(arr.data, d, d, d * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()
    return img


class StrokeEngine:
    """Shared by Brush, Eraser and Clone Stamp."""

    def __init__(self, view, size, hardness, opacity, spacing=0.12):
        self.view = view
        self.doc = view.doc
        self.layer = self.doc.active_layer
        self.size = max(1.0, float(size))
        self.opacity = float(opacity)
        self.dab = make_dab(self.size, hardness)
        self.spacing = max(0.5, self.size * spacing)
        self.buf = np.zeros((self.doc.height, self.doc.width, 4), dtype=np.uint8)
        h, w = self.buf.shape[:2]
        self.img = QImage(self.buf.data, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied)
        self.painter = QPainter(self.img)
        self.painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.last = None
        self.leftover = 0.0
        self.mask = self.doc.selection

    def begin(self, pos, label):
        self.view.history.push(label, detach="active")
        self.orig = self.view.history.undo_stack[-1][1].layers[self.doc.active_index].pixels
        self.layer = self.doc.active_layer
        self.last = pos
        self._stamp(pos)
        self._flush(pos, pos)

    def _stamp(self, p):
        half = self.dab.width() / 2.0
        self.painter.drawImage(QPointF(p.x() - half, p.y() - half), self.dab)

    def segment(self, pos):
        if self.last is None:
            self.begin(pos, "Stroke")
            return
        a, b = self.last, pos
        dx, dy = b.x() - a.x(), b.y() - a.y()
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            return
        t = self.leftover
        while t <= dist:
            self._stamp(QPointF(a.x() + dx * t / dist, a.y() + dy * t / dist))
            t += self.spacing
        self.leftover = t - dist
        self._flush(a, b)
        self.last = pos

    def _flush(self, a, b):
        half = self.dab.width() / 2.0 + 2
        x0 = int(max(0, math.floor(min(a.x(), b.x()) - half)))
        y0 = int(max(0, math.floor(min(a.y(), b.y()) - half)))
        x1 = int(min(self.doc.width, math.ceil(max(a.x(), b.x()) + half)))
        y1 = int(min(self.doc.height, math.ceil(max(a.y(), b.y()) + half)))
        if x1 <= x0 or y1 <= y0:
            return
        cov = self.buf[y0:y1, x0:x1, 3].astype(np.float32) / 255.0 * self.opacity
        if self.mask is not None:
            cov = cov * (self.mask[y0:y1, x0:x1].astype(np.float32) / 255.0)
        self.apply_region(x0, y0, x1, y1, cov)
        self.doc.changed()

    def apply_region(self, x0, y0, x1, y1, cov):
        raise NotImplementedError

    def end(self):
        self.painter.end()


class PaintStroke(StrokeEngine):
    def __init__(self, view, size, hardness, opacity, color):
        super().__init__(view, size, hardness, opacity)
        self.color = np.array(color[:3], dtype=np.float32)

    def apply_region(self, x0, y0, x1, y1, cov):
        o = self.orig[y0:y1, x0:x1].astype(np.float32) / 255.0
        sa = cov[..., None]
        da = o[..., 3:4]
        oa = sa + da * (1 - sa)
        with np.errstate(divide="ignore", invalid="ignore"):
            rgb = np.where(oa > 0, (self.color / 255.0 * sa + o[..., :3] * da * (1 - sa)) / np.maximum(oa, 1e-6), 0)
        out = self.layer.pixels[y0:y1, x0:x1]
        out[..., :3] = np.clip(rgb * 255 + 0.5, 0, 255).astype(np.uint8)
        out[..., 3] = np.clip(oa[..., 0] * 255 + 0.5, 0, 255).astype(np.uint8)


class EraseStroke(StrokeEngine):
    def apply_region(self, x0, y0, x1, y1, cov):
        o = self.orig[y0:y1, x0:x1]
        out = self.layer.pixels[y0:y1, x0:x1]
        out[..., :3] = o[..., :3]
        out[..., 3] = np.clip(o[..., 3].astype(np.float32) * (1 - cov) + 0.5, 0, 255).astype(np.uint8)


class CloneStroke(StrokeEngine):
    def __init__(self, view, size, hardness, opacity, source: np.ndarray, dx: int, dy: int):
        super().__init__(view, size, hardness, opacity)
        self.src = shift_pixels(source, dx, dy)

    def apply_region(self, x0, y0, x1, y1, cov):
        o = self.orig[y0:y1, x0:x1]
        s = self.src[y0:y1, x0:x1].copy()
        s[..., 3] = (s[..., 3].astype(np.float32) * cov + 0.5).astype(np.uint8)
        self.layer.pixels[y0:y1, x0:x1] = alpha_over(o, s)


class BrushTool(Tool):
    name, label, shortcut, glyph = "brush", "Brush", "B", "🖌"
    cursor = Qt.CursorShape.BlankCursor
    tooltip = "Brush (B): paint with the foreground colour. [ and ] change size. Shift+click draws a straight line."

    def __init__(self, ctx):
        super().__init__(ctx)
        self._stroke = None
        self._hover = None
        self._last_end = None

    def option_specs(self):
        return [
            _spec("size", "Size", "int", 20, min=1, max=1000),
            _spec("hardness", "Hardness", "int", 80, min=0, max=100, suffix="%"),
            _spec("opacity", "Opacity", "int", 100, min=1, max=100, suffix="%"),
        ]

    def _make(self, view):
        return PaintStroke(view, self.opt("size"), self.opt("hardness") / 100.0,
                           self.opt("opacity") / 100.0, color_to_rgba(self.ctx.fg))

    def press(self, view, pos, ev):
        if view.doc.active_layer is None or view.doc.active_layer.locked:
            self.ctx.status("Layer is locked.")
            return
        self._stroke = self._make(view)
        self._stroke.begin(pos, self.label)
        if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier and self._last_end is not None:
            self._stroke.last = self._last_end
            self._stroke.segment(pos)
        self._hover = pos

    def move(self, view, pos, ev):
        self._hover = pos
        if self._stroke is not None and ev.buttons() & Qt.MouseButton.LeftButton:
            self._stroke.segment(pos)

    def release(self, view, pos, ev):
        if self._stroke is not None:
            self._stroke.segment(pos)
            self._stroke.end()
            self._last_end = pos
            self._stroke = None

    def key_press(self, view, ev):
        if ev.key() == Qt.Key.Key_BracketLeft:
            self.ctx.set_opt(self.name, "size", max(1, int(self.opt("size") * 0.8)))
            return True
        if ev.key() == Qt.Key.Key_BracketRight:
            self.ctx.set_opt(self.name, "size", min(1000, max(self.opt("size") + 1, int(self.opt("size") * 1.25))))
            return True
        return False

    def draw_overlay(self, p, view):
        if self._hover is None or not view.underMouse():
            return
        r = self.opt("size") * view.zoom / 2.0
        c = view.doc_to_view(self._hover)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0), 1))
        p.drawEllipse(c, r, r)
        p.setPen(QPen(QColor(255, 255, 255), 1, Qt.PenStyle.DotLine))
        p.drawEllipse(c, r, r)
        if r < 3:
            p.setPen(QPen(QColor(255, 255, 255), 1))
            p.drawLine(c + QPointF(-6, 0), c + QPointF(6, 0))
            p.drawLine(c + QPointF(0, -6), c + QPointF(0, 6))


class EraserTool(BrushTool):
    name, label, shortcut, glyph = "eraser", "Eraser", "E", "◻"
    tooltip = "Eraser (E): erase to transparency. [ and ] change size."

    def _make(self, view):
        return EraseStroke(view, self.opt("size"), self.opt("hardness") / 100.0, self.opt("opacity") / 100.0)


class CloneStampTool(BrushTool):
    name, label, shortcut, glyph = "clone", "Clone stamp", "S", "⎘"
    tooltip = "Clone Stamp (S): Alt+click to set the source, then paint to copy pixels from there."

    def __init__(self, ctx):
        super().__init__(ctx)
        self._source = None
        self._offset = None

    def option_specs(self):
        return super().option_specs() + [_spec("sample_all", "Sample all layers", "bool", True)]

    def press(self, view, pos, ev):
        if ev.modifiers() & Qt.KeyboardModifier.AltModifier:
            self._source = pos
            self._offset = None
            self.ctx.status(f"Clone source set at {int(pos.x())}, {int(pos.y())}")
            return
        if self._source is None:
            self.ctx.status("Alt+click to set a clone source first.")
            return
        if self._offset is None:
            self._offset = (int(round(pos.x() - self._source.x())), int(round(pos.y() - self._source.y())))
        super().press(view, pos, ev)

    def _make(self, view):
        doc = view.doc
        src = doc.composite() if self.opt("sample_all") else doc.active_layer.pixels
        dx, dy = self._offset
        return CloneStroke(view, self.opt("size"), self.opt("hardness") / 100.0, self.opt("opacity") / 100.0,
                           src.copy(), dx, dy)

    def draw_overlay(self, p, view):
        super().draw_overlay(p, view)
        if self._source is not None:
            s = view.doc_to_view(self._source) if self._offset is None or self._hover is None else \
                view.doc_to_view(QPointF(self._hover.x() - self._offset[0], self._hover.y() - self._offset[1]))
            p.setPen(QPen(QColor(255, 80, 80), 1))
            p.drawLine(s + QPointF(-8, 0), s + QPointF(8, 0))
            p.drawLine(s + QPointF(0, -8), s + QPointF(0, 8))


# ---------------------------------------------------------------------------
# Fill / gradient / shapes / text
# ---------------------------------------------------------------------------
class FillTool(Tool):
    name, label, shortcut, glyph = "fill", "Paint bucket", "G", "🪣"
    tooltip = "Paint Bucket (G): click to fill similar colours with the foreground colour."

    def option_specs(self):
        return [
            _spec("tolerance", "Tolerance", "int", 32, min=0, max=255),
            _spec("contiguous", "Contiguous", "bool", True),
            _spec("sample_all", "Sample all layers", "bool", False),
            _spec("opacity", "Opacity", "int", 100, min=1, max=100, suffix="%"),
        ]

    def press(self, view, pos, ev):
        doc = view.doc
        x, y = int(pos.x()), int(pos.y())
        if not (0 <= x < doc.width and 0 <= y < doc.height):
            return
        src = doc.composite() if self.opt("sample_all") else doc.active_layer.pixels
        region = color_region(src, x, y, int(self.opt("tolerance")), bool(self.opt("contiguous")))
        color = self.ctx.bg if ev.modifiers() & Qt.KeyboardModifier.AltModifier else self.ctx.fg
        paint = np.zeros((doc.height, doc.width, 4), dtype=np.uint8)
        paint[..., :3] = color_to_rgba(color)[:3]
        paint[..., 3] = region
        stamp_paint(view, paint, "Fill", self.opt("opacity") / 100.0)


class GradientTool(Tool):
    name, label, shortcut, glyph = "gradient", "Gradient", "Shift+G", "▤"
    tooltip = "Gradient (Shift+G): drag from the foreground colour to the background colour."

    def __init__(self, ctx):
        super().__init__(ctx)
        self._a = self._b = None

    def option_specs(self):
        return [
            _spec("kind", "Type", "choice", "Linear", choices=["Linear", "Radial", "Reflected"]),
            _spec("opacity", "Opacity", "int", 100, min=1, max=100, suffix="%"),
            _spec("reverse", "Reverse", "bool", False),
            _spec("transparent", "FG to transparent", "bool", False),
        ]

    def press(self, view, pos, ev):
        self._a = self._b = pos

    def move(self, view, pos, ev):
        if self._a is not None and ev.buttons() & Qt.MouseButton.LeftButton:
            if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                dx, dy = pos.x() - self._a.x(), pos.y() - self._a.y()
                ang = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
                d = math.hypot(dx, dy)
                pos = QPointF(self._a.x() + d * math.cos(ang), self._a.y() + d * math.sin(ang))
            self._b = pos

    def release(self, view, pos, ev):
        if self._a is None:
            return
        a, b = self._a, self._b
        self._a = self._b = None
        if (b - a).manhattanLength() < 1:
            return
        doc = view.doc
        c0, c1 = QColor(self.ctx.fg), QColor(self.ctx.bg)
        if self.opt("transparent"):
            c1 = QColor(c0)
            c1.setAlpha(0)
        if self.opt("reverse"):
            c0, c1 = c1, c0
        kind = self.opt("kind")
        if kind == "Radial":
            g = QRadialGradient(a, math.hypot(b.x() - a.x(), b.y() - a.y()))
        elif kind == "Reflected":
            g = QLinearGradient(a, b)
            g.setSpread(QLinearGradient.Spread.ReflectSpread)
            g.setColorAt(0.0, c0)
            g.setColorAt(1.0, c1)
        else:
            g = QLinearGradient(a, b)
        if kind != "Reflected":
            g.setColorAt(0.0, c0)
            g.setColorAt(1.0, c1)
        arr, img, p = new_paint_canvas(doc)
        p.fillRect(0, 0, doc.width, doc.height, QBrush(g))
        p.end()
        stamp_paint(view, arr, "Gradient", self.opt("opacity") / 100.0)

    def draw_overlay(self, p, view):
        if self._a is None or self._b is None:
            return
        p.setPen(QPen(QColor(255, 255, 255), 1))
        p.drawLine(view.doc_to_view(self._a), view.doc_to_view(self._b))
        p.setPen(QPen(QColor(0, 0, 0), 1, Qt.PenStyle.DotLine))
        p.drawLine(view.doc_to_view(self._a), view.doc_to_view(self._b))


class ShapeTool(Tool):
    name, label, shortcut, glyph = "shape", "Shape", "U", "◯"
    tooltip = "Shape (U): drag to draw a rectangle, ellipse or line. Ctrl constrains proportions."

    def __init__(self, ctx):
        super().__init__(ctx)
        self._a = self._b = None

    def option_specs(self):
        return [
            _spec("shape", "Shape", "choice", "Rectangle", choices=["Rectangle", "Rounded rect", "Ellipse", "Line"]),
            _spec("fill", "Fill", "bool", True),
            _spec("stroke", "Stroke", "int", 0, min=0, max=200, suffix="px"),
            _spec("radius", "Corner radius", "int", 12, min=0, max=500),
            _spec("opacity", "Opacity", "int", 100, min=1, max=100, suffix="%"),
            _spec("new_layer", "New layer", "bool", True),
        ]

    def press(self, view, pos, ev):
        self._a = self._b = pos

    def move(self, view, pos, ev):
        if self._a is not None and ev.buttons() & Qt.MouseButton.LeftButton:
            if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
                dx, dy = pos.x() - self._a.x(), pos.y() - self._a.y()
                s = max(abs(dx), abs(dy))
                pos = QPointF(self._a.x() + math.copysign(s, dx), self._a.y() + math.copysign(s, dy))
            self._b = pos

    def _draw(self, p, a, b, scale=1.0):
        shape = self.opt("shape")
        stroke = self.opt("stroke") * scale
        fill = QBrush(self.ctx.fg) if self.opt("fill") else Qt.BrushStyle.NoBrush
        pen = QPen(self.ctx.bg if self.opt("fill") else self.ctx.fg, stroke) if stroke > 0 else QPen(Qt.PenStyle.NoPen)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        if shape == "Line":
            pen = QPen(self.ctx.fg, max(1.0, stroke if stroke > 0 else 2 * scale))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(a, b)
            return
        p.setPen(pen)
        p.setBrush(fill)
        r = QRectF(a, b).normalized()
        if shape == "Ellipse":
            p.drawEllipse(r)
        elif shape == "Rounded rect":
            rad = self.opt("radius") * scale
            p.drawRoundedRect(r, rad, rad)
        else:
            p.drawRect(r)

    def release(self, view, pos, ev):
        if self._a is None:
            return
        a, b = self._a, self._b
        self._a = self._b = None
        if (b - a).manhattanLength() < 1:
            return
        doc = view.doc
        arr, img, p = new_paint_canvas(doc)
        self._draw(p, a, b)
        p.end()
        if self.opt("new_layer"):
            view.history.push("Shape", detach=None)
            layer = doc.add_layer(f"{self.opt('shape')} {len(doc.layers) + 1}")
            if doc.selection is not None:
                arr[..., 3] = (arr[..., 3].astype(np.float32) * doc.selection / 255.0 + 0.5).astype(np.uint8)
            arr[..., 3] = (arr[..., 3].astype(np.float32) * self.opt("opacity") / 100.0 + 0.5).astype(np.uint8)
            layer.pixels = arr
            doc.changed(structure=True)
        else:
            stamp_paint(view, arr, "Shape", self.opt("opacity") / 100.0)

    def draw_overlay(self, p, view):
        if self._a is None or self._b is None:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setOpacity(0.7)
        self._draw(p, view.doc_to_view(self._a), view.doc_to_view(self._b), scale=view.zoom)


class TextTool(Tool):
    name, label, shortcut, glyph = "text", "Text", "T", "T"
    cursor = Qt.CursorShape.IBeamCursor
    tooltip = "Text (T): click where the text should start; a dialog asks for the text and font."

    def option_specs(self):
        return [
            _spec("font", "Font", "font", "Arial"),
            _spec("size", "Size", "int", 48, min=4, max=2000, suffix="px"),
            _spec("bold", "Bold", "bool", False),
            _spec("italic", "Italic", "bool", False),
            _spec("align", "Align", "choice", "Left", choices=["Left", "Center", "Right"]),
        ]

    def press(self, view, pos, ev):
        self._pos = pos

    def release(self, view, pos, ev):
        from .dialogs import TextDialog
        dlg = TextDialog(view.ctx, view.window())
        if not dlg.exec():
            return
        text = dlg.text()
        if not text.strip():
            return
        self.render_text(view, text, self._pos)

    def render_text(self, view, text: str, pos: QPointF):
        doc = view.doc
        font = QFont(self.opt("font"))
        font.setPixelSize(int(self.opt("size")))
        font.setBold(bool(self.opt("bold")))
        font.setItalic(bool(self.opt("italic")))
        arr, img, p = new_paint_canvas(doc)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setFont(font)
        p.setPen(self.ctx.fg)
        align = {"Left": Qt.AlignmentFlag.AlignLeft, "Center": Qt.AlignmentFlag.AlignHCenter,
                 "Right": Qt.AlignmentFlag.AlignRight}[self.opt("align")]
        big = QRectF(-doc.width * 4, pos.y(), doc.width * 9, doc.height * 4)
        if align == Qt.AlignmentFlag.AlignLeft:
            big.moveLeft(pos.x())
        elif align == Qt.AlignmentFlag.AlignRight:
            big.moveRight(pos.x())
        else:
            big.moveLeft(pos.x() - big.width() / 2)
        p.drawText(big, int(align | Qt.AlignmentFlag.AlignTop), text)
        p.end()
        view.history.push("Text", detach=None)
        name = text.strip().splitlines()[0][:24]
        layer = doc.add_layer(name)
        layer.pixels = arr
        doc.changed(structure=True)


ALL_TOOLS = [MoveTool, MarqueeTool, LassoTool, MagicWandTool, CropTool, EyedropperTool,
             BrushTool, EraserTool, CloneStampTool, FillTool, GradientTool, ShapeTool, TextTool,
             HandTool, ZoomTool]
