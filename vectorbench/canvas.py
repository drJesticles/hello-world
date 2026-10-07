"""The artboard view: zoom/pan, rulers, grid, guides, snapping, selection overlay, inline text editing."""
from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QKeyEvent, QMouseEvent, QPainter, QPen, QTextOption, QWheelEvent
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from .history import History
from .model import Document, GroupItem, Guide, Item, PathItem, TextItem

RULER = 20
HANDLE = 7
ZOOM_LEVELS = [0.05, 0.1, 0.15, 0.25, 0.33, 0.5, 0.67, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0, 32.0, 64.0]
SEL_COLOR = QColor(40, 120, 255)


class TextEditor(QPlainTextEdit):
    """Inline editor laid over a TextItem."""

    def __init__(self, canvas, item: TextItem):
        super().__init__(canvas)
        self.canvas = canvas
        self.item = item
        self.setPlainText(item.text)
        self.setFrameStyle(0)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setStyleSheet("background: rgba(255,255,255,235); color: #111; border: 1px solid #2a7fff;")
        self.textChanged.connect(self._changed)
        self.relayout()
        self.selectAll()
        self.show()
        self.setFocus()

    def relayout(self):
        z = self.canvas.zoom
        f = QFont(self.item.family)
        f.setPixelSize(max(4, int(round(self.item.size * z))))
        f.setBold(self.item.bold); f.setItalic(self.item.italic)
        self.setFont(f)
        bb = self.item.bbox()
        tl = self.canvas.doc_to_view(QPointF(bb.left(), self.item.pos.y() - self.item.size))
        lines = max(1, self.item.text.count("\n") + 1)
        w = max(120, int(bb.width() * z) + 40)
        h = int(lines * self.item.size * self.item.line_height * z) + 16
        self.setGeometry(int(tl.x()) - 4, int(tl.y()) - 4, w, h)

    def _changed(self):
        self.item.text = self.toPlainText()
        self.canvas.doc.changed()
        self.relayout()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape or (ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and ev.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.canvas.end_text_edit()
            return
        super().keyPressEvent(ev)

    def focusOutEvent(self, ev):
        super().focusOutEvent(ev)
        self.canvas.end_text_edit()


class VectorCanvas(QWidget):
    selectionChanged = Signal()
    zoomChanged = Signal(float)
    cursorMoved = Signal(float, float)

    def __init__(self, doc: Document, ctx, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.history = History(doc)
        self.ctx = ctx
        self.zoom = 1.0
        self.offset = QPointF(RULER + 40, RULER + 40)
        self.selection: list[Item] = []
        self.selected_nodes: set[tuple[str, int, int]] = set()
        self.show_grid = False
        self.snap_grid = False
        self.snap_points = True
        self.show_guides = True
        self.lock_guides = False
        self.show_rulers = True
        self.outline_mode = False
        self._space = False
        self._panning = False
        self._pan_start = QPoint()
        self._pan_offset0 = QPointF()
        self._guide_drag: tuple[bool, float] | None = None
        self._editor: TextEditor | None = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        doc.listeners.append(self._on_doc_changed)

    # ------------------------------------------------------------ mapping
    def doc_to_view(self, p: QPointF) -> QPointF:
        return QPointF(self.offset.x() + p.x() * self.zoom, self.offset.y() + p.y() * self.zoom)

    def view_to_doc(self, p) -> QPointF:
        return QPointF((p.x() - self.offset.x()) / self.zoom, (p.y() - self.offset.y()) / self.zoom)

    def view_rect(self, r: QRectF) -> QRectF:
        return QRectF(self.doc_to_view(r.topLeft()), self.doc_to_view(r.bottomRight()))

    def tol(self) -> float:
        return 5.0 / self.zoom

    # ------------------------------------------------------------ zoom/pan
    def set_zoom(self, z, anchor_view=None):
        z = max(0.02, min(64.0, float(z)))
        if anchor_view is None:
            anchor_view = QPointF(self.width() / 2, self.height() / 2)
        d = self.view_to_doc(anchor_view)
        self.zoom = z
        self.offset = QPointF(anchor_view.x() - d.x() * z, anchor_view.y() - d.y() * z)
        self.zoomChanged.emit(z)
        if self._editor:
            self._editor.relayout()
        self.update()

    def zoom_in(self, anchor=None):
        self.set_zoom(next((l for l in ZOOM_LEVELS if l > self.zoom + 1e-6), self.zoom * 1.25), anchor)

    def zoom_out(self, anchor=None):
        self.set_zoom(next((l for l in reversed(ZOOM_LEVELS) if l < self.zoom - 1e-6), self.zoom / 1.25), anchor)

    def fit_in_view(self):
        m = 40
        zw = (self.width() - RULER - m * 2) / max(self.doc.width, 1)
        zh = (self.height() - RULER - m * 2) / max(self.doc.height, 1)
        self.zoom = max(0.02, min(zw, zh))
        self.offset = QPointF(RULER + (self.width() - RULER - self.doc.width * self.zoom) / 2,
                              RULER + (self.height() - RULER - self.doc.height * self.zoom) / 2)
        self.zoomChanged.emit(self.zoom)
        self.update()

    def zoom_to_rect(self, r: QRectF):
        if r.isEmpty():
            return
        m = 40
        z = min((self.width() - RULER - 2 * m) / r.width(), (self.height() - RULER - 2 * m) / r.height())
        self.zoom = max(0.02, min(64.0, z))
        c = r.center()
        self.offset = QPointF(RULER + (self.width() - RULER) / 2 - c.x() * self.zoom, RULER + (self.height() - RULER) / 2 - c.y() * self.zoom)
        self.zoomChanged.emit(self.zoom)
        self.update()

    def pan_by(self, dx, dy):
        self.offset = QPointF(self.offset.x() + dx, self.offset.y() + dy)
        self.update()

    # ------------------------------------------------------------ selection
    def set_selection(self, items: list[Item], nodes=None):
        # resolve by id so stale references (e.g. after undo rebuilt the document) still work
        self.selection = [self.doc.find(i.id) or i for i in items if i is not None]
        self.selected_nodes = set(nodes) if nodes else set()
        self.selectionChanged.emit()
        self.update()

    def select_toggle(self, item: Item):
        if item in self.selection:
            self.selection.remove(item)
        else:
            self.selection.append(item)
        self.selectionChanged.emit()
        self.update()

    def clear_selection(self):
        self.set_selection([])

    def selection_bounds(self) -> QRectF:
        r = QRectF()
        for it in self.selection:
            b = it.bbox()
            r = b if r.isNull() else r.united(b)
        return r

    def prune_selection(self):
        """Drop selected items that no longer exist (after undo, delete...)."""
        ids = {i.id for i in self.doc.all_items(include_hidden=True)}
        live = []
        for it in self.selection:
            if it.id in ids:
                live.append(self.doc.find(it.id) or it)
        changed = len(live) != len(self.selection) or any(a is not b for a, b in zip(live, self.selection))
        self.selection = live
        self.selected_nodes = {k for k in self.selected_nodes if k[0] in ids}
        if changed:
            self.selectionChanged.emit()

    # ------------------------------------------------------------ snapping
    def snap(self, p: QPointF, exclude: list[Item] | None = None) -> QPointF:
        best = QPointF(p)
        tol = 6.0 / self.zoom
        bx = by = None
        if self.snap_grid and self.doc.grid_size > 0:
            g = self.doc.grid_size
            bx, by = round(p.x() / g) * g, round(p.y() / g) * g
        if self.show_guides:
            for gd in self.doc.guides:
                if gd.horizontal and abs(p.y() - gd.pos) < tol:
                    by = gd.pos
                elif not gd.horizontal and abs(p.x() - gd.pos) < tol:
                    bx = gd.pos
        if self.snap_points:
            ex = set(id(i) for i in (exclude or []))
            for it in self.doc.all_items():
                if id(it) in ex:
                    continue
                b = it.bbox()
                if not b.adjusted(-tol, -tol, tol, tol).contains(p):
                    continue
                cand = [b.topLeft(), b.topRight(), b.bottomLeft(), b.bottomRight(), b.center()]
                if isinstance(it, PathItem):
                    cand += [it.transform.map(nd.p) for _, _, nd in it.all_nodes()]
                for c in cand:
                    if abs(c.x() - p.x()) < tol and abs(c.y() - p.y()) < tol:
                        return QPointF(c)
        # artboard edges
        for v in (0.0, self.doc.width, self.doc.width / 2):
            if abs(p.x() - v) < tol and bx is None:
                bx = v
        for v in (0.0, self.doc.height, self.doc.height / 2):
            if abs(p.y() - v) < tol and by is None:
                by = v
        if bx is not None:
            best.setX(bx)
        if by is not None:
            best.setY(by)
        return best

    # ------------------------------------------------------------ text editing
    def begin_text_edit(self, item: TextItem):
        self.end_text_edit()
        self.history.begin("Edit Text")
        self._editor = TextEditor(self, item)

    def end_text_edit(self):
        if self._editor is None:
            return
        ed, self._editor = self._editor, None
        item = ed.item
        ed.hide(); ed.deleteLater()
        if not item.text.strip():
            self.doc.remove_item(item)
            self.selection = [i for i in self.selection if i is not item]
            self.history.cancel() if False else None
        self.history.commit()
        self.doc.changed(structure=True)
        self.setFocus()

    def is_editing_text(self):
        return self._editor is not None

    # ------------------------------------------------------------ doc events
    def _on_doc_changed(self, structure):
        if structure:
            self.prune_selection()
        self.update()

    # ------------------------------------------------------------ painting
    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(72, 72, 72))
        art = self.view_rect(self.doc.rect)
        p.fillRect(art.translated(4, 4), QColor(0, 0, 0, 90))
        p.fillRect(art, QColor(255, 255, 255))
        if self.show_grid and self.doc.grid_size * self.zoom >= 4:
            self._paint_grid(p, art)
        p.save()
        p.setClipRect(self.rect().adjusted(RULER if self.show_rulers else 0, RULER if self.show_rulers else 0, 0, 0))
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.translate(self.offset)
        p.scale(self.zoom, self.zoom)
        self.doc.paint(p, outline=self.outline_mode)
        p.restore()
        p.setPen(QPen(QColor(0, 0, 0, 120), 1)); p.setBrush(Qt.BrushStyle.NoBrush); p.drawRect(art)
        if self.show_guides:
            p.setPen(QPen(QColor(0, 200, 255), 1))
            for g in self.doc.guides:
                if g.horizontal:
                    y = self.offset.y() + g.pos * self.zoom
                    p.drawLine(QPointF(RULER, y), QPointF(self.width(), y))
                else:
                    x = self.offset.x() + g.pos * self.zoom
                    p.drawLine(QPointF(x, RULER), QPointF(x, self.height()))
        self._paint_selection(p)
        tool = self.ctx.tool
        if tool is not None:
            p.save()
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            try:
                tool.draw_overlay(p, self)
            finally:
                p.restore()
        if self.show_rulers:
            self._paint_rulers(p)
        p.end()

    def _paint_grid(self, p, art):
        g = self.doc.grid_size * self.zoom
        p.setPen(QPen(QColor(0, 0, 0, 28), 1))
        x = art.left()
        i = 0
        while x <= art.right():
            p.setPen(QPen(QColor(0, 0, 0, 60 if i % 5 == 0 else 24), 1))
            p.drawLine(QPointF(x, art.top()), QPointF(x, art.bottom())); x += g; i += 1
        y = art.top(); i = 0
        while y <= art.bottom():
            p.setPen(QPen(QColor(0, 0, 0, 60 if i % 5 == 0 else 24), 1))
            p.drawLine(QPointF(art.left(), y), QPointF(art.right(), y)); y += g; i += 1

    def _paint_rulers(self, p):
        p.fillRect(QRect(0, 0, self.width(), RULER), QColor(50, 50, 50))
        p.fillRect(QRect(0, 0, RULER, self.height()), QColor(50, 50, 50))
        p.setPen(QPen(QColor(170, 170, 170), 1))
        f = QFont(); f.setPixelSize(9); p.setFont(f)
        step = self._ruler_step()
        start = math.floor((RULER - self.offset.x()) / self.zoom / step) * step
        v = start
        while self.offset.x() + v * self.zoom < self.width():
            x = self.offset.x() + v * self.zoom
            if x >= RULER:
                p.drawLine(QPointF(x, RULER - 6), QPointF(x, RULER))
                p.drawText(QPointF(x + 2, RULER - 8), str(int(v)))
                for k in range(1, 5):
                    xs = x + step * self.zoom * k / 5
                    p.drawLine(QPointF(xs, RULER - 3), QPointF(xs, RULER))
            v += step
        start = math.floor((RULER - self.offset.y()) / self.zoom / step) * step
        v = start
        while self.offset.y() + v * self.zoom < self.height():
            y = self.offset.y() + v * self.zoom
            if y >= RULER:
                p.drawLine(QPointF(RULER - 6, y), QPointF(RULER, y))
                p.save(); p.translate(RULER - 8, y + 2); p.rotate(-90); p.drawText(QPointF(0, 0), str(int(v))); p.restore()
                for k in range(1, 5):
                    ys = y + step * self.zoom * k / 5
                    p.drawLine(QPointF(RULER - 3, ys), QPointF(RULER, ys))
            v += step
        p.fillRect(QRect(0, 0, RULER, RULER), QColor(40, 40, 40))

    def _ruler_step(self):
        target = 80 / self.zoom
        for s in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000):
            if s >= target:
                return s
        return 10000

    def handle_rects(self) -> dict[str, QRectF]:
        b = self.selection_bounds()
        if b.isNull():
            return {}
        r = self.view_rect(b)
        h = HANDLE
        pts = {"nw": r.topLeft(), "n": QPointF(r.center().x(), r.top()), "ne": r.topRight(),
               "e": QPointF(r.right(), r.center().y()), "se": r.bottomRight(), "s": QPointF(r.center().x(), r.bottom()),
               "sw": r.bottomLeft(), "w": QPointF(r.left(), r.center().y())}
        return {k: QRectF(v.x() - h / 2, v.y() - h / 2, h, h) for k, v in pts.items()}

    def _paint_selection(self, p):
        if not self.selection:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        tool = self.ctx.tool
        direct = tool is not None and tool.name in ("direct", "pen", "add_anchor", "del_anchor", "convert")
        for it in self.selection:
            p.setPen(QPen(SEL_COLOR, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            if direct or isinstance(it, PathItem):
                p.save()
                p.translate(self.offset); p.scale(self.zoom, self.zoom)
                pen = QPen(SEL_COLOR, 0); p.setPen(pen)
                p.drawPath(it.doc_path())
                p.restore()
            else:
                p.drawRect(self.view_rect(it.bbox()))
        if direct:
            for it in self.selection:
                if isinstance(it, PathItem):
                    self._paint_anchors(p, it)
                else:
                    p.setPen(QPen(SEL_COLOR, 1))
                    for pt in [it.bbox().topLeft(), it.bbox().topRight(), it.bbox().bottomLeft(), it.bbox().bottomRight()]:
                        v = self.doc_to_view(pt)
                        p.fillRect(QRectF(v.x() - 3, v.y() - 3, 6, 6), QColor(255, 255, 255)); p.drawRect(QRectF(v.x() - 3, v.y() - 3, 6, 6))
        elif tool is not None and tool.name in ("select", "scale", "rotate", "free"):
            p.setPen(QPen(SEL_COLOR, 1))
            b = self.view_rect(self.selection_bounds())
            p.drawRect(b)
            for r in self.handle_rects().values():
                p.fillRect(r, QColor(255, 255, 255)); p.drawRect(r)

    def _paint_anchors(self, p, it: PathItem):
        p.setPen(QPen(SEL_COLOR, 1))
        for si, ni, nd in it.all_nodes():
            v = self.doc_to_view(it.transform.map(nd.p))
            sel = (it.id, si, ni) in self.selected_nodes
            if sel:
                for h in (nd.h_in, nd.h_out):
                    if h != nd.p:
                        hv = self.doc_to_view(it.transform.map(h))
                        p.setPen(QPen(SEL_COLOR, 1)); p.drawLine(v, hv)
                        p.setBrush(QColor(255, 255, 255)); p.drawEllipse(hv, 3, 3)
                p.setBrush(SEL_COLOR)
            else:
                p.setBrush(QColor(255, 255, 255))
            p.setPen(QPen(SEL_COLOR, 1))
            p.drawRect(QRectF(v.x() - 3, v.y() - 3, 6, 6))

    # ------------------------------------------------------------ input
    def _tool(self):
        return self.ctx.tool

    def mousePressEvent(self, ev: QMouseEvent):
        self.setFocus()
        if self._editor is not None:
            self.end_text_edit()
        pos = ev.position()
        if self.show_rulers and (pos.x() < RULER or pos.y() < RULER) and ev.button() == Qt.MouseButton.LeftButton:
            if not self.lock_guides:
                self._guide_drag = (pos.y() < RULER, 0.0)  # horizontal guide from the top ruler
            return
        if ev.button() == Qt.MouseButton.MiddleButton or (ev.button() == Qt.MouseButton.LeftButton and self._space):
            self._panning = True; self._pan_start = pos.toPoint(); self._pan_offset0 = QPointF(self.offset)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        t = self._tool()
        if t is not None:
            t.press(self, self.view_to_doc(pos), ev)
            self.update()

    def mouseMoveEvent(self, ev: QMouseEvent):
        pos = ev.position()
        d = self.view_to_doc(pos)
        self.cursorMoved.emit(d.x(), d.y())
        if self._guide_drag is not None:
            horizontal = self._guide_drag[0]
            self._guide_drag = (horizontal, d.y() if horizontal else d.x())
            self.update()
            return
        if self._panning:
            delta = pos.toPoint() - self._pan_start
            self.offset = QPointF(self._pan_offset0.x() + delta.x(), self._pan_offset0.y() + delta.y())
            self.update()
            return
        t = self._tool()
        if t is not None:
            t.move(self, d, ev)
            self.update()

    def mouseReleaseEvent(self, ev: QMouseEvent):
        if self._guide_drag is not None:
            horizontal, v = self._guide_drag
            self._guide_drag = None
            pos = ev.position()
            if pos.x() > RULER and pos.y() > RULER:
                self.history.push("Add Guide")
                self.doc.guides.append(Guide(horizontal, v))
                self.doc.changed()
            self.update()
            return
        if self._panning:
            self._panning = False
            self._update_cursor()
            return
        t = self._tool()
        if t is not None:
            t.release(self, self.view_to_doc(ev.position()), ev)
            self.update()

    def mouseDoubleClickEvent(self, ev: QMouseEvent):
        t = self._tool()
        if t is not None:
            t.double_click(self, self.view_to_doc(ev.position()), ev)
            self.update()

    def wheelEvent(self, ev: QWheelEvent):
        d = ev.angleDelta().y()
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            (self.zoom_in if d > 0 else self.zoom_out)(ev.position())
        elif ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.pan_by(d / 2.0, 0)
        else:
            self.pan_by(ev.angleDelta().x() / 2.0, d / 2.0)

    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Space and not ev.isAutoRepeat():
            self._space = True; self.setCursor(Qt.CursorShape.OpenHandCursor); return
        t = self._tool()
        if t is not None and t.key_press(self, ev):
            self.update(); return
        super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Space and not ev.isAutoRepeat():
            self._space = False; self._update_cursor(); return
        super().keyReleaseEvent(ev)

    def _update_cursor(self):
        t = self._tool()
        self.setCursor(t.cursor if t is not None else Qt.CursorShape.ArrowCursor)

    def tool_changed(self):
        self._update_cursor()
        self.update()

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        if self.ctx.window is not None:
            for url in ev.mimeData().urls():
                self.ctx.window.open_or_place(url.toLocalFile(), self.view_to_doc(ev.position()))

    def draw_guide_preview(self, p):
        pass
