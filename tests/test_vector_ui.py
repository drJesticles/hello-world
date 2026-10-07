"""UI smoke tests for VectorBench, driven with synthetic mouse events on the offscreen platform."""
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication

from vectorbench.mainwindow import MainWindow
from vectorbench.model import Paint, PathItem, RectItem, TextItem


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app):
    w = MainWindow(); w.resize(1300, 900); w.show(); app.processEvents()
    yield w
    for i in range(w.tabs.count()):
        w.tabs.widget(i).doc.dirty = False
    w.close()


def _ev(view, t, pos, mods=Qt.KeyboardModifier.NoModifier):
    vp = view.doc_to_view(QPointF(*pos)); b = Qt.MouseButton.LeftButton
    return QMouseEvent(t, vp, vp, b, Qt.MouseButton.NoButton if t == QEvent.Type.MouseButtonRelease else b, mods)


def drag(win, tool, pts, mods=Qt.KeyboardModifier.NoModifier):
    view = win.current_view(); win.ctx.set_tool(tool)
    view.mousePressEvent(_ev(view, QEvent.Type.MouseButtonPress, pts[0], mods))
    for p in pts[1:]:
        view.mouseMoveEvent(_ev(view, QEvent.Type.MouseMove, p, mods))
    view.mouseReleaseEvent(_ev(view, QEvent.Type.MouseButtonRelease, pts[-1], mods))
    QApplication.processEvents()


def test_shapes_select_move_scale_undo(win):
    view = win.new_document(800, 600); doc = view.doc
    drag(win, "rect", [(50, 50), (250, 150)])
    drag(win, "ellipse", [(300, 50), (400, 200)], Qt.KeyboardModifier.ShiftModifier)
    assert [i.kind for i in doc.layers[0].items] == ["rect", "ellipse"]
    e = doc.layers[0].items[1]; assert abs(e.bbox().width() - e.bbox().height()) < 0.01
    drag(win, "select", [(100, 100), (130, 120)])
    assert abs(doc.layers[0].items[0].bbox().x() - 80) < 1
    view.set_selection([doc.layers[0].items[0]])
    se = view.view_to_doc(view.handle_rects()["se"].center())
    drag(win, "select", [(se.x(), se.y()), (380, 300)])
    assert abs(doc.layers[0].items[0].bbox().right() - 380) < 1
    win.edit_undo(); assert abs(doc.layers[0].items[0].bbox().right() - 280) < 1
    drag(win, "select", [(0, 0), (500, 400)])
    assert len(view.selection) == 2
    assert doc.dirty


def test_pen_direct_selection_and_anchor_tools(win):
    view = win.new_document(800, 600); doc = view.doc
    drag(win, "pen", [(100, 300)]); drag(win, "pen", [(200, 200), (230, 180)]); drag(win, "pen", [(300, 320)]); drag(win, "pen", [(100, 300)])
    p = doc.layers[0].items[-1]
    assert isinstance(p, PathItem) and p.subpaths[0].closed and len(p.subpaths[0].nodes) == 3 and p.subpaths[0].nodes[1].smooth
    view.set_selection([p])
    drag(win, "direct", [(100, 300), (80, 330)])
    assert abs(p.subpaths[0].nodes[0].p.x() - 80) < 1
    drag(win, "add_anchor", [(290, 310)])
    p = doc.find(p.id); assert len(p.subpaths[0].nodes) == 4
    drag(win, "convert", [(80, 330)])
    assert p.subpaths[0].nodes[0].smooth
    drag(win, "del_anchor", [(80, 330)])
    assert len(doc.find(p.id).subpaths[0].nodes) == 3


def test_type_tool_inline_editing(win):
    view = win.new_document(800, 600); doc = view.doc
    drag(win, "type", [(60, 200)])
    assert view.is_editing_text()
    view._editor.insertPlainText("Hello")
    view.end_text_edit()
    t = doc.layers[0].items[-1]
    assert isinstance(t, TextItem) and t.text.endswith("Hello") and t.bbox().width() > 10
    view.set_selection([t]); win.expand()
    assert isinstance(view.selection[0], PathItem)


def test_object_commands_and_clipboard(win):
    view = win.new_document(800, 600); doc = view.doc
    drag(win, "rect", [(50, 50), (150, 150)]); drag(win, "ellipse", [(100, 100), (200, 200)])
    win.select_all(); win.group(); assert doc.layers[0].items[0].kind == "group"
    win.ungroup(); assert len(doc.layers[0].items) == 2
    win.select_all(); win.pathfinder("unite"); assert len(doc.layers[0].items) == 1 and isinstance(doc.layers[0].items[0], PathItem)
    win.edit_undo(); assert len(doc.layers[0].items) == 2
    win.select_all(); win.edit_copy(); win.edit_paste(); assert len(doc.layers[0].items) == 4
    win.edit_paste(in_place=True, front=True); assert len(doc.layers[0].items) == 6
    win.select_all(); win.align("left"); assert len({round(i.bbox().left(), 2) for i in doc.layers[0].items}) == 1
    win.edit_delete(); assert len(doc.layers[0].items) == 0


def test_properties_panel_edits_selection(win):
    view = win.new_document(800, 600); doc = view.doc
    drag(win, "rect", [(50, 50), (150, 150)])
    win.properties.stroke_w.setValue(9)
    win.properties.fill_btn.set_paint(Paint.solid(QColor("#ff0000"))); win.properties.fill_btn.changed.emit(win.properties.fill_btn.paint)
    r = doc.layers[0].items[0]
    assert r.style.stroke_width == 9 and r.style.fill.color == QColor("#ff0000")
    win.properties.w.setValue(200); win.properties._apply_geometry()
    assert abs(r.bbox().width() - 200) < 0.01 and abs(r.bbox().height() - 200) < 0.01  # constrained
    view.clear_selection()
    win.properties.stroke_w.setValue(3)
    assert win.ctx.style.stroke_width == 3 and r.style.stroke_width == 9


def test_layers_panel_reflects_document(win):
    view = win.new_document(800, 600); doc = view.doc
    drag(win, "rect", [(50, 50), (150, 150)]); win.layer_new(); drag(win, "ellipse", [(50, 50), (150, 150)])
    assert len(doc.layers) == 2 and doc.layers[1].items[0].kind == "ellipse"
    assert win.layers_panel.tree.topLevelItemCount() == 2
    win.layers_panel.tree.topLevelItem(0).child(0).setCheckState(1, Qt.CheckState.Unchecked)
    assert not doc.layers[1].items[0].visible
