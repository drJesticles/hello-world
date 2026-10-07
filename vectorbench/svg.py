"""SVG import and export (the lingua franca with Illustrator, Inkscape, Figma, browsers)."""
from __future__ import annotations

import base64
import math
import re
import xml.etree.ElementTree as ET

from PySide6.QtCore import QByteArray, QPointF, QRectF
from PySide6.QtGui import QColor, QImage, QPainterPath, QTransform

from .geometry import arc_to_cubics
from .model import (Document, EllipseItem, GroupItem, ImageItem, Item, Layer, Node, Paint, PathItem, PolygonItem,
                    RectItem, Style, Subpath, TextItem)

SVG_NS = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"


def _tag(el):
    return el.tag.split("}")[-1]


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------
def _fmt(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def _color_attr(c: QColor) -> str:
    return c.name()


def path_d(item: PathItem) -> str:
    parts = []
    for sp in item.subpaths:
        if not sp.nodes:
            continue
        n0 = sp.nodes[0]
        parts.append(f"M{_fmt(n0.p.x())} {_fmt(n0.p.y())}")
        prev = n0
        for nd in sp.nodes[1:]:
            if prev.has_out() or nd.has_in():
                parts.append(f"C{_fmt(prev.h_out.x())} {_fmt(prev.h_out.y())} {_fmt(nd.h_in.x())} {_fmt(nd.h_in.y())} {_fmt(nd.p.x())} {_fmt(nd.p.y())}")
            else:
                parts.append(f"L{_fmt(nd.p.x())} {_fmt(nd.p.y())}")
            prev = nd
        if sp.closed and len(sp.nodes) > 1:
            if prev.has_out() or n0.has_in():
                parts.append(f"C{_fmt(prev.h_out.x())} {_fmt(prev.h_out.y())} {_fmt(n0.h_in.x())} {_fmt(n0.h_in.y())} {_fmt(n0.p.x())} {_fmt(n0.p.y())}")
            parts.append("Z")
    return " ".join(parts)


class _Exporter:
    def __init__(self):
        self.defs: list[str] = []
        self.n = 0

    def paint_attr(self, paint: Paint, bbox: QRectF, which: str) -> tuple[str, str]:
        if paint.is_none():
            return f'{which}="none"', ""
        if paint.kind == "solid":
            op = f' {which}-opacity="{_fmt(paint.color.alphaF())}"' if paint.color.alpha() < 255 else ""
            return f'{which}="{_color_attr(paint.color)}"', op
        self.n += 1
        gid = f"g{self.n}"
        stops = "".join(f'<stop offset="{_fmt(t)}" stop-color="{_color_attr(c)}" stop-opacity="{_fmt(c.alphaF())}"/>' for t, c in paint.stops)
        x1, y1 = bbox.left() + paint.start[0] * bbox.width(), bbox.top() + paint.start[1] * bbox.height()
        x2, y2 = bbox.left() + paint.end[0] * bbox.width(), bbox.top() + paint.end[1] * bbox.height()
        if paint.kind == "radial":
            r = math.hypot(x2 - x1, y2 - y1) or 1
            self.defs.append(f'<radialGradient id="{gid}" gradientUnits="userSpaceOnUse" cx="{_fmt(x1)}" cy="{_fmt(y1)}" r="{_fmt(r)}">{stops}</radialGradient>')
        else:
            self.defs.append(f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="{_fmt(x1)}" y1="{_fmt(y1)}" x2="{_fmt(x2)}" y2="{_fmt(y2)}">{stops}</linearGradient>')
        return f'{which}="url(#{gid})"', ""

    def style_attrs(self, st: Style, bbox: QRectF) -> str:
        f, fo = self.paint_attr(st.fill, bbox, "fill")
        s, so = self.paint_attr(st.stroke, bbox, "stroke")
        out = [f + fo, s + so]
        if not st.stroke.is_none():
            out.append(f'stroke-width="{_fmt(st.stroke_width)}"')
            out.append(f'stroke-linecap="{ {"Butt": "butt", "Round": "round", "Square": "square"}[st.cap] }"')
            out.append(f'stroke-linejoin="{ {"Miter": "miter", "Round": "round", "Bevel": "bevel"}[st.join] }"')
            if st.dash:
                out.append(f'stroke-dasharray="{" ".join(_fmt(d) for d in st.dash)}"')
        if st.opacity < 1:
            out.append(f'opacity="{_fmt(st.opacity)}"')
        if st.blend != "Normal":
            out.append(f'style="mix-blend-mode:{st.blend.lower().replace(" ", "-")}"')
        return " ".join(out)

    def xf_attr(self, t: QTransform) -> str:
        if t.isIdentity():
            return ""
        return f' transform="matrix({_fmt(t.m11())} {_fmt(t.m12())} {_fmt(t.m21())} {_fmt(t.m22())} {_fmt(t.dx())} {_fmt(t.dy())})"'

    def item(self, it: Item) -> str:
        if not it.visible:
            return ""
        xf = self.xf_attr(it.transform)
        idattr = f' id="{it.id}"'
        if isinstance(it, GroupItem):
            inner = "".join(self.item(c) for c in it.children)
            op = f' opacity="{_fmt(it.style.opacity)}"' if it.style.opacity < 1 else ""
            return f"<g{idattr}{xf}{op}>{inner}</g>"
        bbox = it.local_bbox()
        if isinstance(it, ImageItem):
            ba = QByteArray(); 
            from PySide6.QtCore import QBuffer
            buf = QBuffer(ba); buf.open(QBuffer.OpenModeFlag.WriteOnly); it.image.save(buf, "PNG")
            data = base64.b64encode(bytes(ba)).decode("ascii")
            r = it.rect
            op = f' opacity="{_fmt(it.style.opacity)}"' if it.style.opacity < 1 else ""
            return f'<image{idattr}{xf}{op} x="{_fmt(r.x())}" y="{_fmt(r.y())}" width="{_fmt(r.width())}" height="{_fmt(r.height())}" href="data:image/png;base64,{data}"/>'
        attrs = self.style_attrs(it.style, bbox)
        if isinstance(it, RectItem):
            r = it.rect
            rad = f' rx="{_fmt(it.radius)}"' if it.radius > 0 else ""
            return f'<rect{idattr}{xf} x="{_fmt(r.x())}" y="{_fmt(r.y())}" width="{_fmt(r.width())}" height="{_fmt(r.height())}"{rad} {attrs}/>'
        if isinstance(it, EllipseItem):
            r = it.rect
            return f'<ellipse{idattr}{xf} cx="{_fmt(r.center().x())}" cy="{_fmt(r.center().y())}" rx="{_fmt(r.width()/2)}" ry="{_fmt(r.height()/2)}" {attrs}/>'
        if isinstance(it, TextItem):
            anchor = {"Left": "start", "Center": "middle", "Right": "end"}[it.align]
            weight = ' font-weight="bold"' if it.bold else ""
            style = ' font-style="italic"' if it.italic else ""
            ls = f' letter-spacing="{_fmt(it.letter_spacing)}"' if it.letter_spacing else ""
            lines = it.text.split("\n")
            tspans = "".join(
                f'<tspan x="{_fmt(it.pos.x())}" y="{_fmt(it.pos.y() + i * it.size * it.line_height)}">{_esc(l)}</tspan>' for i, l in enumerate(lines))
            return (f'<text{idattr}{xf} font-family="{_esc(it.family)}" font-size="{_fmt(it.size)}" text-anchor="{anchor}"{weight}{style}{ls} '
                    f'{attrs} data-vb-text="1">{tspans}</text>')
        if isinstance(it, PathItem):
            return f'<path{idattr}{xf} d="{path_d(it)}" {attrs}/>'
        # polygon and anything else: emit as path
        p = PathItem.from_painter_path(it.local_path())
        return f'<path{idattr}{xf} d="{path_d(p)}" {attrs}/>'


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def export_svg(doc: Document, items: list[Item] | None = None) -> str:
    ex = _Exporter()
    body = []
    if items is None:
        for layer in doc.layers:
            if not layer.visible:
                continue
            body.append(f'<g id="{layer.id}" data-vb-layer="{_esc(layer.name)}">' + "".join(ex.item(i) for i in layer.items) + "</g>")
    else:
        body.append("".join(ex.item(i) for i in items))
    defs = f"<defs>{''.join(ex.defs)}</defs>" if ex.defs else ""
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="{SVG_NS}" xmlns:xlink="{XLINK}" width="{_fmt(doc.width)}" '
            f'height="{_fmt(doc.height)}" viewBox="0 0 {_fmt(doc.width)} {_fmt(doc.height)}">{defs}{"".join(body)}</svg>')


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------
_NUM = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")


def _nums(s: str) -> list[float]:
    return [float(x) for x in _NUM.findall(s or "")]


def parse_transform(s: str | None) -> QTransform:
    t = QTransform()
    if not s:
        return t
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", s):
        a = _nums(args)
        m = QTransform()
        if name == "matrix" and len(a) == 6:
            m = QTransform(a[0], a[1], a[2], a[3], a[4], a[5])
        elif name == "translate":
            m = QTransform.fromTranslate(a[0], a[1] if len(a) > 1 else 0)
        elif name == "scale":
            m = QTransform.fromScale(a[0], a[1] if len(a) > 1 else a[0])
        elif name == "rotate":
            if len(a) >= 3:
                m = QTransform.fromTranslate(a[1], a[2]).rotate(a[0]).translate(-a[1], -a[2])
            else:
                m.rotate(a[0])
        elif name == "skewX":
            m.shear(math.tan(math.radians(a[0])), 0)
        elif name == "skewY":
            m.shear(0, math.tan(math.radians(a[0])))
        t = m * t  # SVG applies left-to-right as nested: first listed is outermost
    return t


def parse_path_d(d: str) -> QPainterPath:
    path = QPainterPath()
    tokens = re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?", d or "")
    i = 0
    cmd = None
    cur = QPointF(0, 0); start = QPointF(0, 0); last_c = None; last_q = None

    def take(n):
        nonlocal i
        vals = [float(x) for x in tokens[i:i + n]]
        i += n
        return vals

    while i < len(tokens):
        tok = tokens[i]
        if re.match(r"[A-Za-z]", tok):
            cmd = tok; i += 1
            if cmd in "Zz":
                path.closeSubpath(); cur = QPointF(start); last_c = last_q = None
                continue
        if cmd is None:
            break
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            x, y = take(2)
            cur = QPointF(cur.x() + x, cur.y() + y) if rel else QPointF(x, y)
            path.moveTo(cur); start = QPointF(cur); cmd = "l" if rel else "L"; last_c = last_q = None
        elif c == "L":
            x, y = take(2)
            cur = QPointF(cur.x() + x, cur.y() + y) if rel else QPointF(x, y)
            path.lineTo(cur); last_c = last_q = None
        elif c == "H":
            (x,) = take(1)
            cur = QPointF(cur.x() + x if rel else x, cur.y()); path.lineTo(cur); last_c = last_q = None
        elif c == "V":
            (y,) = take(1)
            cur = QPointF(cur.x(), cur.y() + y if rel else y); path.lineTo(cur); last_c = last_q = None
        elif c == "C":
            x1, y1, x2, y2, x, y = take(6)
            if rel:
                x1 += cur.x(); y1 += cur.y(); x2 += cur.x(); y2 += cur.y(); x += cur.x(); y += cur.y()
            path.cubicTo(x1, y1, x2, y2, x, y); last_c = QPointF(x2, y2); cur = QPointF(x, y); last_q = None
        elif c == "S":
            x2, y2, x, y = take(4)
            if rel:
                x2 += cur.x(); y2 += cur.y(); x += cur.x(); y += cur.y()
            c1 = QPointF(2 * cur.x() - last_c.x(), 2 * cur.y() - last_c.y()) if last_c is not None else QPointF(cur)
            path.cubicTo(c1.x(), c1.y(), x2, y2, x, y); last_c = QPointF(x2, y2); cur = QPointF(x, y); last_q = None
        elif c == "Q":
            x1, y1, x, y = take(4)
            if rel:
                x1 += cur.x(); y1 += cur.y(); x += cur.x(); y += cur.y()
            path.quadTo(x1, y1, x, y); last_q = QPointF(x1, y1); cur = QPointF(x, y); last_c = None
        elif c == "T":
            x, y = take(2)
            if rel:
                x += cur.x(); y += cur.y()
            q = QPointF(2 * cur.x() - last_q.x(), 2 * cur.y() - last_q.y()) if last_q is not None else QPointF(cur)
            path.quadTo(q, QPointF(x, y)); last_q = q; cur = QPointF(x, y); last_c = None
        elif c == "A":
            rx, ry, phi, large, sweep, x, y = take(7)
            if rel:
                x += cur.x(); y += cur.y()
            for c1, c2, end in arc_to_cubics(cur.x(), cur.y(), rx, ry, phi, int(large) != 0, int(sweep) != 0, x, y):
                path.cubicTo(c1, c2, end)
            cur = QPointF(x, y); last_c = last_q = None
        else:
            i += 1
    return path


def _parse_color(s: str | None, opacity: float = 1.0) -> QColor | None:
    if s is None:
        return None
    s = s.strip()
    if s in ("none", "transparent"):
        return QColor(0, 0, 0, 0)
    if s.startswith("url("):
        return None
    c = QColor(s)
    if not c.isValid():
        m = re.match(r"rgba?\(([^)]*)\)", s)
        if m:
            v = _nums(m.group(1))
            if len(v) >= 3:
                scale = 2.55 if "%" in m.group(1) else 1.0
                c = QColor(int(v[0] * scale), int(v[1] * scale), int(v[2] * scale))
                if len(v) > 3:
                    c.setAlphaF(v[3])
        if not c.isValid():
            return QColor(0, 0, 0)
    if opacity < 1:
        c.setAlphaF(c.alphaF() * opacity)
    return c


class _Importer:
    def __init__(self):
        self.gradients: dict[str, Paint] = {}
        self.items_out: list[Item] = []

    def collect_defs(self, root):
        for el in root.iter():
            t = _tag(el)
            if t in ("linearGradient", "radialGradient"):
                gid = el.get("id")
                if not gid:
                    continue
                stops = []
                for st in el:
                    if _tag(st) != "stop":
                        continue
                    style = _style_map(st)
                    off = st.get("offset", style.get("offset", "0"))
                    o = float(off.strip("%")) / (100 if "%" in off else 1)
                    col = _parse_color(style.get("stop-color", st.get("stop-color", "#000")),
                                       float(style.get("stop-opacity", st.get("stop-opacity", 1))))
                    stops.append((o, col))
                if not stops:
                    stops = [(0, QColor(0, 0, 0)), (1, QColor(255, 255, 255))]
                paint = Paint("radial" if t == "radialGradient" else "linear", stops=stops)
                units = el.get("gradientUnits", "objectBoundingBox")
                if t == "linearGradient":
                    x1, y1 = float(el.get("x1", "0").strip("%")), float(el.get("y1", "0").strip("%"))
                    x2, y2 = float(el.get("x2", "100" if "%" in el.get("x2", "100%") else "1").strip("%")), float(el.get("y2", "0").strip("%"))
                    if any("%" in el.get(k, "") for k in ("x1", "y1", "x2", "y2")):
                        x1, y1, x2, y2 = x1 / 100, y1 / 100, x2 / 100, y2 / 100
                    paint.start, paint.end = (x1, y1), (x2, y2)
                    paint._user_space = units == "userSpaceOnUse"
                else:
                    cx, cy, r = float(el.get("cx", "0.5").strip("%")), float(el.get("cy", "0.5").strip("%")), float(el.get("r", "0.5").strip("%"))
                    if any("%" in el.get(k, "") for k in ("cx", "cy", "r")):
                        cx, cy, r = cx / 100, cy / 100, r / 100
                    paint.start, paint.end = (cx, cy), (cx + r, cy)
                    paint._user_space = units == "userSpaceOnUse"
                href = el.get(f"{{{XLINK}}}href") or el.get("href")
                if href and href.startswith("#") and href[1:] in self.gradients and not stops:
                    paint.stops = self.gradients[href[1:]].stops
                self.gradients[gid] = paint

    def paint_for(self, value: str | None, opacity: float, default: Paint, bbox: QRectF) -> Paint:
        if value is None:
            return default
        v = value.strip()
        if v.startswith("url("):
            gid = re.sub(r"url\(#?([^)]*)\)", r"\1", v).strip("'\" ")
            g = self.gradients.get(gid)
            if g is None:
                return Paint.solid(QColor(128, 128, 128))
            p = g.copy()
            if getattr(g, "_user_space", False) and bbox.width() > 0 and bbox.height() > 0:
                p.start = ((g.start[0] - bbox.left()) / bbox.width(), (g.start[1] - bbox.top()) / bbox.height())
                p.end = ((g.end[0] - bbox.left()) / bbox.width(), (g.end[1] - bbox.top()) / bbox.height())
            return p
        c = _parse_color(v, opacity)
        if c is None:
            return default
        if c.alpha() == 0 and v in ("none", "transparent"):
            return Paint.none()
        return Paint.solid(c)

    def style_for(self, el, inherited: dict, bbox: QRectF) -> Style:
        props = dict(inherited)
        props.update(_style_map(el))
        st = Style()
        fo = float(props.get("fill-opacity", 1))
        so = float(props.get("stroke-opacity", 1))
        st.fill = self.paint_for(props.get("fill"), fo, Paint.solid(QColor(0, 0, 0)), bbox)
        st.stroke = self.paint_for(props.get("stroke"), so, Paint.none(), bbox)
        st.stroke_width = _len(props.get("stroke-width", "1"))
        st.cap = {"butt": "Butt", "round": "Round", "square": "Square"}.get(props.get("stroke-linecap", "butt"), "Butt")
        st.join = {"miter": "Miter", "round": "Round", "bevel": "Bevel"}.get(props.get("stroke-linejoin", "miter"), "Miter")
        da = props.get("stroke-dasharray", "none")
        st.dash = [] if da in ("none", "") else _nums(da)
        st.opacity = float(props.get("opacity", 1))
        mb = props.get("mix-blend-mode")
        if mb:
            st.blend = {"multiply": "Multiply", "screen": "Screen", "overlay": "Overlay", "darken": "Darken", "lighten": "Lighten",
                        "color-dodge": "Color Dodge", "color-burn": "Color Burn", "hard-light": "Hard Light", "soft-light": "Soft Light",
                        "difference": "Difference", "exclusion": "Exclusion"}.get(mb, "Normal")
        return st, props

    def walk(self, el, inherited: dict, out: list[Item]):
        t = _tag(el)
        if t in ("defs", "style", "title", "desc", "metadata", "clipPath", "mask", "symbol", "linearGradient", "radialGradient", "pattern", "marker"):
            return
        if _style_map(el).get("display") == "none" or el.get("visibility") == "hidden":
            return
        xf = parse_transform(el.get("transform"))
        item: Item | None = None
        if t in ("g", "svg", "a", "switch"):
            children: list[Item] = []
            _, props = self.style_for(el, inherited, QRectF())
            for ch in el:
                self.walk(ch, props, children)
            if t == "svg" and el is not None and out is self.items_out:
                out.extend(children)
                return
            if not children:
                return
            g = GroupItem(children)
            g.transform = xf
            g.style.opacity = float(_style_map(el).get("opacity", 1))
            g.name = el.get("data-vb-layer") or el.get("id", "")
            g._svg_layer = el.get("data-vb-layer") is not None  # type: ignore[attr-defined]
            out.append(g)
            return
        if t == "path":
            p = parse_path_d(el.get("d", ""))
            item = PathItem.from_painter_path(p)
            if el.get("fill-rule", _style_map(el).get("fill-rule")) == "evenodd":
                pass
        elif t == "rect":
            x, y = _len(el.get("x", 0)), _len(el.get("y", 0))
            w, h = _len(el.get("width", 0)), _len(el.get("height", 0))
            rx = _len(el.get("rx", el.get("ry", 0)))
            item = RectItem(QRectF(x, y, w, h), rx)
        elif t == "circle":
            cx, cy, r = _len(el.get("cx", 0)), _len(el.get("cy", 0)), _len(el.get("r", 0))
            item = EllipseItem(QRectF(cx - r, cy - r, 2 * r, 2 * r))
        elif t == "ellipse":
            cx, cy, rx, ry = _len(el.get("cx", 0)), _len(el.get("cy", 0)), _len(el.get("rx", 0)), _len(el.get("ry", 0))
            item = EllipseItem(QRectF(cx - rx, cy - ry, 2 * rx, 2 * ry))
        elif t == "line":
            item = PathItem.from_points([QPointF(_len(el.get("x1", 0)), _len(el.get("y1", 0))), QPointF(_len(el.get("x2", 0)), _len(el.get("y2", 0)))])
        elif t in ("polyline", "polygon"):
            v = _nums(el.get("points", ""))
            pts = [QPointF(v[i], v[i + 1]) for i in range(0, len(v) - 1, 2)]
            if len(pts) >= 2:
                item = PathItem.from_points(pts, closed=(t == "polygon"))
        elif t == "text":
            item = self.text(el)
        elif t == "image":
            href = el.get(f"{{{XLINK}}}href") or el.get("href") or ""
            img = QImage()
            if href.startswith("data:"):
                try:
                    data = base64.b64decode(href.split(",", 1)[1])
                    img.loadFromData(QByteArray(data))
                except Exception:
                    pass
            if not img.isNull():
                x, y = _len(el.get("x", 0)), _len(el.get("y", 0))
                w, h = _len(el.get("width", img.width())), _len(el.get("height", img.height()))
                item = ImageItem(img, QRectF(x, y, w, h))
        if item is None:
            return
        item.transform = xf
        if not isinstance(item, ImageItem):
            st, _ = self.style_for(el, inherited, item.local_bbox())
            if isinstance(item, TextItem):
                if st.fill.is_none() and st.stroke.is_none():
                    st.fill = Paint.solid(QColor(0, 0, 0))
            item.style = st
        else:
            item.style.opacity = float(_style_map(el).get("opacity", 1))
        item.name = el.get("id", "")
        out.append(item)

    def text(self, el) -> TextItem | None:
        props = _style_map(el)
        x = _len(el.get("x", "0").split()[0]) if el.get("x") else 0.0
        y = _len(el.get("y", "0").split()[0]) if el.get("y") else 0.0
        lines = []
        first_xy = None
        if el.text and el.text.strip():
            lines.append(el.text.strip())
        for ch in el:
            if _tag(ch) == "tspan":
                txt = "".join(ch.itertext()).strip()
                if ch.get("x") is not None and first_xy is None:
                    first_xy = (_len(ch.get("x", "0").split()[0]), _len(ch.get("y", str(y)).split()[0]))
                if txt:
                    lines.append(txt)
                if ch.tail and ch.tail.strip():
                    lines.append(ch.tail.strip())
        if not lines:
            whole = "".join(el.itertext()).strip()
            if not whole:
                return None
            lines = [whole]
        if first_xy and not (el.get("x") or el.get("y")):
            x, y = first_xy
        t = TextItem("\n".join(lines), QPointF(x, y))
        t.family = props.get("font-family", el.get("font-family", "Arial")).split(",")[0].strip("'\" ")
        t.size = _len(props.get("font-size", el.get("font-size", "16")))
        t.bold = props.get("font-weight", el.get("font-weight", "")) in ("bold", "700", "800", "900", "bolder")
        t.italic = props.get("font-style", el.get("font-style", "")) == "italic"
        t.align = {"middle": "Center", "end": "Right"}.get(props.get("text-anchor", el.get("text-anchor", "start")), "Left")
        return t


def _style_map(el) -> dict:
    props = {}
    for k in ("fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "opacity",
              "fill-opacity", "stroke-opacity", "font-family", "font-size", "font-weight", "font-style", "text-anchor",
              "display", "mix-blend-mode", "stop-color", "stop-opacity", "offset"):
        if el.get(k) is not None:
            props[k] = el.get(k)
    style = el.get("style")
    if style:
        for part in style.split(";"):
            if ":" in part:
                k, v = part.split(":", 1)
                props[k.strip()] = v.strip()
    return props


def _len(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    m = _NUM.match(s)
    if not m:
        return 0.0
    val = float(m.group())
    unit = s[m.end():].strip().lower()
    return val * {"px": 1, "pt": 96 / 72, "pc": 16, "mm": 96 / 25.4, "cm": 96 / 2.54, "in": 96, "": 1, "%": 1}.get(unit, 1)


def import_svg(text: str) -> tuple[list[Item], float, float]:
    """Returns (items, width, height). Items are in viewBox coordinates scaled to width/height."""
    root = ET.fromstring(text)
    imp = _Importer()
    imp.collect_defs(root)
    vb = _nums(root.get("viewBox", ""))
    w = _len(root.get("width")) if root.get("width") else (vb[2] if len(vb) == 4 else 800.0)
    h = _len(root.get("height")) if root.get("height") else (vb[3] if len(vb) == 4 else 600.0)
    items: list[Item] = []
    _, props = imp.style_for(root, {}, QRectF())
    for ch in root:
        imp.walk(ch, props, items)
    if len(vb) == 4 and (vb[0] or vb[1] or abs(vb[2] - w) > 1e-6 or abs(vb[3] - h) > 1e-6):
        sx, sy = (w / vb[2]) if vb[2] else 1, (h / vb[3]) if vb[3] else 1
        fix = QTransform.fromScale(sx, sy) * QTransform.fromTranslate(0, 0)
        fix = QTransform.fromTranslate(-vb[0], -vb[1]) * QTransform.fromScale(sx, sy)
        for it in items:
            it.transform = it.transform * fix
    return items, w, h


def document_from_svg(text: str) -> Document:
    """Top-level untransformed groups (how Illustrator and VectorBench write layers) become layers."""
    items, w, h = import_svg(text)
    doc = Document(w, h)
    layers: list[Layer] = []
    loose: list[Item] = []
    for it in items:
        if isinstance(it, GroupItem) and it.transform.isIdentity() and it.style.opacity >= 1.0 and \
                (getattr(it, "_svg_layer", False) or len(items) <= 12):
            if loose:
                l = Layer(f"Layer {len(layers) + 1}"); l.items = loose; layers.append(l); loose = []
            l = Layer(it.name or f"Layer {len(layers) + 1}")
            l.items = it.children
            layers.append(l)
        else:
            loose.append(it)
    if loose or not layers:
        l = Layer(f"Layer {len(layers) + 1}"); l.items = loose; layers.append(l)
    doc.layers = layers
    doc.active_layer_index = len(layers) - 1
    return doc
