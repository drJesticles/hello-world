"""UI smoke tests driven through synthetic mouse events on an offscreen Qt platform."""
import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import QApplication

from pixelbench import filters
from pixelbench.mainwindow import MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app):
    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    app.processEvents()
    yield w
    for i in range(w.tabs.count()):
        w.tabs.widget(i).doc.dirty = False
    w.close()


def _ev(view, t, pos, btn=Qt.MouseButton.LeftButton, mods=Qt.KeyboardModifier.NoModifier):
    vp = view.doc_to_view(QPointF(*pos))
    buttons = Qt.MouseButton.NoButton if t == QEvent.Type.MouseButtonRelease else btn
    return QMouseEvent(t, vp, vp, btn, buttons, mods)


def drag(win, tool, pts, mods=Qt.KeyboardModifier.NoModifier):
    view = win.current_view()
    win.ctx.set_tool(tool)
    view.mousePressEvent(_ev(view, QEvent.Type.MouseButtonPress, pts[0], mods=mods))
    for p in pts[1:]:
        view.mouseMoveEvent(_ev(view, QEvent.Type.MouseMove, p, mods=mods))
    view.mouseReleaseEvent(_ev(view, QEvent.Type.MouseButtonRelease, pts[-1], mods=mods))
    QApplication.processEvents()


def test_brush_eraser_undo(win):
    view = win.new_document(200, 200, "White")
    doc = view.doc
    win.ctx.set_fg(QColor(0, 0, 255))
    drag(win, "brush", [(20, 100), (180, 100)])
    assert tuple(doc.layers[0].pixels[100, 100, :3]) == (0, 0, 255)
    assert tuple(doc.layers[0].pixels[20, 20, :3]) == (255, 255, 255)
    drag(win, "eraser", [(100, 90), (100, 110)])
    assert doc.layers[0].pixels[100, 100, 3] < 255
    win.edit_undo(); win.edit_undo()
    assert tuple(doc.layers[0].pixels[100, 100]) == (255, 255, 255, 255)
    assert doc.dirty


def test_selection_limits_fill_and_filters(win):
    view = win.new_document(200, 200, "White")
    doc = view.doc
    drag(win, "marquee", [(0, 0), (100, 100)])
    assert doc.selection is not None and doc.selection[50, 50] == 255 and doc.selection[150, 150] == 0
    win.ctx.set_fg(QColor(255, 0, 0))
    drag(win, "fill", [(50, 50)])
    assert tuple(doc.layers[0].pixels[50, 50, :3]) == (255, 0, 0)
    assert tuple(doc.layers[0].pixels[150, 150, :3]) == (255, 255, 255)
    win.run_filter("Invert", filters.invert, [], {})
    assert tuple(doc.layers[0].pixels[50, 50, :3]) == (0, 255, 255)
    assert tuple(doc.layers[0].pixels[150, 150, :3]) == (255, 255, 255)
    win.select_invert()
    assert doc.selection[150, 150] == 255
    win.select_none()
    assert doc.selection is None


def test_wand_shape_text_move_crop(win):
    view = win.new_document(300, 200, "White")
    doc = view.doc
    drag(win, "shape", [(10, 10), (60, 60)])  # new layer with a rectangle
    assert len(doc.layers) == 2 and doc.layers[1].pixels[30, 30, 3] == 255
    drag(win, "move", [(30, 30), (80, 30)])
    assert doc.layers[1].pixels[30, 80, 3] == 255 and doc.layers[1].pixels[30, 20, 3] == 0
    win.ctx.tools["text"].render_text(view, "Hi", QPointF(100, 100))
    assert len(doc.layers) == 3 and doc.layers[2].pixels[..., 3].sum() > 0
    drag(win, "wand", [(290, 190)])
    assert doc.selection is not None and doc.selection[190, 290] == 255 and doc.selection[30, 80] == 0
    win.select_none()
    drag(win, "crop", [(50, 50), (250, 150)])
    win.ctx.tools["crop"].apply(view)
    assert (doc.width, doc.height) == (200, 100)
    win.edit_undo()
    assert (doc.width, doc.height) == (300, 200)


def test_layers_panel_and_clipboard(win):
    view = win.new_document(100, 100, "White")
    doc = view.doc
    win.layer_new(); win.layer_duplicate()
    assert len(doc.layers) == 3 and win.layers_panel.list.count() == 3
    win.layers_panel.list.setCurrentRow(2)  # bottom row = layer 0
    assert doc.active_index == 0
    win.select_all(); win.edit_copy(); win.edit_paste()
    assert len(doc.layers) == 4 and doc.layers[doc.active_index].name == "Pasted"
    win.layer_merge_down(); win.layer_flatten()
    assert len(doc.layers) == 1
    u, r = view.history.labels()
    assert u[-1] == "Flatten"
    win.history_panel._jump(win.history_panel.item(1))
    assert len(doc.layers) == 2


def test_transform_dialog_compute(win):
    from pixelbench.dialogs import TransformDialog
    view = win.new_document(100, 100, "Transparent")
    doc = view.doc
    doc.layers[0].pixels[40:60, 40:60] = (0, 255, 0, 255)
    dlg = TransformDialog(doc, doc.layers[0], win)
    dlg.scale_w.setValue(200)
    out = dlg.compute()
    assert out[31, 31, 3] == 255 and out[20, 20, 3] == 0
    dlg.reject()
    assert doc.layers[0].pixels[31, 31, 3] == 0
