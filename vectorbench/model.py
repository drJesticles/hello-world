"""Document model for VectorBench.

Everything is plain Python + Qt value types and serialises to JSON (``to_dict`` / ``from_dict``).
That JSON is the native ``.vbx`` file format *and* the undo snapshot format.

Coordinates: document units are pixels (72 px = 1 inch for PDF export). Every item carries a
``QTransform`` mapping its local coordinates to document coordinates.
"""
from __future__ import annotations

import base64
import copy
import io
import itertools
import math
import uuid

from PySide6.QtCore import QBuffer, QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath,
                           QPainterPathStroker, QPen, QRadialGradient, QTransform)

from .geometry import polygon_points, smooth_handles

BLEND_MODES = ["Normal", "Multiply", "Screen", "Overlay", "Darken", "Lighten", "Color Dodge", "Color Burn",
               "Hard Light", "Soft Light", "Difference", "Exclusion"]
_QT_BLEND = {
    "Normal": QPainter.CompositionMode.CompositionMode_SourceOver,
    "Multiply": QPainter.CompositionMode.CompositionMode_Multiply,
    "Screen": QPainter.CompositionMode.CompositionMode_Screen,
    "Overlay": QPainter.CompositionMode.CompositionMode_Overlay,
    "Darken": QPainter.CompositionMode.CompositionMode_Darken,
    "Lighten": QPainter.CompositionMode.CompositionMode_Lighten,
    "Color Dodge": QPainter.CompositionMode.CompositionMode_ColorDodge,
    "Color Burn": QPainter.CompositionMode.CompositionMode_ColorBurn,
    "Hard Light": QPainter.CompositionMode.CompositionMode_HardLight,
    "Soft Light": QPainter.CompositionMode.CompositionMode_SoftLight,
    "Difference": QPainter.CompositionMode.CompositionMode_Difference,
    "Exclusion": QPainter.CompositionMode.CompositionMode_Exclusion,
}
CAPS = {"Butt": Qt.PenCapStyle.FlatCap, "Round": Qt.PenCapStyle.RoundCap, "Square": Qt.PenCapStyle.SquareCap}
JOINS = {"Miter": Qt.PenJoinStyle.MiterJoin, "Round": Qt.PenJoinStyle.RoundJoin, "Bevel": Qt.PenJoinStyle.BevelJoin}


def new_id() -> str:
    return uuid.uuid4().hex[:10]


# ---------------------------------------------------------------------------
# value helpers
# ---------------------------------------------------------------------------
def pt_to(p: QPointF):
    return [round(p.x(), 4), round(p.y(), 4)]


def pt_from(v) -> QPointF:
    return QPointF(float(v[0]), float(v[1]))


def color_to(c: QColor) -> str:
    return c.name(QColor.NameFormat.HexArgb)


def color_from(s) -> QColor:
    return QColor(s)


def xf_to(t: QTransform):
    return [t.m11(), t.m12(), t.m21(), t.m22(), t.dx(), t.dy()]


def xf_from(v) -> QTransform:
    return QTransform(*[float(x) for x in v])


# ---------------------------------------------------------------------------
# Paint & Style
# ---------------------------------------------------------------------------
class Paint:
    """None / solid colour / linear / radial gradient. Gradient geometry is in bbox-relative units."""

    def __init__(self, kind="solid", color=QColor(0, 0, 0), stops=None, start=(0.0, 0.5), end=(1.0, 0.5)):
        self.kind = kind
        self.color = QColor(color)
        self.stops = stops or [(0.0, QColor(0, 0, 0)), (1.0, QColor(255, 255, 255))]
        self.start = tuple(start)
        self.end = tuple(end)

    @classmethod
    def none(cls):
        return cls("none")

    @classmethod
    def solid(cls, color):
        return cls("solid", QColor(color))

    def is_none(self):
        return self.kind == "none"

    def copy(self):
        return Paint(self.kind, QColor(self.color), [(t, QColor(c)) for t, c in self.stops], self.start, self.end)

    def brush(self, bbox: QRectF) -> QBrush:
        if self.kind == "none":
            return QBrush(Qt.BrushStyle.NoBrush)
        if self.kind == "solid":
            return QBrush(self.color)
        p0 = QPointF(bbox.left() + self.start[0] * bbox.width(), bbox.top() + self.start[1] * bbox.height())
        p1 = QPointF(bbox.left() + self.end[0] * bbox.width(), bbox.top() + self.end[1] * bbox.height())
        if self.kind == "radial":
            r = math.hypot(p1.x() - p0.x(), p1.y() - p0.y()) or 1.0
            g = QRadialGradient(p0, r)
        else:
            g = QLinearGradient(p0, p1)
        for t, c in self.stops:
            g.setColorAt(float(t), c)
        return QBrush(g)

    def to_dict(self):
        return {"kind": self.kind, "color": color_to(self.color),
                "stops": [[t, color_to(c)] for t, c in self.stops], "start": list(self.start), "end": list(self.end)}

    @classmethod
    def from_dict(cls, d):
        if d is None:
            return cls.none()
        return cls(d.get("kind", "solid"), color_from(d.get("color", "#ff000000")),
                   [(float(t), color_from(c)) for t, c in d.get("stops", [])] or None,
                   tuple(d.get("start", (0, 0.5))), tuple(d.get("end", (1, 0.5))))


class Style:
    def __init__(self):
        self.fill = Paint.solid(QColor(200, 200, 200))
        self.stroke = Paint.solid(QColor(0, 0, 0))
        self.stroke_width = 1.0
        self.cap = "Butt"
        self.join = "Miter"
        self.dash: list[float] = []
        self.opacity = 1.0
        self.blend = "Normal"

    def copy(self):
        s = Style()
        s.fill, s.stroke = self.fill.copy(), self.stroke.copy()
        s.stroke_width, s.cap, s.join = self.stroke_width, self.cap, self.join
        s.dash, s.opacity, s.blend = list(self.dash), self.opacity, self.blend
        return s

    def pen(self) -> QPen:
        if self.stroke.is_none() or self.stroke_width <= 0:
            return QPen(Qt.PenStyle.NoPen)
        pen = QPen(self.stroke.color if self.stroke.kind == "solid" else QColor(0, 0, 0), self.stroke_width)
        pen.setCapStyle(CAPS.get(self.cap, Qt.PenCapStyle.FlatCap))
        pen.setJoinStyle(JOINS.get(self.join, Qt.PenJoinStyle.MiterJoin))
        if self.dash:
            pen.setDashPattern([max(0.01, d / max(self.stroke_width, 0.01)) for d in self.dash])
        return pen

    def to_dict(self):
        return {"fill": self.fill.to_dict(), "stroke": self.stroke.to_dict(), "stroke_width": self.stroke_width,
                "cap": self.cap, "join": self.join, "dash": self.dash, "opacity": self.opacity, "blend": self.blend}

    @classmethod
    def from_dict(cls, d):
        s = cls()
        if not d:
            return s
        s.fill = Paint.from_dict(d.get("fill")); s.stroke = Paint.from_dict(d.get("stroke"))
        s.stroke_width = float(d.get("stroke_width", 1.0)); s.cap = d.get("cap", "Butt"); s.join = d.get("join", "Miter")
        s.dash = [float(x) for x in d.get("dash", [])]; s.opacity = float(d.get("opacity", 1.0)); s.blend = d.get("blend", "Normal")
        return s


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------
class Node:
    """A path anchor with absolute (local-space) in/out handles."""
    __slots__ = ("p", "h_in", "h_out", "smooth")

    def __init__(self, p: QPointF, h_in: QPointF | None = None, h_out: QPointF | None = None, smooth=False):
        self.p = QPointF(p)
        self.h_in = QPointF(h_in) if h_in is not None else QPointF(p)
        self.h_out = QPointF(h_out) if h_out is not None else QPointF(p)
        self.smooth = smooth

    def copy(self):
        return Node(self.p, self.h_in, self.h_out, self.smooth)

    def has_in(self):
        return self.h_in != self.p

    def has_out(self):
        return self.h_out != self.p

    def to_dict(self):
        return {"p": pt_to(self.p), "i": pt_to(self.h_in), "o": pt_to(self.h_out), "s": self.smooth}

    @classmethod
    def from_dict(cls, d):
        return cls(pt_from(d["p"]), pt_from(d.get("i", d["p"])), pt_from(d.get("o", d["p"])), bool(d.get("s", False)))


class Subpath:
    def __init__(self, nodes: list[Node] | None = None, closed=False):
        self.nodes = nodes or []
        self.closed = closed

    def copy(self):
        return Subpath([n.copy() for n in self.nodes], self.closed)

    def to_dict(self):
        return {"nodes": [n.to_dict() for n in self.nodes], "closed": self.closed}

    @classmethod
    def from_dict(cls, d):
        return cls([Node.from_dict(n) for n in d.get("nodes", [])], bool(d.get("closed", False)))


class Item:
    kind = "item"

    def __init__(self):
        self.id = new_id()
        self.name = ""
        self.visible = True
        self.locked = False
        self.style = Style()
        self.transform = QTransform()

    # geometry ----------------------------------------------------------
    def local_path(self) -> QPainterPath:
        raise NotImplementedError

    def doc_path(self) -> QPainterPath:
        return self.transform.map(self.local_path())

    def local_bbox(self) -> QRectF:
        return self.local_path().boundingRect()

    def bbox(self) -> QRectF:
        """Geometric bounds in document space (no stroke)."""
        return self.doc_path().boundingRect()

    def visual_bbox(self) -> QRectF:
        r = self.bbox()
        if not self.style.stroke.is_none():
            w = self.style.stroke_width * max(abs(self.transform.m11()), abs(self.transform.m22()), 1e-6)
            r = r.adjusted(-w / 2, -w / 2, w / 2, w / 2)
        return r

    def hit(self, pt: QPointF, tol: float) -> bool:
        p = self.doc_path()
        if not self.style.fill.is_none() and p.contains(pt):
            return True
        st = QPainterPathStroker()
        st.setWidth(max(self.style.stroke_width if not self.style.stroke.is_none() else 0.0, tol * 2))
        return st.createStroke(p).contains(pt)

    # painting ----------------------------------------------------------
    def paint(self, painter: QPainter, outline=False):
        if not self.visible:
            return
        painter.save()
        painter.setTransform(self.transform, True)
        path = self.local_path()
        if outline:
            painter.setPen(QPen(QColor(60, 120, 255), 0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        else:
            painter.setOpacity(painter.opacity() * self.style.opacity)
            painter.setCompositionMode(_QT_BLEND.get(self.style.blend, _QT_BLEND["Normal"]))
            bb = path.boundingRect()
            painter.setBrush(self.style.fill.brush(bb))
            pen = self.style.pen()
            if self.style.stroke.kind in ("linear", "radial") and not self.style.stroke.is_none():
                pen.setBrush(self.style.stroke.brush(bb))
            painter.setPen(pen)
            painter.drawPath(path)
        painter.restore()

    # serialisation -----------------------------------------------------
    def base_dict(self):
        return {"kind": self.kind, "id": self.id, "name": self.name, "visible": self.visible, "locked": self.locked,
                "style": self.style.to_dict(), "transform": xf_to(self.transform)}

    def load_base(self, d):
        self.id = d.get("id", self.id); self.name = d.get("name", ""); self.visible = bool(d.get("visible", True))
        self.locked = bool(d.get("locked", False)); self.style = Style.from_dict(d.get("style"))
        self.transform = xf_from(d.get("transform", [1, 0, 0, 1, 0, 0]))

    def to_dict(self):
        return self.base_dict()

    def copy(self):
        return item_from_dict(self.to_dict(), new_ids=True)

    def display_name(self):
        return self.name or self.kind.capitalize()

    def to_path_item(self) -> "PathItem":
        """Convert to an editable PathItem (bakes nothing: keeps transform)."""
        p = PathItem.from_painter_path(self.local_path())
        p.style, p.transform, p.name = self.style.copy(), QTransform(self.transform), self.name
        return p


class PathItem(Item):
    kind = "path"

    def __init__(self, subpaths: list[Subpath] | None = None):
        super().__init__()
        self.subpaths = subpaths or []

    @classmethod
    def from_points(cls, pts: list[QPointF], closed=False, smooth=False):
        sp = Subpath([Node(p) for p in pts], closed)
        if smooth and len(pts) > 1:
            for n, (hi, ho) in zip(sp.nodes, smooth_handles(pts, closed)):
                n.h_in, n.h_out, n.smooth = hi, ho, True
        return cls([sp])

    @classmethod
    def from_painter_path(cls, path: QPainterPath):
        subs: list[Subpath] = []
        cur: Subpath | None = None
        i = 0
        n = path.elementCount()
        while i < n:
            e = path.elementAt(i)
            if e.isMoveTo():
                cur = Subpath([Node(QPointF(e.x, e.y))])
                subs.append(cur)
                i += 1
            elif e.isLineTo():
                if cur is None:
                    cur = Subpath([]); subs.append(cur)
                cur.nodes.append(Node(QPointF(e.x, e.y)))
                i += 1
            elif e.isCurveTo():
                c1 = QPointF(e.x, e.y)
                c2 = path.elementAt(i + 1); end = path.elementAt(i + 2)
                c2p, endp = QPointF(c2.x, c2.y), QPointF(end.x, end.y)
                if cur is None:
                    cur = Subpath([Node(c1)]); subs.append(cur)
                cur.nodes[-1].h_out = c1
                cur.nodes.append(Node(endp, c2p, endp))
                i += 3
            else:
                i += 1
        for sp in subs:
            if len(sp.nodes) > 1 and sp.nodes[0].p == sp.nodes[-1].p and (sp.nodes[-1].p == sp.nodes[-1].h_out):
                # closed path: fold the duplicated last anchor back into the first node
                last = sp.nodes.pop()
                sp.nodes[0].h_in = last.h_in
                sp.closed = True
            for nd in sp.nodes:
                if nd.has_in() and nd.has_out():
                    a = QPointF(nd.p.x() - nd.h_in.x(), nd.p.y() - nd.h_in.y())
                    b = QPointF(nd.h_out.x() - nd.p.x(), nd.h_out.y() - nd.p.y())
                    cross = a.x() * b.y() - a.y() * b.x()
                    dot = a.x() * b.x() + a.y() * b.y()
                    nd.smooth = abs(cross) < 1e-3 * (abs(dot) + 1) and dot > 0
        return cls(subs)

    def local_path(self) -> QPainterPath:
        path = QPainterPath()
        for sp in self.subpaths:
            if not sp.nodes:
                continue
            path.moveTo(sp.nodes[0].p)
            prev = sp.nodes[0]
            for nd in sp.nodes[1:]:
                if prev.has_out() or nd.has_in():
                    path.cubicTo(prev.h_out, nd.h_in, nd.p)
                else:
                    path.lineTo(nd.p)
                prev = nd
            if sp.closed and len(sp.nodes) > 1:
                first = sp.nodes[0]
                if prev.has_out() or first.has_in():
                    path.cubicTo(prev.h_out, first.h_in, first.p)
                path.closeSubpath()
        return path

    def all_nodes(self):
        for si, sp in enumerate(self.subpaths):
            for ni, nd in enumerate(sp.nodes):
                yield si, ni, nd

    def is_open(self):
        return any(not sp.closed for sp in self.subpaths)

    def to_dict(self):
        d = self.base_dict()
        d["subpaths"] = [sp.to_dict() for sp in self.subpaths]
        return d

    def load(self, d):
        self.load_base(d)
        self.subpaths = [Subpath.from_dict(s) for s in d.get("subpaths", [])]
        return self

    def to_path_item(self):
        return self.copy()


class RectItem(Item):
    kind = "rect"

    def __init__(self, rect=QRectF(0, 0, 100, 100), radius=0.0):
        super().__init__()
        self.rect = QRectF(rect)
        self.radius = float(radius)

    def local_path(self):
        p = QPainterPath()
        if self.radius > 0:
            p.addRoundedRect(self.rect, self.radius, self.radius)
        else:
            p.addRect(self.rect)
        return p

    def to_dict(self):
        d = self.base_dict(); d["rect"] = [self.rect.x(), self.rect.y(), self.rect.width(), self.rect.height()]; d["radius"] = self.radius
        return d

    def load(self, d):
        self.load_base(d); self.rect = QRectF(*d["rect"]); self.radius = float(d.get("radius", 0)); return self


class EllipseItem(Item):
    kind = "ellipse"

    def __init__(self, rect=QRectF(0, 0, 100, 100)):
        super().__init__()
        self.rect = QRectF(rect)

    def local_path(self):
        p = QPainterPath(); p.addEllipse(self.rect); return p

    def to_dict(self):
        d = self.base_dict(); d["rect"] = [self.rect.x(), self.rect.y(), self.rect.width(), self.rect.height()]; return d

    def load(self, d):
        self.load_base(d); self.rect = QRectF(*d["rect"]); return self


class PolygonItem(Item):
    kind = "polygon"

    def __init__(self, center=QPointF(0, 0), radius=50.0, sides=6, star=False, inner_ratio=0.5):
        super().__init__()
        self.center, self.radius, self.sides, self.star, self.inner_ratio = QPointF(center), float(radius), int(sides), bool(star), float(inner_ratio)

    def local_path(self):
        pts = polygon_points(self.center.x(), self.center.y(), self.radius, self.sides,
                             inner=self.radius * self.inner_ratio if self.star else None)
        p = QPainterPath(pts[0])
        for q in pts[1:]:
            p.lineTo(q)
        p.closeSubpath()
        return p

    def to_dict(self):
        d = self.base_dict(); d.update(center=pt_to(self.center), radius=self.radius, sides=self.sides, star=self.star, inner_ratio=self.inner_ratio); return d

    def load(self, d):
        self.load_base(d); self.center = pt_from(d["center"]); self.radius = float(d["radius"]); self.sides = int(d["sides"])
        self.star = bool(d.get("star", False)); self.inner_ratio = float(d.get("inner_ratio", 0.5)); return self


class TextItem(Item):
    kind = "text"

    def __init__(self, text="Text", pos=QPointF(0, 0), family="Arial", size=24.0):
        super().__init__()
        self.text, self.pos, self.family, self.size = text, QPointF(pos), family, float(size)
        self.bold = False; self.italic = False; self.align = "Left"; self.line_height = 1.2; self.letter_spacing = 0.0
        self.style.fill = Paint.solid(QColor(0, 0, 0)); self.style.stroke = Paint.none()

    def font(self) -> QFont:
        f = QFont(self.family)
        f.setPixelSize(max(1, int(round(self.size))))
        f.setBold(self.bold); f.setItalic(self.italic)
        if self.letter_spacing:
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, self.letter_spacing)
        return f

    def local_path(self):
        f = self.font()
        fm = QFontMetricsF(f)
        path = QPainterPath()
        lines = self.text.split("\n") or [""]
        widths = [fm.horizontalAdvance(l) for l in lines]
        for i, line in enumerate(lines):
            y = self.pos.y() + i * self.size * self.line_height
            w = widths[i]
            x = self.pos.x()
            if self.align == "Center":
                x -= w / 2
            elif self.align == "Right":
                x -= w
            if line:
                path.addText(QPointF(x, y), f, line)
        if path.isEmpty():  # keep an empty text selectable
            path.addRect(QRectF(self.pos.x(), self.pos.y() - self.size, max(4.0, self.size / 2), self.size))
        return path

    def to_dict(self):
        d = self.base_dict()
        d.update(text=self.text, pos=pt_to(self.pos), family=self.family, size=self.size, bold=self.bold, italic=self.italic,
                 align=self.align, line_height=self.line_height, letter_spacing=self.letter_spacing)
        return d

    def load(self, d):
        self.load_base(d); self.text = d.get("text", ""); self.pos = pt_from(d["pos"]); self.family = d.get("family", "Arial")
        self.size = float(d.get("size", 24)); self.bold = bool(d.get("bold", False)); self.italic = bool(d.get("italic", False))
        self.align = d.get("align", "Left"); self.line_height = float(d.get("line_height", 1.2)); self.letter_spacing = float(d.get("letter_spacing", 0))
        return self

    def display_name(self):
        return self.name or (self.text.strip().splitlines()[0][:20] if self.text.strip() else "Text")


class ImageItem(Item):
    kind = "image"

    def __init__(self, image: QImage | None = None, rect: QRectF | None = None):
        super().__init__()
        self.image = image if image is not None else QImage(1, 1, QImage.Format.Format_ARGB32)
        self.rect = QRectF(rect) if rect is not None else QRectF(0, 0, self.image.width(), self.image.height())
        self.style.fill = Paint.none(); self.style.stroke = Paint.none()

    def local_path(self):
        p = QPainterPath(); p.addRect(self.rect); return p

    def paint(self, painter, outline=False):
        if not self.visible:
            return
        painter.save()
        painter.setTransform(self.transform, True)
        if outline:
            painter.setPen(QPen(QColor(60, 120, 255), 0)); painter.setBrush(Qt.BrushStyle.NoBrush); painter.drawRect(self.rect)
            painter.drawLine(self.rect.topLeft(), self.rect.bottomRight()); painter.drawLine(self.rect.topRight(), self.rect.bottomLeft())
        else:
            painter.setOpacity(painter.opacity() * self.style.opacity)
            painter.setCompositionMode(_QT_BLEND.get(self.style.blend, _QT_BLEND["Normal"]))
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawImage(self.rect, self.image)
            if not self.style.stroke.is_none():
                painter.setBrush(Qt.BrushStyle.NoBrush); painter.setPen(self.style.pen()); painter.drawRect(self.rect)
        painter.restore()

    def to_dict(self):
        d = self.base_dict()
        buf = QBuffer(); buf.open(QBuffer.OpenModeFlag.WriteOnly); self.image.save(buf, "PNG")
        d["png"] = base64.b64encode(bytes(buf.data())).decode("ascii")
        d["rect"] = [self.rect.x(), self.rect.y(), self.rect.width(), self.rect.height()]
        return d

    def load(self, d):
        self.load_base(d)
        img = QImage(); img.loadFromData(QByteArray(base64.b64decode(d["png"])), "PNG"); self.image = img
        self.rect = QRectF(*d["rect"]); return self


class GroupItem(Item):
    kind = "group"

    def __init__(self, children: list[Item] | None = None):
        super().__init__()
        self.children = children or []

    def local_path(self):
        p = QPainterPath()
        for c in self.children:
            p.addPath(c.doc_path())
        return p

    def hit(self, pt, tol):
        inv, ok = self.transform.inverted()
        lp = inv.map(pt) if ok else pt
        return any(c.visible and c.hit(lp, tol) for c in self.children)

    def paint(self, painter, outline=False):
        if not self.visible:
            return
        painter.save()
        painter.setTransform(self.transform, True)
        painter.setOpacity(painter.opacity() * self.style.opacity)
        for c in self.children:
            c.paint(painter, outline)
        painter.restore()

    def to_dict(self):
        d = self.base_dict(); d["children"] = [c.to_dict() for c in self.children]; return d

    def load(self, d):
        self.load_base(d); self.children = [item_from_dict(c) for c in d.get("children", [])]; return self

    def walk(self):
        for c in self.children:
            yield c
            if isinstance(c, GroupItem):
                yield from c.walk()


ITEM_TYPES = {"path": PathItem, "rect": RectItem, "ellipse": EllipseItem, "polygon": PolygonItem,
              "text": TextItem, "image": ImageItem, "group": GroupItem}


def item_from_dict(d, new_ids=False) -> Item:
    cls = ITEM_TYPES.get(d.get("kind"), PathItem)
    it = cls.__new__(cls)
    Item.__init__(it)
    if cls is GroupItem:
        it.children = []
    if cls is PathItem:
        it.subpaths = []
    it.load(d)
    if new_ids:
        it.id = new_id()
        if isinstance(it, GroupItem):
            for c in it.walk():
                c.id = new_id()
    return it


# ---------------------------------------------------------------------------
# Layers & Document
# ---------------------------------------------------------------------------
LAYER_COLORS = ["#4a90e2", "#e24a4a", "#4ae26b", "#e2c24a", "#b04ae2", "#4ae2d8", "#e28b4a"]


class Layer:
    def __init__(self, name="Layer 1", color=None):
        self.id = new_id()
        self.name = name
        self.visible = True
        self.locked = False
        self.color = QColor(color or LAYER_COLORS[0])
        self.items: list[Item] = []   # bottom-to-top

    def to_dict(self):
        return {"id": self.id, "name": self.name, "visible": self.visible, "locked": self.locked,
                "color": color_to(self.color), "items": [i.to_dict() for i in self.items]}

    @classmethod
    def from_dict(cls, d):
        l = cls(d.get("name", "Layer"), d.get("color"))
        l.id = d.get("id", l.id); l.visible = bool(d.get("visible", True)); l.locked = bool(d.get("locked", False))
        l.items = [item_from_dict(i) for i in d.get("items", [])]
        return l


class Guide:
    def __init__(self, horizontal: bool, pos: float):
        self.horizontal, self.pos = horizontal, float(pos)


class Document:
    _untitled = 0

    def __init__(self, width=1920.0, height=1080.0, name=None):
        self.width, self.height = float(width), float(height)
        self.layers: list[Layer] = [Layer("Layer 1")]
        self.active_layer_index = 0
        self.guides: list[Guide] = []
        self.grid_size = 20.0
        self.path: str | None = None
        self.dirty = False
        self.version = 0
        self.listeners: list = []
        if name is None:
            Document._untitled += 1
            name = f"Untitled-{Document._untitled}"
        self._name = name

    @property
    def name(self):
        import os
        return os.path.basename(self.path) if self.path else self._name

    @property
    def rect(self):
        return QRectF(0, 0, self.width, self.height)

    def changed(self, structure=False):
        self.version += 1
        self.dirty = True
        for cb in list(self.listeners):
            cb(structure)

    # layers / items ------------------------------------------------------
    @property
    def active_layer(self) -> Layer:
        self.active_layer_index = max(0, min(self.active_layer_index, len(self.layers) - 1))
        return self.layers[self.active_layer_index]

    def add_layer(self, name=None) -> Layer:
        name = name or f"Layer {len(self.layers) + 1}"
        l = Layer(name, LAYER_COLORS[len(self.layers) % len(LAYER_COLORS)])
        self.layers.insert(self.active_layer_index + 1, l)
        self.active_layer_index += 1
        return l

    def all_items(self, include_hidden=False, top_level_only=True):
        """Items in paint order (bottom to top)."""
        for layer in self.layers:
            if not layer.visible and not include_hidden:
                continue
            for it in layer.items:
                if it.visible or include_hidden:
                    yield it
                    if not top_level_only and isinstance(it, GroupItem):
                        yield from it.walk()

    def find(self, item_id):
        for layer in self.layers:
            for it in layer.items:
                if it.id == item_id:
                    return it
        return None

    def layer_of(self, item) -> Layer | None:
        for layer in self.layers:
            if item in layer.items:
                return layer
        return None

    def add_item(self, item: Item, layer: Layer | None = None, index: int | None = None):
        layer = layer or self.active_layer
        if index is None:
            layer.items.append(item)
        else:
            layer.items.insert(index, item)
        return item

    def remove_item(self, item: Item):
        l = self.layer_of(item)
        if l:
            l.items.remove(item)

    def item_at(self, pt: QPointF, tol: float, ignore_locked=True) -> Item | None:
        for layer in reversed(self.layers):
            if not layer.visible or (layer.locked and ignore_locked):
                continue
            for it in reversed(layer.items):
                if not it.visible or (it.locked and ignore_locked):
                    continue
                if it.hit(pt, tol):
                    return it
        return None

    def items_in_rect(self, r: QRectF, ignore_locked=True):
        out = []
        for layer in self.layers:
            if not layer.visible or (layer.locked and ignore_locked):
                continue
            for it in layer.items:
                if not it.visible or (it.locked and ignore_locked):
                    continue
                if r.intersects(it.bbox()):
                    out.append(it)
        return out

    def z_order(self, item: Item) -> tuple[int, int]:
        for li, layer in enumerate(self.layers):
            if item in layer.items:
                return li, layer.items.index(item)
        return -1, -1

    # painting ------------------------------------------------------------
    def paint(self, painter: QPainter, outline=False):
        for layer in self.layers:
            if not layer.visible:
                continue
            for it in layer.items:
                it.paint(painter, outline)

    def render_image(self, scale=1.0, background: QColor | None = QColor(255, 255, 255)) -> QImage:
        w, h = max(1, int(round(self.width * scale))), max(1, int(round(self.height * scale)))
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(background if background is not None else Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.scale(scale, scale)
        self.paint(p)
        p.end()
        return img

    # serialisation -------------------------------------------------------
    def to_dict(self):
        return {"app": "VectorBench", "format": 1, "width": self.width, "height": self.height,
                "active_layer": self.active_layer_index, "grid": self.grid_size,
                "guides": [[g.horizontal, g.pos] for g in self.guides],
                "layers": [l.to_dict() for l in self.layers]}

    @classmethod
    def from_dict(cls, d, doc: "Document | None" = None) -> "Document":
        doc = doc or cls(d.get("width", 1920), d.get("height", 1080))
        doc.width, doc.height = float(d.get("width", 1920)), float(d.get("height", 1080))
        doc.layers = [Layer.from_dict(l) for l in d.get("layers", [])] or [Layer("Layer 1")]
        doc.active_layer_index = int(d.get("active_layer", 0))
        doc.grid_size = float(d.get("grid", 20))
        doc.guides = [Guide(bool(h), float(p)) for h, p in d.get("guides", [])]
        return doc

    def snapshot(self):
        return self.to_dict()

    def restore(self, snap):
        Document.from_dict(snap, self)
