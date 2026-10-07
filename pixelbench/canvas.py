"""The document view: zoom/pan, checkerboard, composite rendering, selection ants, tool overlay."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QImage, QKeyEvent, QMouseEvent, QPainter, QPen, QPixmap,
                           QWheelEvent)
from PySide6.QtWidgets import QWidget

from .document import Document
from .history import History
from .qtutil import qimage_from_array
from .selection import outline_points

ZOOM_LEVELS = [0.05, 0.1, 0.15, 0.25, 0.33, 0.5, 0.67, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0, 32.0]


def _checker_pixmap(size: int = 16) -> QPixmap:
    pm = QPixmap(size * 2, size * 2)
    pm.fill(QColor(200, 200, 200))
    p = QPainter(pm)
    p.fillRect(0, 0, size, size, QColor(150, 150, 150))
    p.fillRect(size, size, size, size, QColor(150, 150, 150))
    p.end()
    return pm


class CanvasView(QWidget):
    zoomChanged = Signal(float)
    cursorMoved = Signal(float, float)

    def __init__(self, doc: Document, ctx, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.history = History(doc)
        self.ctx = ctx
        self.zoom = 1.0
        self.offset = QPointF(0, 0)  # widget-space position of document pixel (0, 0)
        self._checker = QBrush(_checker_pixmap())
        self._composite_img: QImage | None = None
        self._composite_version = -1
        self._ants_overlay: np.ndarray | None = None
        self._ants_pts = None
        self._ants_sel_id = None
        self._ants_phase = 0
        self._ants_timer = QTimer(self)
        self._ants_timer.setInterval(120)
        self._ants_timer.timeout.connect(self._tick_ants)
        self._space_down = False
        self._panning = False
        self._pan_start = QPoint()
        self._pan_offset0 = QPointF()
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAcceptDrops(True)
        doc.listeners.append(self._on_doc_changed)

    # -- coordinate mapping -------------------------------------------------
    def doc_to_view(self, p: QPointF) -> QPointF:
        return QPointF(self.offset.x() + p.x() * self.zoom, self.offset.y() + p.y() * self.zoom)

    def view_to_doc(self, p) -> QPointF:
        return QPointF((p.x() - self.offset.x()) / self.zoom, (p.y() - self.offset.y()) / self.zoom)

    def doc_rect_in_view(self) -> QRectF:
        return QRectF(self.offset, QPointF(self.offset.x() + self.doc.width * self.zoom,
                                           self.offset.y() + self.doc.height * self.zoom))

    # -- zoom / pan ---------------------------------------------------------
    def set_zoom(self, z: float, anchor_view: QPointF | None = None):
        z = max(0.02, min(64.0, float(z)))
        if anchor_view is None:
            anchor_view = QPointF(self.width() / 2, self.height() / 2)
        doc_pt = self.view_to_doc(anchor_view)
        self.zoom = z
        self.offset = QPointF(anchor_view.x() - doc_pt.x() * z, anchor_view.y() - doc_pt.y() * z)
        self._clamp_offset()
        self.zoomChanged.emit(self.zoom)
        self.update()

    def zoom_in(self, anchor: QPointF | None = None):
        nxt = next((l for l in ZOOM_LEVELS if l > self.zoom + 1e-6), self.zoom * 1.25)
        self.set_zoom(nxt, anchor)

    def zoom_out(self, anchor: QPointF | None = None):
        nxt = next((l for l in reversed(ZOOM_LEVELS) if l < self.zoom - 1e-6), self.zoom / 1.25)
        self.set_zoom(nxt, anchor)

    def fit_in_view(self):
        if self.doc.width == 0 or self.doc.height == 0:
            return
        margin = 24
        zw = (self.width() - margin * 2) / self.doc.width
        zh = (self.height() - margin * 2) / self.doc.height
        self.zoom = max(0.02, min(zw, zh))
        self.center()
        self.zoomChanged.emit(self.zoom)

    def center(self):
        self.offset = QPointF((self.width() - self.doc.width * self.zoom) / 2,
                              (self.height() - self.doc.height * self.zoom) / 2)
        self.update()

    def _clamp_offset(self):
        """Keep at least a sliver of the document visible."""
        w, h = self.doc.width * self.zoom, self.doc.height * self.zoom
        minx, maxx = -w + 40, self.width() - 40
        miny, maxy = -h + 40, self.height() - 40
        self.offset = QPointF(max(minx, min(maxx, self.offset.x())), max(miny, min(maxy, self.offset.y())))

    def pan_by(self, dx: float, dy: float):
        self.offset = QPointF(self.offset.x() + dx, self.offset.y() + dy)
        self._clamp_offset()
        self.update()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._clamp_offset()

    # -- document change ----------------------------------------------------
    def _on_doc_changed(self, structure: bool):
        self.update()

    def composite_image(self) -> QImage:
        if self._composite_version != self.doc.version or self._composite_img is None:
            arr = self.doc.composite()
            self._composite_img = qimage_from_array(arr)
            self._composite_version = self.doc.version
        return self._composite_img

    # -- marching ants ------------------------------------------------------
    def _ensure_ants(self):
        sel = self.doc.selection
        if sel is None:
            self._ants_overlay = None
            self._ants_pts = None
            self._ants_sel_id = None
            if self._ants_timer.isActive():
                self._ants_timer.stop()
            return
        if self._ants_sel_id != id(sel) or self._ants_overlay is None or self._ants_overlay.shape[:2] != sel.shape:
            ys, xs = outline_points(sel)
            self._ants_pts = (ys, xs)
            self._ants_overlay = np.zeros((sel.shape[0], sel.shape[1], 4), dtype=np.uint8)
            self._ants_sel_id = id(sel)
            self._paint_ants()
        if not self._ants_timer.isActive():
            self._ants_timer.start()

    def _paint_ants(self):
        ys, xs = self._ants_pts
        if len(ys) == 0:
            return
        on = ((xs + ys + self._ants_phase) // 4) % 2 == 0
        colors = np.where(on[:, None], np.array([255, 255, 255, 255], np.uint8), np.array([0, 0, 0, 255], np.uint8))
        self._ants_overlay[ys, xs] = colors

    def _tick_ants(self):
        if self.doc.selection is None or self._ants_pts is None:
            return
        self._ants_phase = (self._ants_phase + 1) % 8
        self._paint_ants()
        self.update()

    # -- painting -----------------------------------------------------------
    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(40, 40, 40))
        r = self.doc_rect_in_view()
        p.fillRect(r, self._checker)

        p.save()
        p.translate(self.offset)
        p.scale(self.zoom, self.zoom)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.zoom < 1.0)
        p.drawImage(QPointF(0, 0), self.composite_image())

        self._ensure_ants()
        if self._ants_overlay is not None:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            p.drawImage(QPointF(0, 0), qimage_from_array(self._ants_overlay))
        p.restore()

        if self.zoom >= 8:
            p.setPen(QPen(QColor(0, 0, 0, 60), 0))
            x0 = max(0, int(-self.offset.x() / self.zoom))
            x1 = min(self.doc.width, int((self.width() - self.offset.x()) / self.zoom) + 1)
            y0 = max(0, int(-self.offset.y() / self.zoom))
            y1 = min(self.doc.height, int((self.height() - self.offset.y()) / self.zoom) + 1)
            for x in range(x0, x1 + 1):
                vx = self.offset.x() + x * self.zoom
                p.drawLine(QPointF(vx, r.top()), QPointF(vx, r.bottom()))
            for y in range(y0, y1 + 1):
                vy = self.offset.y() + y * self.zoom
                p.drawLine(QPointF(r.left(), vy), QPointF(r.right(), vy))

        p.setPen(QPen(QColor(0, 0, 0), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r.adjusted(-1, -1, 0, 0))

        tool = self.ctx.tool
        if tool is not None:
            p.save()
            try:
                tool.draw_overlay(p, self)
            finally:
                p.restore()
        p.end()

    # -- input --------------------------------------------------------------
    def _tool(self):
        return self.ctx.tool

    def mousePressEvent(self, ev: QMouseEvent):
        self.setFocus()
        if ev.button() == Qt.MouseButton.MiddleButton or (ev.button() == Qt.MouseButton.LeftButton and self._space_down):
            self._panning = True
            self._pan_start = ev.position().toPoint()
            self._pan_offset0 = QPointF(self.offset)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        t = self._tool()
        if t is not None and self.doc.layers:
            t.press(self, self.view_to_doc(ev.position()), ev)
            self.update()

    def mouseMoveEvent(self, ev: QMouseEvent):
        pos = self.view_to_doc(ev.position())
        self.cursorMoved.emit(pos.x(), pos.y())
        if self._panning:
            d = ev.position().toPoint() - self._pan_start
            self.offset = QPointF(self._pan_offset0.x() + d.x(), self._pan_offset0.y() + d.y())
            self._clamp_offset()
            self.update()
            return
        t = self._tool()
        if t is not None and self.doc.layers:
            t.move(self, pos, ev)
            self.update()

    def mouseReleaseEvent(self, ev: QMouseEvent):
        if self._panning:
            self._panning = False
            self._update_cursor()
            return
        t = self._tool()
        if t is not None and self.doc.layers:
            t.release(self, self.view_to_doc(ev.position()), ev)
            self.update()

    def mouseDoubleClickEvent(self, ev: QMouseEvent):
        t = self._tool()
        if t is not None and self.doc.layers:
            t.double_click(self, self.view_to_doc(ev.position()), ev)
            self.update()

    def wheelEvent(self, ev: QWheelEvent):
        delta = ev.angleDelta().y()
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if delta > 0:
                self.zoom_in(ev.position())
            elif delta < 0:
                self.zoom_out(ev.position())
        elif ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.pan_by(delta / 2.0, 0)
        else:
            self.pan_by(ev.angleDelta().x() / 2.0, delta / 2.0)

    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Space and not ev.isAutoRepeat():
            self._space_down = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            return
        t = self._tool()
        if t is not None and t.key_press(self, ev):
            self.update()
            return
        super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Space and not ev.isAutoRepeat():
            self._space_down = False
            self._update_cursor()
            return
        super().keyReleaseEvent(ev)

    def leaveEvent(self, ev):
        self.update()

    def _update_cursor(self):
        t = self._tool()
        self.setCursor(t.cursor if t is not None else Qt.CursorShape.ArrowCursor)

    def tool_changed(self):
        self._update_cursor()
        self.update()

    # -- drag & drop of files -------------------------------------------------
    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        if self.ctx.window is not None:
            for url in ev.mimeData().urls():
                self.ctx.window.open_path(url.toLocalFile())
