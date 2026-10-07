import os

import pytest
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QTransform
from PySide6.QtWidgets import QApplication

from vectorbench import fileio, pathops, svg
from vectorbench.geometry import arc_to_cubics, rdp, split_cubic
from vectorbench.history import History
from vectorbench.model import (Document, EllipseItem, GroupItem, ImageItem, Paint, PathItem, PolygonItem, RectItem,
                               TextItem, item_from_dict)


@pytest.fixture(scope="module", autouse=True)
def app():
    return QApplication.instance() or QApplication([])


def make_doc():
    d = Document(400, 300)
    r = RectItem(QRectF(10, 10, 100, 50), 8)
    r.style.fill = Paint("linear", stops=[(0, QColor("red")), (1, QColor("blue"))])
    d.add_item(r)
    e = EllipseItem(QRectF(60, 30, 100, 100)); e.style.stroke_width = 4; e.style.dash = [8, 4]
    d.add_item(e)
    t = TextItem("Hi there", QPointF(20, 200), "DejaVu Sans", 30); t.bold = True
    d.add_item(t)
    return d


def test_serialization_roundtrip():
    d = make_doc()
    g = GroupItem([PolygonItem(QPointF(50, 50), 20, 5, True)])
    d.add_item(g)
    snap = d.to_dict()
    d2 = Document.from_dict(snap)
    assert d2.to_dict() == snap
    assert [i.kind for i in d2.all_items()] == ["rect", "ellipse", "text", "group"]


def test_copy_gets_new_ids():
    d = make_doc()
    c = d.layers[0].items[0].copy()
    assert c.id != d.layers[0].items[0].id and c.to_dict()["rect"] == d.layers[0].items[0].to_dict()["rect"]


def test_path_from_painter_path_and_back():
    r = RectItem(QRectF(0, 0, 10, 10))
    p = PathItem.from_painter_path(r.local_path())
    assert len(p.subpaths) == 1 and p.subpaths[0].closed and len(p.subpaths[0].nodes) == 4
    smooth = PathItem.from_points([QPointF(0, 0), QPointF(10, 0), QPointF(10, 10)], closed=True, smooth=True)
    again = PathItem.from_painter_path(smooth.local_path())
    assert again.subpaths[0].closed and all(n.smooth for n in again.subpaths[0].nodes)


def test_hit_testing_and_z_order():
    d = make_doc()
    assert d.item_at(QPointF(20, 20), 2).kind == "rect"
    assert d.item_at(QPointF(100, 100), 2).kind == "ellipse"   # ellipse is above the rect where they overlap
    assert d.item_at(QPointF(300, 290), 2) is None
    assert d.z_order(d.layers[0].items[1]) == (0, 1)


def test_text_bbox_and_outline():
    t = TextItem("Hello", QPointF(0, 50), "DejaVu Sans", 40)
    b = t.bbox()
    assert b.width() > 50 and b.top() < 50 < b.bottom() + 1
    p = t.to_path_item()
    assert isinstance(p, PathItem) and len(p.subpaths) >= 5


def test_transform_about_and_bbox():
    r = RectItem(QRectF(0, 0, 10, 10))
    t = QTransform(); t.rotate(90)
    pathops.transform_about([r], t, QPointF(5, 5))
    b = r.bbox()
    assert abs(b.x()) < 1e-6 and abs(b.y()) < 1e-6 and abs(b.width() - 10) < 1e-6


def test_history_undo_redo():
    d = make_doc(); h = History(d)
    h.push("delete"); d.layers[0].items.clear(); d.changed()
    assert h.undo() == "delete" and len(d.layers[0].items) == 3
    assert h.redo() == "delete" and len(d.layers[0].items) == 0
    h.begin("drag"); d.layers[0].items.append(RectItem()); h.cancel()
    assert len(d.layers[0].items) == 0 and not h.can_redo()


def test_pathfinder_and_path_ops():
    d = make_doc(); r, e = d.layers[0].items[0], d.layers[0].items[1]
    u = pathops.pathfinder([r, e], "unite"); assert u.bbox() == r.bbox().united(e.bbox())
    m = pathops.pathfinder([r, e], "minus_front"); assert m.bbox().width() <= r.bbox().width()
    i = pathops.pathfinder([r, e], "intersect"); assert i.bbox().width() < r.bbox().width()
    x = pathops.pathfinder([r, e], "exclude"); assert len(x.subpaths) >= 2
    o = pathops.outline_stroke(e); assert o.style.fill.kind == "solid" and o.style.stroke.is_none()
    off = pathops.offset_path(r, 5); assert abs(off.bbox().x() - 5) < 0.5
    neg = pathops.offset_path(r, -5); assert neg.bbox().width() < r.bbox().width()
    p = PathItem.from_points([QPointF(0, 0), QPointF(100, 0), QPointF(100, 100)])
    assert len(pathops.add_anchor_points(p).subpaths[0].nodes) == 5
    rev = pathops.reverse_path(p); assert rev.subpaths[0].nodes[0].p == QPointF(100, 100)
    j = pathops.join_paths(p, PathItem.from_points([QPointF(100, 100), QPointF(0, 100)]))
    assert len(j.subpaths[0].nodes) == 5
    closed = pathops.join_paths(p, None); assert closed.subpaths[0].closed


def test_align_distribute():
    d = make_doc(); items = d.layers[0].items
    pathops.align(items, "left"); assert len({round(i.bbox().left(), 3) for i in items}) == 1
    pathops.align(items, "vcenter"); assert len({round(i.bbox().center().y(), 3) for i in items}) == 1
    pathops.align(items, "right", QRectF(0, 0, 400, 300)); assert all(abs(i.bbox().right() - 400) < 1e-6 for i in items)
    a, b, c = RectItem(QRectF(0, 0, 10, 10)), RectItem(QRectF(100, 0, 10, 10)), RectItem(QRectF(30, 0, 10, 10))
    pathops.distribute([a, b, c], "hspace")
    xs = sorted(i.bbox().left() for i in (a, b, c)); assert abs((xs[1] - xs[0]) - (xs[2] - xs[1])) < 1e-6


def test_geometry_helpers():
    segs = arc_to_cubics(0, 0, 10, 10, 0, False, True, 10, 10)
    assert 1 <= len(segs) <= 2 and abs(segs[-1][2].x() - 10) < 1e-6
    pts = [QPointF(i, (i % 2) * 0.1) for i in range(20)]
    assert len(rdp(pts, 0.5)) == 2
    (a, b, c, m), (m2, e, f, g) = split_cubic(QPointF(0, 0), QPointF(0, 10), QPointF(10, 10), QPointF(10, 0))
    assert m == m2 and abs(m.x() - 5) < 1e-6


def test_svg_roundtrip_and_import():
    d = make_doc()
    text = svg.export_svg(d)
    assert "<rect" in text and "linearGradient" in text and "<text" in text and "stroke-dasharray" in text
    d2 = svg.document_from_svg(text)
    kinds = [i.kind for i in d2.all_items()]
    assert kinds == ["rect", "ellipse", "text"] and (d2.width, d2.height) == (400, 300)
    assert d2.layers[0].items[0].style.fill.kind == "linear" and d2.layers[0].items[1].style.dash == [8.0, 4.0]
    assert d2.layers[0].items[2].text == "Hi there" and d2.layers[0].items[2].bold
    ext = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100"><g id="Layer_1">'
           '<g transform="translate(10,10) scale(2)"><path d="M0 0 h50 v30 a10 10 0 0 1 -10 10 H10 q-5 0 -5 -5 Z" style="fill:#0f0;stroke:rgb(0,0,255);stroke-width:2"/>'
           '<circle cx="100" cy="50" r="20" fill="none" stroke="#f00"/><polyline points="0,90 20,70 40,90" stroke="black" fill="none"/></g>'
           '<text x="5" y="95" font-size="12" text-anchor="middle">A</text></g></svg>')
    d3 = svg.document_from_svg(ext)
    assert d3.layers[0].name == "Layer_1"
    top = d3.layers[0].items
    assert [i.kind for i in top] == ["group", "text"] and top[1].align == "Center"
    inner = top[0].children
    assert [i.kind for i in inner] == ["path", "ellipse", "path"]
    assert inner[0].style.fill.color == QColor("#00ff00") and inner[0].style.stroke.color == QColor(0, 0, 255)
    in_doc = top[0].transform.map(inner[1].bbox().topLeft())  # children are group-relative
    assert abs(in_doc.x() - (10 + 2 * 80)) < 1e-6 and abs(in_doc.y() - (10 + 2 * 30)) < 1e-6


def test_fileio_all_formats(tmp_path):
    d = make_doc()
    p = str(tmp_path / "t.vbx"); fileio.save_native(d, p)
    d2 = fileio.open_document(p); assert d2.to_dict()["layers"] == d.to_dict()["layers"] and not d2.dirty
    for ext in ("svg", "pdf", "png", "jpg", "webp"):
        out = str(tmp_path / f"t.{ext}"); fileio.export(d, out, scale=0.5); assert os.path.getsize(out) > 0
    with open(str(tmp_path / "t.pdf"), "rb") as f:
        assert f.read(4) == b"%PDF"
    d3 = fileio.open_document(str(tmp_path / "t.svg")); assert len(list(d3.all_items())) == 3
    d4 = fileio.open_document(str(tmp_path / "t.png")); assert isinstance(d4.layers[0].items[0], ImageItem)
    img_item = d4.layers[0].items[0]
    again = item_from_dict(img_item.to_dict()); assert again.image.size() == img_item.image.size()
